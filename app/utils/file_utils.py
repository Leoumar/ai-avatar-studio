"""
Small helpers for working with uploaded and generated files.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from pathlib import Path

import aiofiles

from app.config import settings

logger = logging.getLogger(__name__)


def get_extension(filename: str | None, fallback: str = "") -> str:
    """Return the lowercase extension of a filename, e.g. '.png'."""
    if not filename:
        return fallback
    ext = Path(filename).suffix.lower()
    return ext or fallback


def human_size(num_bytes: int) -> str:
    """Turn 1536000 into '1.5 MB' for friendly error messages."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return f"{size:.1f} GB"


def guess_content_type(filename: str, default: str = "application/octet-stream") -> str:
    """Best-effort MIME type from a filename."""
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or default


def new_id() -> str:
    """A fresh UUID string, used as the generation id."""
    return str(uuid.uuid4())


def build_storage_path(folder: str, user_id: str, generation_id: str, extension: str) -> str:
    """
    Build a unique storage path.

    Example:
        images/<user_id>/<generation_id>.png

    Because the generation id is a fresh UUID for every job, files from
    previous generations are never overwritten.
    """
    extension = extension if extension.startswith(".") else f".{extension}"
    return f"{folder}/{user_id}/{generation_id}{extension}"


async def write_temp_file(data: bytes, extension: str, prefix: str = "tmp") -> Path:
    """
    Write bytes into ./storage/tmp and return the path.

    Used for handing a file to an external service that needs a real path.
    """
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    extension = extension if extension.startswith(".") else f".{extension}"
    path = settings.TEMP_DIR / f"{prefix}_{uuid.uuid4().hex}{extension}"
    async with aiofiles.open(path, "wb") as handle:
        await handle.write(data)
    return path


def cleanup_files(*paths: Path | str | None) -> None:
    """
    Delete temporary files, ignoring anything that is already gone.

    Never raises - cleanup failures must not break a successful generation.
    """
    for path in paths:
        if not path:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Could not delete temporary file %s: %s", path, exc)
