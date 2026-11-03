"""Tests ciblés de POST /chat (contrat, historique, validations, erreurs).

Aucun appel à Meta Model API : le client est entièrement simulé.
Les statuts HTTP sont vérifiés via HTTPException.status_code (erreurs) et le
modèle de réponse retourné (succès, sérialisé en 200 par FastAPI).
Exécution depuis la racine du projet :
    .\\.venv\\Scripts\\python.exe -m unittest tests.test_chat -v
"""

import base64
import unittest
from unittest.mock import patch

import httpx2
from fastapi import HTTPException
from openai import APIStatusError, APITimeoutError, AuthenticationError
from pydantic import ValidationError

from main import (
    CHAT_IMAGE_ONLY_TEXT,
    MAX_CHAT_IMAGE_BYTES,
    MODEL_NAME,
    ChatRequest,
    ChatResponse,
    build_chat_input,
    create_chat,
    format_chat_history,
)


class FakeResponse:
    """Réponse Meta simulée : seuls les attributs fournis existent."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)


class FakeResponses:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, result=None, error=None):
        self.responses = FakeResponses(result=result, error=error)


def make_messages(count, prefix="marqueur"):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "text": f"{prefix}-{i:02d}"}
        for i in range(count)
    ]


class FormatHistoryTest(unittest.TestCase):
    def test_roles_rendered(self):
        self.assertEqual(
            format_chat_history([("user", "Bonjour"), ("assistant", "Salut !")]),
            "Utilisateur: Bonjour\nAssistant: Salut !",
        )

    def test_empty_history(self):
        self.assertEqual(format_chat_history([]), "")


class ChatValidationTest(unittest.TestCase):
    def test_invalid_role_rejected(self):
        with self.assertRaises(ValidationError):
            ChatRequest(messages=[{"role": "system", "text": "Bonjour"}])

    def test_empty_text_without_image_is_422(self):
        # Texte vide autorisé par le schéma (cas image seule) : rejeté
        # au niveau endpoint quand aucune image ne l'accompagne.
        body = ChatRequest(messages=[{"role": "user", "text": ""}])
        with self.assertRaises(HTTPException) as ctx:
            create_chat(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_too_long_text_rejected(self):
        with self.assertRaises(ValidationError):
            ChatRequest(messages=[{"role": "user", "text": "x" * 2001}])

    def test_boundary_text_accepted(self):
        request = ChatRequest(messages=[{"role": "user", "text": "x" * 2000}])
        self.assertEqual(len(request.messages[0].text), 2000)

    def test_empty_messages_rejected(self):
        with self.assertRaises(ValidationError):
            ChatRequest(messages=[])

    def test_too_many_messages_rejected(self):
        with self.assertRaises(ValidationError):
            ChatRequest(messages=make_messages(51))

    def test_whitespace_only_is_422(self):
        body = ChatRequest(messages=[{"role": "user", "text": "   "}])
        with self.assertRaises(HTTPException) as ctx:
            create_chat(body)
        self.assertEqual(ctx.exception.status_code, 422)


class ChatEndpointTest(unittest.TestCase):
    def test_valid_reply(self):
        fake = FakeClient(result=FakeResponse(output_text="  Salut, comment ça va ?  "))
        with patch("main.get_client", return_value=fake):
            response = create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertIsInstance(response, ChatResponse)
        self.assertEqual(response.reply, "Salut, comment ça va ?")
        self.assertEqual(len(fake.responses.calls), 1)

    def test_long_history_keeps_last_12(self):
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(ChatRequest(messages=make_messages(20)))
        sent = fake.responses.calls[0]["input"]
        self.assertEqual(len(sent.splitlines()), 12)
        for i in range(8):
            self.assertNotIn(f"marqueur-{i:02d}", sent)
        for i in range(8, 20):
            self.assertIn(f"marqueur-{i:02d}", sent)

    def test_call_uses_string_input_only(self):
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        kwargs = fake.responses.calls[0]
        self.assertEqual(set(kwargs), {"model", "instructions", "input"})
        self.assertEqual(kwargs["model"], MODEL_NAME)
        self.assertIsInstance(kwargs["input"], str)
        self.assertNotIn("conversation", kwargs)
        self.assertNotIn("previous_response_id", kwargs)
        self.assertIn("conversational", kwargs["instructions"])

    def test_missing_output_is_502(self):
        fake = FakeClient(result=FakeResponse())
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_none_output_is_502(self):
        fake = FakeClient(result=FakeResponse(output_text=None))
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_nontext_output_is_502(self):
        fake = FakeClient(result=FakeResponse(output_text=12345))
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_empty_output_is_502(self):
        fake = FakeClient(result=FakeResponse(output_text="   "))
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_auth_error_maps_401(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        error = AuthenticationError(
            "clé invalide", response=httpx2.Response(401, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_timeout_maps_504(self):
        error = APITimeoutError(request=httpx2.Request("POST", "https://api.meta.ai/v1/responses"))
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertEqual(ctx.exception.status_code, 504)

    def test_status_error_maps_502(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        error = APIStatusError(
            "panne", response=httpx2.Response(500, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertEqual(ctx.exception.status_code, 502)


def make_image(mime="image/jpeg", raw=None):
    if raw is None:
        raw = b"\xff\xd8\xff" + bytes(100)
    return {"mime": mime, "data": base64.b64encode(raw).decode("ascii")}


PNG_RAW = b"\x89PNG\r\n\x1a\n" + bytes(100)
GIF_RAW = b"GIF89a" + bytes(100)
WEBP_RAW = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + bytes(100)
ICON_RAW = b"\x00\x00\x01\x00" + bytes(100)


class ChatImageValidationTest(unittest.TestCase):
    def test_image_defaults_to_none(self):
        request = ChatRequest(messages=[{"role": "user", "text": "Bonjour"}])
        self.assertIsNone(request.messages[0].image)

    def test_disallowed_mime_is_422(self):
        body = ChatRequest(
            messages=[{"role": "user", "text": "", "image": make_image("image/tiff")}]
        )
        with self.assertRaises(HTTPException) as ctx:
            create_chat(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_invalid_base64_is_422(self):
        body = ChatRequest(
            messages=[
                {
                    "role": "user",
                    "text": "",
                    "image": {"mime": "image/png", "data": "!!!pas-du-base64!!!"},
                }
            ]
        )
        with self.assertRaises(HTTPException) as ctx:
            create_chat(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_wrong_signature_is_422(self):
        body = ChatRequest(
            messages=[
                {
                    "role": "user",
                    "text": "",
                    "image": make_image("image/png", b"pas-une-image" + bytes(100)),
                }
            ]
        )
        with self.assertRaises(HTTPException) as ctx:
            create_chat(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_oversize_image_is_413(self):
        big = b"\xff\xd8\xff" + bytes(MAX_CHAT_IMAGE_BYTES)
        body = ChatRequest(
            messages=[{"role": "user", "text": "", "image": make_image("image/jpeg", big)}]
        )
        with self.assertRaises(HTTPException) as ctx:
            create_chat(body)
        self.assertEqual(ctx.exception.status_code, 413)

    def test_mime_case_insensitive(self):
        body = ChatRequest(
            messages=[{"role": "user", "text": "", "image": make_image("IMAGE/PNG", PNG_RAW)}]
        )
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(body)
        sent = fake.responses.calls[0]["input"]
        self.assertIn("data:image/png;base64,", sent[-1]["content"][1]["image_url"])

    def test_assistant_image_is_422(self):
        body = ChatRequest(
            messages=[
                {"role": "user", "text": "Bonjour"},
                {"role": "assistant", "text": "Salut", "image": make_image()},
            ]
        )
        with self.assertRaises(HTTPException) as ctx:
            create_chat(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_other_signatures_accepted(self):
        cases = [
            ("image/gif", GIF_RAW),
            ("image/webp", WEBP_RAW),
            ("image/x-icon", ICON_RAW),
            ("image/jpg", b"\xff\xd8\xff" + bytes(100)),
        ]
        for mime, raw in cases:
            with self.subTest(mime=mime):
                body = ChatRequest(
                    messages=[{"role": "user", "text": "", "image": make_image(mime, raw)}]
                )
                fake = FakeClient(result=FakeResponse(output_text="ok"))
                with patch("main.get_client", return_value=fake):
                    response = create_chat(body)
                self.assertEqual(response.reply, "ok")


class ChatImagePayloadTest(unittest.TestCase):
    def test_text_only_stays_string_input(self):
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(ChatRequest(messages=[{"role": "user", "text": "Bonjour"}]))
        self.assertIsInstance(fake.responses.calls[0]["input"], str)

    def test_jpeg_image_payload(self):
        raw = b"\xff\xd8\xff" + bytes(100)
        expected_b64 = base64.b64encode(raw).decode("ascii")
        fake = FakeClient(result=FakeResponse(output_text="je vois"))
        with patch("main.get_client", return_value=fake):
            response = create_chat(
                ChatRequest(
                    messages=[
                        {
                            "role": "user",
                            "text": "Que vois-tu ?",
                            "image": make_image("image/jpeg", raw),
                        }
                    ]
                )
            )
        self.assertEqual(response.reply, "je vois")
        sent = fake.responses.calls[0]["input"]
        self.assertIsInstance(sent, list)
        self.assertEqual(len(sent), 1)
        message = sent[0]
        self.assertEqual(message["type"], "message")
        self.assertEqual(message["role"], "user")
        self.assertEqual(
            message["content"],
            [
                {"type": "input_text", "text": "Que vois-tu ?"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64," + expected_b64},
            ],
        )

    def test_png_image_payload(self):
        expected_b64 = base64.b64encode(PNG_RAW).decode("ascii")
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(
                ChatRequest(
                    messages=[
                        {
                            "role": "user",
                            "text": "Analyse.",
                            "image": make_image("image/png", PNG_RAW),
                        }
                    ]
                )
            )
        content = fake.responses.calls[0]["input"][0]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": "Analyse."})
        self.assertEqual(
            content[1],
            {"type": "input_image", "image_url": "data:image/png;base64," + expected_b64},
        )

    def test_image_only_message(self):
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(
                ChatRequest(messages=[{"role": "user", "text": "", "image": make_image()}])
            )
        content = fake.responses.calls[0]["input"][0]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": CHAT_IMAGE_ONLY_TEXT})
        self.assertEqual(content[1]["type"], "input_image")

    def test_old_images_not_forwarded(self):
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(
                ChatRequest(
                    messages=[
                        {"role": "user", "text": "", "image": make_image()},
                        {"role": "assistant", "text": "Une photo de chat."},
                        {"role": "user", "text": "Et sa couleur ?"},
                    ]
                )
            )
        sent = fake.responses.calls[0]["input"]
        # Aucune image courante : entrée texte seule, sans les anciennes images.
        self.assertIsInstance(sent, str)
        self.assertIn("Et sa couleur ?", sent)
        self.assertIn("Une photo de chat.", sent)

    def test_history_with_current_image(self):
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(
                ChatRequest(
                    messages=[
                        {"role": "user", "text": "Bonjour"},
                        {"role": "assistant", "text": "Salut !"},
                        {"role": "user", "text": "Décris.", "image": make_image()},
                    ]
                )
            )
        sent = fake.responses.calls[0]["input"]
        self.assertIsInstance(sent, list)
        self.assertEqual(len(sent), 3)
        # Tours anciens : texte seul, rôles préservés, blocs typés par rôle.
        self.assertEqual(sent[0]["role"], "user")
        self.assertEqual(sent[0]["content"], [{"type": "input_text", "text": "Bonjour"}])
        self.assertEqual(sent[1]["role"], "assistant")
        self.assertEqual(sent[1]["content"], [{"type": "output_text", "text": "Salut !"}])
        # Message courant : texte + image.
        self.assertEqual(sent[2]["role"], "user")
        self.assertEqual(len(sent[2]["content"]), 2)
        self.assertEqual(sent[2]["content"][1]["type"], "input_image")

    def test_build_chat_input_text_fallback(self):
        self.assertEqual(
            build_chat_input([("user", "Salut"), ("assistant", "Bonjour")]),
            "Utilisateur: Salut\nAssistant: Bonjour",
        )

    def test_no_input_text_under_assistant_role(self):
        # Non-régression 400 Meta (param=input_text) : les tours rejoués
        # de l'assistant utilisent output_text, jamais input_text.
        fake = FakeClient(result=FakeResponse(output_text="ok"))
        with patch("main.get_client", return_value=fake):
            create_chat(
                ChatRequest(
                    messages=[
                        {"role": "user", "text": "Bonjour"},
                        {"role": "assistant", "text": "Salut !"},
                        {"role": "user", "text": "Et ceci ?"},
                        {"role": "assistant", "text": "Je t'écoute."},
                        {"role": "user", "text": "Décris.", "image": make_image()},
                    ]
                )
            )
        sent = fake.responses.calls[0]["input"]
        self.assertIsInstance(sent, list)
        for message in sent:
            for block in message["content"]:
                if message["role"] == "assistant":
                    self.assertNotEqual(block["type"], "input_text")
                    self.assertEqual(block["type"], "output_text")
                elif block["type"] in ("input_text", "output_text"):
                    self.assertEqual(block["type"], "input_text")
