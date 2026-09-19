"""
Supabase service.

This is the only module in the project that knows how records and files are
stored. Routes and the AI pipeline call the functions at the bottom of this
file and never touch Supabase (or the local fallback) directly.

Two backends are supported and they share the same function signatures:

    * Supabase  - PostgreSQL for records, Supabase Storage for files.
    * Local     - ./storage/local_db.json + ./storage/files (development only).

Which one is used is decided by settings.use_supabase (see app/config.py).

Security note: the backend uses the SERVICE ROLE key when it is available,
because it needs to write to Storage and to rows owned by a user. That key
stays on the server. It is never rendered into a template and never sent to
the browser.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.models.schemas import GenerationStatus
from app.services.local_store import LocalStore
from app.utils.file_utils import build_storage_path

logger = logging.getLogger(__name__)

GENERATIONS_TABLE = "avatar_generations"
PROFILES_TABLE = "profiles"

# Folders inside the Supabase Storage bucket
IMAGES_FOLDER = "images"
VOICES_FOLDER = "voices"
GENERATED_AUDIO_FOLDER = "generated-audio"
GENERATED_VIDEO_FOLDER = "generated-videos"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Supabase client
# ----------------------------------------------------------------------

_client: Any | None = None


def get_client() -> Any:
    """
    Create (once) and return the Supabase client.

    Raises a clear RuntimeError if Supabase is selected but not configured,
    instead of failing later with a confusing error.
    """
    global _client
    if _client is not None:
        return _client

    if not settings.SUPABASE_URL:
        raise RuntimeError(
            "Supabase is selected but SUPABASE_URL is missing. "
            "Fill in .env, or set STORAGE_BACKEND=local to use the local fallback."
        )

    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'supabase' package is not installed. Run: pip install -r requirements.txt"
        ) from exc

    key = settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_KEY
    if not key:
        raise RuntimeError("Supabase is selected but no Supabase key is configured.")

    if not settings.SUPABASE_SERVICE_KEY:
        logger.warning(
            "Running Supabase with the anon key. Storage writes will fail unless "
            "your RLS/storage policies allow them. Set SUPABASE_SERVICE_KEY for the backend."
        )

    _client = create_client(settings.SUPABASE_URL, key)
    return _client


# ----------------------------------------------------------------------
# Local fallback instance
# ----------------------------------------------------------------------

_local_store: LocalStore | None = None


def get_local_store() -> LocalStore:
    global _local_store
    if _local_store is None:
        _local_store = LocalStore()
    return _local_store


def backend_name() -> str:
    return "supabase" if settings.use_supabase else "local"


# ----------------------------------------------------------------------
# File storage
# ----------------------------------------------------------------------


async def _upload(path: str, data: bytes, content_type: str) -> str:
    """Upload bytes to the active backend and return the stored path."""
    if not settings.use_supabase:
        return await get_local_store().upload(path, data, content_type)

    def _do_upload() -> str:
        client = get_client()
        client.storage.from_(settings.SUPABASE_BUCKET).upload(
            path=path,
            file=data,
            file_options={"content-type": content_type, "upsert": "false"},
        )
        return path

    return await asyncio.to_thread(_do_upload)


async def download_file(path: str) -> bytes:
    """Read a stored file back into memory."""
    if not settings.use_supabase:
        return await get_local_store().download(path)

    def _do_download() -> bytes:
        client = get_client()
        return client.storage.from_(settings.SUPABASE_BUCKET).download(path)

    return await asyncio.to_thread(_do_download)


async def delete_file(path: str | None) -> None:
    """Delete a stored file. Missing files are ignored."""
    if not path:
        return
    if not settings.use_supabase:
        await get_local_store().delete_file(path)
        return

    def _do_delete() -> None:
        client = get_client()
        client.storage.from_(settings.SUPABASE_BUCKET).remove([path])

    try:
        await asyncio.to_thread(_do_delete)
    except Exception as exc:  # pragma: no cover - cleanup must not break callers
        logger.warning("Could not delete %s from storage: %s", path, exc)


async def create_signed_url(path: str, expires_in: int = 3600) -> str | None:
    """
    Create a temporary public link to a private Storage object.

    The app normally streams media through its own /api/media route instead,
    so this is here for when you want to hand a direct link to a CDN, an
    e-mail, or a mobile client.
    """
    if not settings.use_supabase:
        return None

    def _do_sign() -> str | None:
        client = get_client()
        result = client.storage.from_(settings.SUPABASE_BUCKET).create_signed_url(path, expires_in)
        if isinstance(result, dict):
            return result.get("signedURL") or result.get("signed_url")
        return None

    try:
        return await asyncio.to_thread(_do_sign)
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not sign URL for %s: %s", path, exc)
        return None


# -- named upload helpers ------------------------------------------------


async def upload_image(user_id: str, generation_id: str, data: bytes, extension: str, content_type: str) -> str:
    path = build_storage_path(IMAGES_FOLDER, user_id, generation_id, extension)
    return await _upload(path, data, content_type)


async def upload_voice(user_id: str, generation_id: str, data: bytes, extension: str, content_type: str) -> str:
    path = build_storage_path(VOICES_FOLDER, user_id, generation_id, extension)
    return await _upload(path, data, content_type)


async def upload_generated_audio(user_id: str, generation_id: str, data: bytes, extension: str = ".wav") -> str:
    path = build_storage_path(GENERATED_AUDIO_FOLDER, user_id, generation_id, extension)
    return await _upload(path, data, "audio/wav")


async def upload_generated_video(user_id: str, generation_id: str, data: bytes, extension: str = ".mp4") -> str:
    path = build_storage_path(GENERATED_VIDEO_FOLDER, user_id, generation_id, extension)
    return await _upload(path, data, "video/mp4")


# ----------------------------------------------------------------------
# Database: generations
# ----------------------------------------------------------------------


async def create_generation(
    *,
    generation_id: str,
    user_id: str | None,
    image_path: str,
    voice_path: str,
    script_text: str,
    duration: int,
    quality: str,
) -> dict[str, Any]:
    """Insert a new generation row in the 'pending' state."""
    record: dict[str, Any] = {
        "id": generation_id,
        "user_id": user_id,
        "image_path": image_path,
        "voice_path": voice_path,
        "script_text": script_text,
        "duration": duration,
        "quality": quality,
        "status": GenerationStatus.PENDING.value,
        "progress": 0,
        "current_stage": "Preparing generation",
        "cosyvoice_job_id": None,
        "soulxflash_job_id": None,
        "generated_audio_path": None,
        "generated_video_path": None,
        "error_message": None,
        "created_at": _now(),
        "updated_at": _now(),
    }

    if not settings.use_supabase:
        return await get_local_store().insert_generation(record)

    def _do_insert() -> dict[str, Any]:
        client = get_client()
        response = client.table(GENERATIONS_TABLE).insert(record).execute()
        return (response.data or [record])[0]

    return await asyncio.to_thread(_do_insert)


async def get_generation(generation_id: str) -> dict[str, Any] | None:
    """Fetch one generation row, or None when it does not exist."""
    if not settings.use_supabase:
        return await get_local_store().get_generation(generation_id)

    def _do_select() -> dict[str, Any] | None:
        client = get_client()
        response = (
            client.table(GENERATIONS_TABLE)
            .select("*")
            .eq("id", generation_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    return await asyncio.to_thread(_do_select)


async def _update(generation_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    changes = {**changes, "updated_at": _now()}

    if not settings.use_supabase:
        return await get_local_store().update_generation(generation_id, changes)

    def _do_update() -> dict[str, Any] | None:
        client = get_client()
        response = (
            client.table(GENERATIONS_TABLE)
            .update(changes)
            .eq("id", generation_id)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    return await asyncio.to_thread(_do_update)


async def update_generation_status(
    generation_id: str,
    status: GenerationStatus | str,
    stage: str | None = None,
    progress: int | None = None,
) -> None:
    """Move a generation to a new status, optionally with stage and progress."""
    changes: dict[str, Any] = {
        "status": status.value if isinstance(status, GenerationStatus) else status
    }
    if stage is not None:
        changes["current_stage"] = stage
    if progress is not None:
        changes["progress"] = max(0, min(100, int(progress)))
    await _update(generation_id, changes)


async def update_generation_progress(generation_id: str, progress: int, stage: str | None = None) -> None:
    """Update only the progress bar (and the stage label when given)."""
    changes: dict[str, Any] = {"progress": max(0, min(100, int(progress)))}
    if stage is not None:
        changes["current_stage"] = stage
    await _update(generation_id, changes)


async def save_cosyvoice_job_id(generation_id: str, job_id: str) -> None:
    await _update(generation_id, {"cosyvoice_job_id": job_id})


async def save_soulxflash_job_id(generation_id: str, job_id: str) -> None:
    await _update(generation_id, {"soulxflash_job_id": job_id})


async def save_generated_audio(generation_id: str, path: str) -> None:
    await _update(generation_id, {"generated_audio_path": path})


async def save_generated_video(generation_id: str, path: str) -> None:
    await _update(generation_id, {"generated_video_path": path})


async def mark_completed(generation_id: str) -> None:
    await _update(
        generation_id,
        {
            "status": GenerationStatus.COMPLETED.value,
            "progress": 100,
            "current_stage": "Completed",
            "error_message": None,
        },
    )


async def mark_failed(generation_id: str, message: str) -> None:
    """Store a user-safe failure message. Full details go to the server log."""
    await _update(
        generation_id,
        {
            "status": GenerationStatus.FAILED.value,
            "current_stage": "Failed",
            "error_message": message,
        },
    )


async def get_user_generations(user_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
    """Return a user's generations, newest first."""
    if not settings.use_supabase:
        return await get_local_store().list_generations(user_id, limit)

    def _do_select() -> list[dict[str, Any]]:
        client = get_client()
        query = client.table(GENERATIONS_TABLE).select("*")
        if user_id:
            query = query.eq("user_id", user_id)
        response = query.order("created_at", desc=True).limit(limit).execute()
        return response.data or []

    return await asyncio.to_thread(_do_select)


async def delete_generation(generation_id: str) -> bool:
    """Delete a generation row and every file that belongs to it."""
    record = await get_generation(generation_id)
    if record is None:
        return False

    for key in ("image_path", "voice_path", "generated_audio_path", "generated_video_path"):
        await delete_file(record.get(key))

    if not settings.use_supabase:
        return await get_local_store().delete_generation(generation_id)

    def _do_delete() -> bool:
        client = get_client()
        client.table(GENERATIONS_TABLE).delete().eq("id", generation_id).execute()
        return True

    return await asyncio.to_thread(_do_delete)


# ----------------------------------------------------------------------
# Database: profiles
# ----------------------------------------------------------------------


async def upsert_profile(user_id: str, email: str, full_name: str | None = None) -> None:
    """Create or update the profile row that belongs to an auth user."""
    if not settings.use_supabase:
        return

    def _do_upsert() -> None:
        client = get_client()
        client.table(PROFILES_TABLE).upsert(
            {
                "id": user_id,
                "email": email,
                "full_name": full_name,
                "updated_at": _now(),
            }
        ).execute()

    await asyncio.to_thread(_do_upsert)
