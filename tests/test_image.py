"""Tests ciblés de POST /image (contrat, validations, erreurs).

Aucun appel à Meta Model API : le client est entièrement simulé.
Les statuts HTTP sont vérifiés via HTTPException.status_code (erreurs) et le
modèle de réponse retourné (succès, sérialisé en 200 par FastAPI).
Exécution depuis la racine du projet :
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_image.py -v
"""

import base64
import unittest
from unittest.mock import patch

import httpx2
from fastapi import HTTPException
from openai import APIStatusError, AuthenticationError
from pydantic import ValidationError

from main import (
    IMAGE_MODEL_NAME,
    MAX_GENERATED_IMAGE_BYTES,
    ImageRequest,
    ImageResponse,
    create_image,
)


class FakeImageEntry:
    """Entrée data[] façon SDK : attribut b64_json."""

    def __init__(self, b64_json=None):
        self.b64_json = b64_json


class FakeImagesResponse:
    def __init__(self, data):
        self.data = data


class FakeImages:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, result=None, error=None):
        self.images = FakeImages(result=result, error=error)


PNG_RAW = b"\x89PNG\r\n\x1a\n" + bytes(100)
JPEG_RAW = b"\xff\xd8\xff" + bytes(100)
WEBP_RAW = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + bytes(100)


def to_b64(raw):
    return base64.b64encode(raw).decode("ascii")


def make_body(prompt="Un renard roux dans la neige", size=None):
    payload = {"prompt": prompt}
    if size is not None:
        payload["size"] = size
    return ImageRequest(**payload)


class ImageValidationTest(unittest.TestCase):
    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValidationError):
            ImageRequest(prompt="")

    def test_whitespace_prompt_is_422(self):
        body = ImageRequest(prompt="   ")
        with self.assertRaises(HTTPException) as ctx:
            create_image(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_too_long_prompt_rejected(self):
        with self.assertRaises(ValidationError):
            ImageRequest(prompt="x" * 2001)

    def test_bad_size_is_422(self):
        body = ImageRequest(prompt="Un chat", size="9999x9999")
        with self.assertRaises(HTTPException) as ctx:
            create_image(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_size_defaults_to_none(self):
        request = ImageRequest(prompt="Un chat")
        self.assertIsNone(request.size)


class ImageEndpointTest(unittest.TestCase):
    def test_valid_png_generation(self):
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(PNG_RAW))]))
        with patch("main.get_client", return_value=fake):
            response = create_image(make_body())
        self.assertIsInstance(response, ImageResponse)
        self.assertEqual(response.mime, "image/png")
        self.assertEqual(response.data, to_b64(PNG_RAW))
        self.assertEqual(len(fake.images.calls), 1)

    def test_call_shape(self):
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(PNG_RAW))]))
        with patch("main.get_client", return_value=fake):
            create_image(make_body(prompt="  Un chat  "))
        kwargs = fake.images.calls[0]
        self.assertEqual(kwargs["model"], IMAGE_MODEL_NAME)
        self.assertEqual(kwargs["prompt"], "Un chat")
        self.assertEqual(kwargs["n"], 1)
        self.assertEqual(kwargs["response_format"], "b64_json")
        self.assertNotIn("size", kwargs)

    def test_size_forwarded_when_provided(self):
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(PNG_RAW))]))
        with patch("main.get_client", return_value=fake):
            create_image(make_body(size="1792x1024"))
        self.assertEqual(fake.images.calls[0]["size"], "1792x1024")

    def test_mime_detection(self):
        cases = [
            ("image/jpeg", JPEG_RAW),
            ("image/webp", WEBP_RAW),
        ]
        for expected_mime, raw in cases:
            with self.subTest(mime=expected_mime):
                fake = FakeClient(
                    result=FakeImagesResponse(data=[FakeImageEntry(to_b64(raw))])
                )
                with patch("main.get_client", return_value=fake):
                    response = create_image(make_body())
                self.assertEqual(response.mime, expected_mime)

    def test_dict_entry_supported(self):
        fake = FakeClient(result=FakeImagesResponse(data=[{"b64_json": to_b64(JPEG_RAW)}]))
        with patch("main.get_client", return_value=fake):
            response = create_image(make_body())
        self.assertEqual(response.mime, "image/jpeg")

    def test_missing_data_is_502(self):
        for data in (None, []):
            with self.subTest(data=data):
                fake = FakeClient(result=FakeImagesResponse(data=data))
                with patch("main.get_client", return_value=fake):
                    with self.assertRaises(HTTPException) as ctx:
                        create_image(make_body())
                self.assertEqual(ctx.exception.status_code, 502)

    def test_empty_b64_is_502(self):
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry("")]))
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)

    def test_invalid_b64_is_502(self):
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry("!!!pas-du-base64!!!")]))
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)

    def test_unknown_signature_is_502(self):
        fake = FakeClient(
            result=FakeImagesResponse(data=[FakeImageEntry(to_b64(b"pas-une-image" + bytes(100)))])
        )
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)

    def test_oversize_output_is_502(self):
        big = b"\x89PNG\r\n\x1a\n" + bytes(MAX_GENERATED_IMAGE_BYTES)
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(big))]))
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)

    def test_content_policy_is_422(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/images/generations")
        error = APIStatusError(
            "refusé",
            response=httpx2.Response(400, request=request),
            body={"code": "content_policy_violation", "type": "invalid_request_error"},
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_image(make_body())
        self.assertEqual(ctx.exception.status_code, 422)

    def test_auth_error_maps_401(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/images/generations")
        error = AuthenticationError(
            "clé invalide", response=httpx2.Response(401, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_image(make_body())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_status_error_maps_502(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/images/generations")
        error = APIStatusError(
            "panne", response=httpx2.Response(500, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                create_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
