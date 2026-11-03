"""Tests hors-ligne de POST /transcribe (transport brut, validation WAV, limites).

Transport entrant : corps HTTP brut audio/wav lu en flux via Request.stream(),
sans parsing multipart (aucun fichier temporaire disque). Aucun appel à Meta
Model API : le client est simulé et les WAV sont générés en mémoire.
Exécution depuis la racine du projet :
    .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

import io
import json
import struct
import unittest
import wave
from unittest.mock import patch

import httpx2
from fastapi import HTTPException
from openai import APITimeoutError, AuthenticationError
from starlette.requests import Request

from main import (
    ASR_AUDIO_ENCODING,
    ASR_MODE,
    ASR_MODEL_NAME,
    META_ASR_URL,
    TranscribeResponse,
    describe_wav,
    transcribe,
)


def make_wav(channels=1, width=2, rate=24000, seconds=1.0):
    """Génère un WAV valide en mémoire."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00" * int(rate * seconds) * channels * width)
    return buf.getvalue()


def wav_header_only(channels=1, width=2, rate=24000, nframes=24000):
    """En-tête WAV seul, avec un nombre de trames arbitraire (sans les données)."""
    data_len = nframes * channels * width
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_len,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        rate,
        rate * channels * width,
        channels * width,
        width * 8,
        b"data",
        data_len,
    )


def make_request(data, content_type="audio/wav", chunk_size=65536, length="auto", counter=None):
    """Construit une requête brute rejouant le corps par morceaux.

    length="auto" déclare la vraie taille, None omet l'en-tête (flux sans
    taille annoncée), toute autre valeur force un Content-Length explicite.
    counter (liste d'un entier) compte les lectures du flux si fourni.
    """
    headers = [(b"content-type", content_type.encode("latin-1"))]
    if length == "auto":
        headers.append((b"content-length", str(len(data)).encode("latin-1")))
    elif length is not None:
        headers.append((b"content-length", str(length).encode("latin-1")))
    scope = {"type": "http", "method": "POST", "path": "/transcribe", "headers": headers}
    chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]

    async def receive():
        if counter is not None:
            counter[0] += 1
        if chunks:
            body = chunks.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def run_coro(coro):
    """Exécute une coroutine pure sans boucle d'événements (aucune I/O réelle).

    Suffit ici : le flux rejoué et l'appel Meta simulé n'attendent jamais
    de primitive liée à une boucle. Toute autre attente échoue explicitement.
    """
    try:
        yielded = coro.send(None)
    except StopIteration as done:
        return done.value
    coro.close()
    raise AssertionError(f"await non supporté hors boucle : {yielded!r}")


class FakeClient:
    """Simule le client Meta : enregistre l'appel, rejoue un résultat ou une erreur."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


class DescribeWavTest(unittest.TestCase):
    def test_valid_24k_mono(self):
        channels, width, rate, seconds = describe_wav(make_wav())
        self.assertEqual((channels, width, rate), (1, 2, 24000))
        self.assertAlmostEqual(seconds, 1.0, places=3)

    def test_valid_16k_mono(self):
        channels, width, rate, seconds = describe_wav(make_wav(rate=16000, seconds=2.0))
        self.assertEqual((channels, width, rate), (1, 2, 16000))
        self.assertAlmostEqual(seconds, 2.0, places=3)

    def test_rejects_stereo(self):
        with self.assertRaises(ValueError):
            describe_wav(make_wav(channels=2))

    def test_rejects_8bit(self):
        with self.assertRaises(ValueError):
            describe_wav(make_wav(width=1))

    def test_rejects_44100hz(self):
        with self.assertRaises(ValueError):
            describe_wav(make_wav(rate=44100))

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            describe_wav(b"ceci n'est pas un WAV")
        with self.assertRaises(ValueError):
            describe_wav(b"")

    def test_rejects_overlong(self):
        # 601 s annoncées dans l'en-tête, sans générer 29 Mo de données.
        with self.assertRaises(ValueError):
            describe_wav(wav_header_only(nframes=601 * 24000))

    def test_rejects_zero_frames(self):
        with self.assertRaises(ValueError):
            describe_wav(make_wav(seconds=0.0))


class TranscribeEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Exécute l'appel Meta simulé sans thread : comportement déterministe,
        # sans boucle d'événements. La logique testée (transport, validation,
        # mapping d'erreurs) est inchangée ; asyncio.to_thread reste du ressort du socle.
        async def direct_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        cls._to_thread_patch = patch("asyncio.to_thread", new=direct_to_thread)
        cls._to_thread_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._to_thread_patch.stop()
        super().tearDownClass()

    def assertHttpStatus(self, status, func, *args, **kwargs):
        with self.assertRaises(HTTPException) as ctx:
            run_coro(func(*args, **kwargs))
        self.assertEqual(ctx.exception.status_code, status)

    def test_rejects_bad_mime(self):
        self.assertHttpStatus(400, transcribe, make_request(make_wav(), "text/plain"))

    def test_rejects_formdata(self):
        # L'ancien transport FormData n'est plus accepté : corps brut exigé.
        request = make_request(make_wav(), "multipart/form-data; boundary=xyz")
        self.assertHttpStatus(400, transcribe, request)

    def test_rejects_empty_file(self):
        self.assertHttpStatus(400, transcribe, make_request(b""))

    def test_rejects_oversize_declared(self):
        counter = [0]
        with patch("main.MAX_AUDIO_BYTES", 100):
            request = make_request(make_wav(), counter=counter)
            self.assertHttpStatus(413, transcribe, request)
        # Le Content-Length suffit : rien n'est lu.
        self.assertEqual(counter[0], 0)

    def test_streaming_stops_at_limit(self):
        counter = [0]
        with patch("main.MAX_AUDIO_BYTES", 100):
            # 5 morceaux de 64 o sans taille annoncée : 64 puis 128 > 100.
            request = make_request(b"\x00" * 320, chunk_size=64, length=None, counter=counter)
            self.assertHttpStatus(413, transcribe, request)
        self.assertEqual(counter[0], 2)

    def test_bad_content_length_falls_back_to_stream(self):
        fake = FakeClient(result={"transcript": "bonjour"})
        with patch("main.get_client", return_value=fake):
            response = run_coro(transcribe(make_request(make_wav(), length="xyz")))
        self.assertEqual(response.transcript, "bonjour")

    def test_rejects_invalid_wav(self):
        self.assertHttpStatus(400, transcribe, make_request(b"RIFF" + b"\x00" * 100))

    def test_success_sends_exact_multipart(self):
        fake = FakeClient(
            result={"sessionId": "abc", "transcript": "  Bonjour ReFlow  ", "turns": []}
        )
        with patch("main.get_client", return_value=fake):
            response = run_coro(transcribe(make_request(make_wav())))
        self.assertIsInstance(response, TranscribeResponse)
        self.assertEqual(response.transcript, "Bonjour ReFlow")
        self.assertEqual(len(fake.calls), 1)
        url, kwargs = fake.calls[0]
        self.assertEqual(url, META_ASR_URL)
        self.assertEqual(kwargs["cast_to"], dict)
        self.assertEqual(
            kwargs["options"]["headers"], {"Content-Type": "multipart/form-data"}
        )
        files = kwargs["files"]
        self.assertEqual([name for name, _ in files], ["request", "audio"])
        request_filename, request_body, request_type = files[0][1]
        self.assertIsNone(request_filename)
        self.assertEqual(request_type, "application/json")
        self.assertEqual(
            json.loads(request_body),
            {"mode": ASR_MODE, "model": ASR_MODEL_NAME, "audioEncoding": ASR_AUDIO_ENCODING},
        )
        audio_filename, audio_body, audio_type = files[1][1]
        self.assertEqual(audio_filename, "recording.wav")
        self.assertEqual(audio_type, "audio/wav")
        self.assertTrue(audio_body.startswith(b"RIFF"))

    def test_empty_transcript_is_502(self):
        fake = FakeClient(result={"transcript": "   "})
        with patch("main.get_client", return_value=fake):
            self.assertHttpStatus(502, transcribe, make_request(make_wav()))

    def test_missing_transcript_is_502(self):
        fake = FakeClient(result={"sessionId": "abc"})
        with patch("main.get_client", return_value=fake):
            self.assertHttpStatus(502, transcribe, make_request(make_wav()))

    def test_non_dict_result_is_502(self):
        fake = FakeClient(result=["nope"])
        with patch("main.get_client", return_value=fake):
            self.assertHttpStatus(502, transcribe, make_request(make_wav()))

    def test_auth_error_maps_401(self):
        request = httpx2.Request("POST", META_ASR_URL)
        error = AuthenticationError(
            "clé invalide", response=httpx2.Response(401, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            self.assertHttpStatus(401, transcribe, make_request(make_wav()))

    def test_timeout_maps_504(self):
        error = APITimeoutError(request=httpx2.Request("POST", META_ASR_URL))
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            self.assertHttpStatus(504, transcribe, make_request(make_wav()))
