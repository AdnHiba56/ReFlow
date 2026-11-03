"""Persistent local storage for ReFlow-generated images (no database).

Backend counterpart of the conversation fix: generated/edited images used to
live only as in-memory ``blob:`` URLs, so every reload expired them. They are
now saved under ``reflow-media/`` next to this module and served back through
``GET /media/{media_id}``.

Security properties:
- Filenames are content hashes (``{sha256hex}.{ext}``): identical bytes are
  stored once (natural dedup) and names carry no user input, so traversal is
  impossible by construction.
- Serving validates the identifier strictly (64 lowercase hex + allowlisted
  extension) and re-checks the magic bytes before answering.
- Images are never deleted automatically; missing or corrupted files answer
  404 with a safe message, never a traceback or a path.
"""

import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger("reflow")

MEDIA_DIR_NAME = "reflow-media"
MEDIA_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MEDIA_MIME_TYPES = {ext: mime for mime, ext in MEDIA_EXTENSIONS.items()}
MEDIA_ID_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")
MAX_MEDIA_BYTES = 10 * 1024 * 1024

# Test hook only: when set, storage and serving use this directory instead of
# ``reflow-media/``. Production code never sets it.
MEDIA_DIRECTORY = None


def media_dir() -> Path:
    """Project-local media directory (``reflow-media/`` by default)."""
    if MEDIA_DIRECTORY:
        return Path(MEDIA_DIRECTORY)
    return Path(__file__).resolve().parent / MEDIA_DIR_NAME


def _signature_ok(extension, raw):
    head = bytes(raw[:12])
    if extension == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == "jpg":
        return head.startswith(b"\xff\xd8\xff")
    if extension == "webp":
        return head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP"
    return False


def save_generated_image(raw, mime, directory=None):
    """Stores generated/edited bytes, returns the stable media id.

    The id doubles as the relative filename. Same bytes always yield the
    same id, so re-saving never duplicates. Raises HTTPException (502) when
    the payload is empty, oversized, or of an unsupported type.
    """
    extension = MEDIA_EXTENSIONS.get(mime)
    if extension is None or not raw or len(raw) > MAX_MEDIA_BYTES:
        raise HTTPException(status_code=502, detail="The generated image is unusable.")
    media_id = hashlib.sha256(bytes(raw)).hexdigest() + "." + extension
    base = Path(directory) if directory else media_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
        target = base / media_id
        if target.is_file() and target.stat().st_size == len(raw):
            return media_id
        fd, tmp_name = tempfile.mkstemp(prefix=".media-", dir=str(base))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(bytes(raw))
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except HTTPException:
        raise
    except OSError:
        logger.error("Media store unavailable: cannot persist generated image")
        raise HTTPException(
            status_code=500, detail="The image was generated but could not be stored."
        )
    logger.info("Media stored: %d bytes (%s)", len(raw), extension)
    return media_id


def media_path(media_id, directory=None):
    """Resolves a media id to its file, or raises a safe 404.

    The strict identifier shape (fixed hash + allowlisted extension) rejects
    traversal attempts, absolute paths, and unknown extensions before any
    filesystem access; containment is re-checked after resolution.
    """
    if not isinstance(media_id, str) or not MEDIA_ID_RE.match(media_id):
        raise HTTPException(status_code=404, detail="Image not found.")
    base = (Path(directory) if directory else media_dir()).resolve()
    target = (base / media_id).resolve()
    if target.parent != base or not target.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return target


def media_response(media_id, directory=None):
    """Builds the file response for ``GET /media/{media_id}`` (404 if absent/corrupt)."""
    target = media_path(media_id, directory=directory)
    extension = media_id.rsplit(".", 1)[1]
    try:
        with open(target, "rb") as handle:
            head = handle.read(12)
    except OSError:
        raise HTTPException(status_code=404, detail="Image not found.")
    if not _signature_ok(extension, head):
        logger.error("Media file failed signature check: extension=%s", extension)
        raise HTTPException(status_code=404, detail="Image is corrupted or unavailable.")
    return FileResponse(path=str(target), media_type=MEDIA_MIME_TYPES[extension], filename=media_id)
