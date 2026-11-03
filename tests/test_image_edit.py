"""Tests ciblés de POST /image/edit (contrat, validations, erreurs).

Aucun appel à Meta Model API : le client est entièrement simulé.
Exécution depuis la racine du projet :
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_image_edit.py -v
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
    MAX_CHAT_IMAGE_BYTES,
    MAX_GENERATED_IMAGE_BYTES,
    ImageEditRequest,
    ImageResponse,
    edit_image,
)


class FakeImageEntry:
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

    def edit(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, result=None, error=None):
        self.images = FakeImages(result=result, error=error)


PNG_RAW = b"\x89PNG\r\n\x1a\n" + bytes(100)
JPEG_RAW = b"\xff\xd8\xff" + bytes(100)


def to_b64(raw):
    return base64.b64encode(raw).decode("ascii")


def make_image(mime="image/jpeg", raw=None):
    payload = raw if raw is not None else JPEG_RAW
    return {"mime": mime, "data": to_b64(payload)}


def make_body(prompt="Supprime l'arrière-plan", image=None):
    return ImageEditRequest(prompt=prompt, image=image if image is not None else make_image())


class ImageEditValidationTest(unittest.TestCase):
    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValidationError):
            ImageEditRequest(prompt="", image=make_image())

    def test_whitespace_prompt_is_422(self):
        body = ImageEditRequest(prompt="   ", image=make_image())
        with self.assertRaises(HTTPException) as ctx:
            edit_image(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_too_long_prompt_rejected(self):
        with self.assertRaises(ValidationError):
            ImageEditRequest(prompt="x" * 2001, image=make_image())

    def test_disallowed_mime_is_422(self):
        body = make_body(image=make_image("image/tiff"))
        with self.assertRaises(HTTPException) as ctx:
            edit_image(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_invalid_base64_is_422(self):
        body = ImageEditRequest(
            prompt="Modifie", image={"mime": "image/png", "data": "!!!pas-du-base64!!!"}
        )
        with self.assertRaises(HTTPException) as ctx:
            edit_image(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_wrong_signature_is_422(self):
        body = make_body(image=make_image("image/png", b"pas-une-image" + bytes(100)))
        with self.assertRaises(HTTPException) as ctx:
            edit_image(body)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_oversize_input_is_413(self):
        big = b"\xff\xd8\xff" + bytes(MAX_CHAT_IMAGE_BYTES)
        body = make_body(image=make_image("image/jpeg", big))
        with self.assertRaises(HTTPException) as ctx:
            edit_image(body)
        self.assertEqual(ctx.exception.status_code, 413)


class ImageEditEndpointTest(unittest.TestCase):
    def test_successful_edit(self):
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(PNG_RAW))]))
        with patch("main.get_client", return_value=fake):
            response = edit_image(make_body())
        self.assertIsInstance(response, ImageResponse)
        self.assertEqual(response.mime, "image/png")
        self.assertEqual(response.data, to_b64(PNG_RAW))
        self.assertEqual(len(fake.images.calls), 1)

    def test_call_shape(self):
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(PNG_RAW))]))
        with patch("main.get_client", return_value=fake):
            edit_image(make_body(prompt="  Floute le fond  "))
        kwargs = fake.images.calls[0]
        self.assertEqual(kwargs["model"], IMAGE_MODEL_NAME)
        self.assertEqual(kwargs["prompt"], "Floute le fond")
        self.assertEqual(kwargs["n"], 1)
        self.assertNotIn("mask", kwargs)
        self.assertNotIn("size", kwargs)
        filename, stream, mime = kwargs["image"]
        self.assertTrue(filename.endswith(".jpg"))
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(stream.read(), JPEG_RAW)

    def test_missing_output_is_502(self):
        for data in (None, [], [FakeImageEntry("")], [FakeImageEntry("!!!pas-du-base64!!!")]):
            with self.subTest(data=data):
                fake = FakeClient(result=FakeImagesResponse(data=data))
                with patch("main.get_client", return_value=fake):
                    with self.assertRaises(HTTPException) as ctx:
                        edit_image(make_body())
                self.assertEqual(ctx.exception.status_code, 502)

    def test_unknown_output_signature_is_502(self):
        fake = FakeClient(
            result=FakeImagesResponse(data=[FakeImageEntry(to_b64(b"pas-une-image" + bytes(100)))])
        )
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                edit_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)

    def test_oversize_output_is_502(self):
        big = b"\x89PNG\r\n\x1a\n" + bytes(MAX_GENERATED_IMAGE_BYTES)
        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(big))]))
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                edit_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)

    def test_content_policy_is_422(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/images/edits")
        error = APIStatusError(
            "refusé",
            response=httpx2.Response(400, request=request),
            body={"code": "content_policy_violation", "type": "invalid_request_error"},
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                edit_image(make_body())
        self.assertEqual(ctx.exception.status_code, 422)

    def test_auth_error_maps_401(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/images/edits")
        error = AuthenticationError(
            "clé invalide", response=httpx2.Response(401, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                edit_image(make_body())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_status_error_maps_502(self):
        request = httpx2.Request("POST", "https://api.meta.ai/v1/images/edits")
        error = APIStatusError(
            "panne", response=httpx2.Response(500, request=request), body=None
        )
        fake = FakeClient(error=error)
        with patch("main.get_client", return_value=fake):
            with self.assertRaises(HTTPException) as ctx:
                edit_image(make_body())
        self.assertEqual(ctx.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
