"""Tests du stockage persistant des images générées (reflow-media/).

Aucun appel réseau : le client Meta est simulé et le stockage est redirigé
vers un répertoire temporaire via ``media.MEDIA_DIRECTORY``.
"""

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import FileResponse

import media
from media import media_path, media_response, save_generated_image

PNG_RAW = b"\x89PNG\r\n\x1a\n" + bytes(100)
JPEG_RAW = b"\xff\xd8\xff" + bytes(100)
WEBP_RAW = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + bytes(100)


def to_b64(raw):
    return base64.b64encode(raw).decode("ascii")


class FakeImageEntry:
    def __init__(self, b64_json=None):
        self.b64_json = b64_json


class FakeImagesResponse:
    def __init__(self, data):
        self.data = data


class FakeImages:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return self.result

    def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))
        return self.result


class FakeClient:
    def __init__(self, result=None):
        self.images = FakeImages(result=result)


class MediaStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = media.MEDIA_DIRECTORY
        media.MEDIA_DIRECTORY = self.tmp.name

    def tearDown(self):
        media.MEDIA_DIRECTORY = self.previous
        self.tmp.cleanup()

    def test_save_returns_stable_hash_id(self):
        media_id = save_generated_image(PNG_RAW, "image/png")
        self.assertRegex(media_id, r"^[0-9a-f]{64}\.png$")
        self.assertEqual(media_id, hashlib.sha256(PNG_RAW).hexdigest() + ".png")
        stored = Path(self.tmp.name) / media_id
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.read_bytes(), PNG_RAW)

    def test_save_dedupes_identical_bytes(self):
        first = save_generated_image(JPEG_RAW, "image/jpeg")
        second = save_generated_image(JPEG_RAW, "image/jpeg")
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(".jpg"))
        self.assertEqual(len(list(Path(self.tmp.name).glob("*"))), 1)

    def test_save_distinguishes_mime(self):
        png_id = save_generated_image(PNG_RAW, "image/png")
        webp_id = save_generated_image(WEBP_RAW, "image/webp")
        self.assertNotEqual(png_id, webp_id)
        self.assertTrue(webp_id.endswith(".webp"))

    def test_save_rejects_bad_payload(self):
        for raw, mime in ((b"", "image/png"), (PNG_RAW, "image/gif"), (PNG_RAW, "text/plain")):
            with self.subTest(mime=mime, size=len(raw)):
                with self.assertRaises(HTTPException) as ctx:
                    save_generated_image(raw, mime)
                self.assertEqual(ctx.exception.status_code, 502)
        with self.assertRaises(HTTPException) as ctx:
            save_generated_image(b"x" * (media.MAX_MEDIA_BYTES + 1), "image/png")
        self.assertEqual(ctx.exception.status_code, 502)


class MediaServeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = media.MEDIA_DIRECTORY
        media.MEDIA_DIRECTORY = self.tmp.name

    def tearDown(self):
        media.MEDIA_DIRECTORY = self.previous
        self.tmp.cleanup()

    def test_serve_returns_file_with_mime(self):
        media_id = save_generated_image(PNG_RAW, "image/png")
        response = media_response(media_id)
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.media_type, "image/png")
        self.assertEqual(Path(response.path).read_bytes(), PNG_RAW)

    def test_missing_image_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            media_response("0" * 64 + ".png")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertNotIn("0" * 64, ctx.exception.detail)

    def test_corrupted_file_is_404(self):
        media_id = save_generated_image(PNG_RAW, "image/png")
        (Path(self.tmp.name) / media_id).write_bytes(b"not an image at all....")
        with self.assertRaises(HTTPException) as ctx:
            media_response(media_id)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_traversal_and_bad_ids_rejected(self):
        good = save_generated_image(PNG_RAW, "image/png")
        evil = [
            "../secret.txt",
            "..\\secret.txt",
            "/etc/passwd",
            "a/b.png",
            "x.exe",
            "",
            None,
            123,
            "Z" * 64 + ".png",
            "0" * 63 + ".png",
            "0" * 64 + ".PNG",
            "0" * 64 + ".gif",
            "0" * 64 + ".png ",
            " " + "0" * 64 + ".png",
            good + ".png",
        ]
        for candidate in evil:
            with self.subTest(candidate=candidate):
                with self.assertRaises(HTTPException) as ctx:
                    media_path(candidate)
                self.assertEqual(ctx.exception.status_code, 404)

    def test_nothing_deleted_or_listed(self):
        first = save_generated_image(PNG_RAW, "image/png")
        second = save_generated_image(JPEG_RAW, "image/jpeg")
        media_response(first)
        self.assertTrue((Path(self.tmp.name) / first).is_file())
        self.assertTrue((Path(self.tmp.name) / second).is_file())


class ImageRoutePersistenceTest(unittest.TestCase):
    """POST /image et /image/edit persistent une copie et exposent son id."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = media.MEDIA_DIRECTORY
        media.MEDIA_DIRECTORY = self.tmp.name

    def tearDown(self):
        media.MEDIA_DIRECTORY = self.previous
        self.tmp.cleanup()

    def test_generate_persists_and_returns_media_id(self):
        from main import ImageRequest, create_image

        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(PNG_RAW))]))
        with patch("main.get_client", return_value=fake):
            response = create_image(ImageRequest(prompt="Un renard"))
        self.assertEqual(response.mime, "image/png")
        self.assertEqual(response.data, to_b64(PNG_RAW))
        self.assertRegex(response.media_id, r"^[0-9a-f]{64}\.png$")
        self.assertEqual((Path(self.tmp.name) / response.media_id).read_bytes(), PNG_RAW)

    def test_edit_persists_and_returns_media_id(self):
        from main import ImageEditRequest, edit_image

        fake = FakeClient(result=FakeImagesResponse(data=[FakeImageEntry(to_b64(JPEG_RAW))]))
        body = ImageEditRequest(
            prompt="Make it brighter",
            image={"mime": "image/png", "data": to_b64(PNG_RAW)},
        )
        with patch("main.get_client", return_value=fake):
            response = edit_image(body)
        self.assertEqual(response.mime, "image/jpeg")
        self.assertRegex(response.media_id, r"^[0-9a-f]{64}\.jpg$")
        self.assertEqual((Path(self.tmp.name) / response.media_id).read_bytes(), JPEG_RAW)

    def test_media_route_serves_and_404s(self):
        from main import get_media

        media_id = save_generated_image(WEBP_RAW, "image/webp")
        response = get_media(media_id)
        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.media_type, "image/webp")
        with self.assertRaises(HTTPException) as ctx:
            get_media("../nope.png")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
