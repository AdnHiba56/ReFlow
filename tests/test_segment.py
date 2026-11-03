"""Tests ciblés de POST /segment (contrat, validations, parsing SAM, erreurs).

Aucun appel réseau : le client Meta est simulé, mais les objets de résultat
sont de vraies dataclasses de meta_sam_parser (paquet officiel installé).
Exécution depuis la racine du projet :
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_segment.py -v
"""

import base64
import unittest
from unittest.mock import patch

import httpx2
from fastapi import HTTPException
from meta_sam_parser import (
    CompletedOutcome,
    ImageSegmentationResult,
    IncompleteOutcome,
    InvalidSegmentationMaskError,
    ResponsesStreamAbortedError,
    SegmentationBoxRecord,
    SegmentationDiagnostic,
    SegmentationMask,
    SegmentationMaskBounds,
    SegmentationMaskIdentity,
    SegmentationMaskRecord,
    image_segmentation_format,
)
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError

from main import (
    MAX_CHAT_IMAGE_BYTES,
    MAX_SEGMENT_MATCHES,
    MAX_SEGMENT_SVG_CHARS,
    MAX_SEGMENT_TOTAL_SVG_CHARS,
    MAX_SEGMENT_SVG_CHARS,
    SAM_MODEL_NAME,
    SAM_TIMEOUT,
    SegmentRequest,
    SegmentResponse,
    _image_dimensions,
    segment_image,
)


def run_coro(coro):
    """Exécute une coroutine mockée sans boucle (aucune I/O réelle)."""
    try:
        yielded = coro.send(None)
    except StopIteration as done:
        return done.value
    coro.close()
    raise AssertionError(f"coroutine inattendue en attente de {yielded!r}")


# Ligne de grammaire SAM documentée (exemple officiel) : deux objets réels.
REAL_LINE = (
    "<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,60,~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>"
    ",1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|>"
    "<|mask;x=0;y=0;data=27,48,~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>\n"
)


def real_result(line=""):
    """Construit un vrai résultat via le parseur officiel (zéro réseau)."""
    parser = image_segmentation_format().create_parser()
    if line:
        parser.push(line)
    return parser.finish(CompletedOutcome()).result


def box_record(object_id, left=10.0, top=20.0, right=60.0, bottom=80.0, confidence=None):
    return SegmentationBoxRecord(
        order=0,
        object_id=object_id,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        frame=None,
        confidence=confidence,
    )


def mask_record(object_id, width=27, height=60):
    return SegmentationMaskRecord(
        order=1,
        object_id=object_id,
        identity=SegmentationMaskIdentity(media="image", frame_index=None, object_id=object_id),
        revision=1,
        mask=SegmentationMask(encoding="lossless", payload="~", width=width, height=height),
        bounds=SegmentationMaskBounds(left=10.0, top=20.0, right=60.0, bottom=80.0),
        frame=None,
        confidence=None,
    )


def make_result(records=(), diagnostics=(), outcome=None):
    return ImageSegmentationResult(
        revision=1,
        records=tuple(records),
        diagnostics=tuple(diagnostics),
        raw_output="",
        outcome=outcome if outcome is not None else CompletedOutcome(),
    )


class FakeParsed:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def final_result(self):
        if self.error is not None:
            raise self.error
        return self.result


class FakeStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeResponses:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return FakeStream()


class FakeAsyncClient:
    def __init__(self, error=None):
        self.responses = FakeResponses(error=error)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


PNG_RAW = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0dIHDR"
    + (640).to_bytes(4, "big")
    + (480).to_bytes(4, "big")
    + bytes(20)
)
GIF_RAW = b"GIF89a" + (320).to_bytes(2, "little") + (200).to_bytes(2, "little") + bytes(20)
JPEG_RAW = (
    b"\xff\xd8"
    + b"\xff\xe0\x00\x10" + bytes(14)
    + b"\xff\xc0\x00\x09\x08" + (300).to_bytes(2, "big") + (400).to_bytes(2, "big") + bytes(3)
)
WEBP_VP8X_RAW = (
    b"RIFF" + (30).to_bytes(4, "little") + b"WEBP" + b"VP8X" + bytes(4)
    + bytes(4) + (1919).to_bytes(3, "little") + (1079).to_bytes(3, "little")
)
WEBP_VP8_RAW = (
    b"RIFF" + (30).to_bytes(4, "little") + b"WEBP" + b"VP8 " + bytes(4)
    + bytes(3) + b"\x9d\x01\x2a" + (800).to_bytes(2, "little") + (600).to_bytes(2, "little")
)
WEBP_VP8L_RAW = (
    b"RIFF" + (25).to_bytes(4, "little") + b"WEBP" + b"VP8L" + bytes(4)
    + b"\x2f" + ((99 | (149 << 14))).to_bytes(4, "little")
)
ICO_RAW = b"\x00\x00\x01\x00\x01\x00\x20\x20" + bytes(20)


def to_b64(raw):
    return base64.b64encode(raw).decode("ascii")


def make_image(mime="image/png", raw=None):
    return {"mime": mime, "data": to_b64(raw if raw is not None else PNG_RAW)}


def make_body(concept="velo", image=None):
    return SegmentRequest(concept=concept, image=image if image is not None else make_image())


def run_segment(body, client, parsed):
    with patch("main.get_async_client", return_value=client), patch(
        "main.parse_responses_stream", return_value=parsed
    ):
        return run_coro(segment_image(body))


class SegmentValidationTest(unittest.TestCase):
    def test_empty_concept_rejected(self):
        with self.assertRaises(ValidationError):
            SegmentRequest(concept="", image=make_image())

    def test_whitespace_concept_is_422(self):
        body = SegmentRequest(concept="   ", image=make_image())
        with self.assertRaises(HTTPException) as ctx:
            run_coro(segment_image(body))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_too_long_concept_rejected(self):
        with self.assertRaises(ValidationError):
            SegmentRequest(concept="x" * 201, image=make_image())

    def test_disallowed_mime_is_422(self):
        body = make_body(image=make_image("image/tiff"))
        with self.assertRaises(HTTPException) as ctx:
            run_coro(segment_image(body))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_invalid_base64_is_422(self):
        body = SegmentRequest(
            concept="velo", image={"mime": "image/png", "data": "!!!pas-du-base64!!!"}
        )
        with self.assertRaises(HTTPException) as ctx:
            run_coro(segment_image(body))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_wrong_signature_is_422(self):
        body = make_body(image=make_image("image/png", b"pas-une-image" + bytes(100)))
        with self.assertRaises(HTTPException) as ctx:
            run_coro(segment_image(body))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_oversize_input_is_413(self):
        big = b"\xff\xd8\xff" + bytes(MAX_CHAT_IMAGE_BYTES)
        body = make_body(image=make_image("image/jpeg", big))
        with self.assertRaises(HTTPException) as ctx:
            run_coro(segment_image(body))
        self.assertEqual(ctx.exception.status_code, 413)

    def test_unreadable_dimensions_is_422(self):
        body = make_body(image=make_image("image/jpeg", b"\xff\xd8\xff" + bytes(100)))
        with self.assertRaises(HTTPException) as ctx:
            run_coro(segment_image(body))
        self.assertEqual(ctx.exception.status_code, 422)


class ImageDimensionsTest(unittest.TestCase):
    def test_dimensions_per_format(self):
        cases = [
            ("image/png", PNG_RAW, (640, 480)),
            ("image/gif", GIF_RAW, (320, 200)),
            ("image/jpeg", JPEG_RAW, (400, 300)),
            ("image/jpg", JPEG_RAW, (400, 300)),
            ("image/webp", WEBP_VP8X_RAW, (1920, 1080)),
            ("image/webp", WEBP_VP8_RAW, (800, 600)),
            ("image/webp", WEBP_VP8L_RAW, (100, 150)),
            ("image/x-icon", ICO_RAW, (32, 32)),
        ]
        for mime, raw, expected in cases:
            with self.subTest(mime=mime):
                self.assertEqual(_image_dimensions(mime, raw), expected)


class SegmentEndpointTest(unittest.TestCase):
    def test_successful_segmentation(self):
        client = FakeAsyncClient()
        response = run_segment(make_body(), client, FakeParsed(result=real_result(REAL_LINE)))
        self.assertIsInstance(response, SegmentResponse)
        self.assertEqual((response.image.width, response.image.height), (640, 480))
        self.assertFalse(response.truncated)
        self.assertEqual(len(response.matches), 2)
        first = response.matches[0]
        self.assertEqual(first.object_id, "0")
        self.assertEqual(
            (first.box.left, first.box.top, first.box.right, first.box.bottom),
            (211.0, 228.0, 271.0, 255.0),
        )
        self.assertIsNone(first.confidence)
        self.assertIsNotNone(first.mask)
        self.assertEqual((first.mask.width, first.mask.height), (60, 27))
        self.assertTrue(first.mask.svg.startswith("M"))
        self.assertEqual(len(client.responses.calls), 1)

    def test_call_shape(self):
        client = FakeAsyncClient()
        run_segment(make_body(concept="  velo rouge  "), client, FakeParsed(result=make_result()))
        kwargs = client.responses.calls[0]
        self.assertEqual(kwargs["model"], SAM_MODEL_NAME)
        self.assertTrue(kwargs["stream"])
        self.assertNotIn("n", kwargs)
        self.assertNotIn("response_format", kwargs)
        messages = kwargs["input"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "message")
        self.assertEqual(messages[0]["role"], "user")
        parts = messages[0]["content"]
        self.assertEqual(parts[0], {"type": "input_text", "text": "velo rouge"})
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png;base64,"))

    def test_zero_matches_is_200_empty(self):
        client = FakeAsyncClient()
        response = run_segment(make_body(), client, FakeParsed(result=real_result()))
        self.assertEqual(response.matches, [])
        self.assertFalse(response.truncated)
        self.assertEqual((response.image.width, response.image.height), (640, 480))

    def test_incomplete_outcome_is_502(self):
        client = FakeAsyncClient()
        result = make_result(outcome=IncompleteOutcome(reason="eof", detail=None))
        with self.assertRaises(HTTPException) as ctx:
            run_segment(make_body(), client, FakeParsed(result=result))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_error_diagnostic_is_502(self):
        client = FakeAsyncClient()
        diagnostic = SegmentationDiagnostic(
            severity="error", code="bad_record", message="ligne rejetée", line=1, raw="?"
        )
        result = make_result(diagnostics=[diagnostic])
        with self.assertRaises(HTTPException) as ctx:
            run_segment(make_body(), client, FakeParsed(result=result))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_stream_error_is_502(self):
        client = FakeAsyncClient()
        with self.assertRaises(HTTPException) as ctx:
            run_segment(
                make_body(), client, FakeParsed(error=ResponsesStreamAbortedError())
            )
        self.assertEqual(ctx.exception.status_code, 502)

    def test_auth_error_maps_401(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        error = AuthenticationError(
            "clé invalide", response=httpx2.Response(401, request=request), body=None
        )
        client = FakeAsyncClient(error=error)
        with self.assertRaises(HTTPException) as ctx:
            run_segment(make_body(), client, FakeParsed(result=make_result()))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_status_error_maps_502(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        error = APIStatusError("panne", response=httpx2.Response(500, request=request), body=None)
        client = FakeAsyncClient(error=error)
        with self.assertRaises(HTTPException) as ctx:
            run_segment(make_body(), client, FakeParsed(result=make_result()))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_oversize_mask_dropped_and_truncated(self):
        client = FakeAsyncClient()
        result = make_result(records=[box_record("0"), mask_record("0")])
        with patch("main.decode_mask_to_svg_path", return_value="M" + "0" * MAX_SEGMENT_SVG_CHARS):
            response = run_segment(make_body(), client, FakeParsed(result=result))
        self.assertEqual(len(response.matches), 1)
        self.assertIsNone(response.matches[0].mask)
        self.assertTrue(response.truncated)

    def test_invalid_mask_decode_keeps_box(self):
        client = FakeAsyncClient()
        result = make_result(records=[box_record("0"), mask_record("0")])
        with patch(
            "main.decode_mask_to_svg_path",
            side_effect=InvalidSegmentationMaskError("masque corrompu"),
        ):
            response = run_segment(make_body(), client, FakeParsed(result=result))
        self.assertEqual(len(response.matches), 1)
        self.assertIsNone(response.matches[0].mask)
        self.assertFalse(response.truncated)

    def test_mask_without_box_skipped(self):
        client = FakeAsyncClient()
        result = make_result(records=[mask_record("7")])
        with patch("main.decode_mask_to_svg_path", return_value="M0 0"):
            response = run_segment(make_body(), client, FakeParsed(result=result))
        self.assertEqual(response.matches, [])

    def test_too_many_matches_truncated(self):
        client = FakeAsyncClient()
        records = [box_record(str(i)) for i in range(MAX_SEGMENT_MATCHES + 5)]
        response = run_segment(make_body(), client, FakeParsed(result=make_result(records)))
        self.assertEqual(len(response.matches), MAX_SEGMENT_MATCHES)
        self.assertTrue(response.truncated)

    def test_confidence_forwarded(self):
        client = FakeAsyncClient()
        result = make_result(records=[box_record("0", confidence=0.87)])
        response = run_segment(make_body(), client, FakeParsed(result=result))
        self.assertAlmostEqual(response.matches[0].confidence, 0.87)

    def test_error_mapping(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        cases = [
            (APITimeoutError(request=request), 504),
            (APIConnectionError(request=request), 503),
            (
                RateLimitError(
                    "quota", response=httpx2.Response(429, request=request), body=None
                ),
                429,
            ),
            (
                NotFoundError(
                    "absent", response=httpx2.Response(404, request=request), body=None
                ),
                404,
            ),
            (
                PermissionDeniedError(
                    "refusé", response=httpx2.Response(403, request=request), body=None
                ),
                403,
            ),
        ]
        for error, status in cases:
            with self.subTest(status=status):
                client = FakeAsyncClient(error=error)
                with self.assertRaises(HTTPException) as ctx:
                    run_segment(make_body(), client, FakeParsed(result=make_result()))
                self.assertEqual(ctx.exception.status_code, status)

    def test_total_svg_budget_truncates(self):
        per_mask = MAX_SEGMENT_SVG_CHARS // 2
        count = MAX_SEGMENT_TOTAL_SVG_CHARS // per_mask + 1
        records = []
        for i in range(count):
            records.append(box_record(str(i)))
            records.append(mask_record(str(i)))
        client = FakeAsyncClient()
        with patch("main.decode_mask_to_svg_path", return_value="M" + "0" * (per_mask - 1)):
            response = run_segment(make_body(), client, FakeParsed(result=make_result(records)))
        self.assertTrue(response.truncated)
        kept = [m for m in response.matches if m.mask is not None]
        dropped = [m for m in response.matches if m.mask is None]
        self.assertTrue(kept)
        self.assertTrue(dropped)

    def test_successive_calls_are_independent(self):
        """Retry après erreur : la route est sans état, le 2e appel réussit."""
        request = httpx2.Request("POST", "https://api.meta.ai/v1/responses")
        failing = FakeAsyncClient(
            error=APIStatusError(
                "panne", response=httpx2.Response(500, request=request), body=None
            )
        )
        with self.assertRaises(HTTPException):
            run_segment(make_body(), failing, FakeParsed(result=make_result()))
        succeeding = FakeAsyncClient()
        response = run_segment(
            make_body(), succeeding, FakeParsed(result=real_result(REAL_LINE))
        )
        self.assertEqual(len(response.matches), 2)


class AsyncClientConfigTest(unittest.TestCase):
    def test_async_client_uses_sam_timeout(self):
        from main import get_async_client

        with patch("main.os.getenv", return_value="cle-factice-test"):
            client = get_async_client()
        self.assertEqual(client.timeout, SAM_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
