"""
Generation API.

    POST   /api/generate                 start a job, returns a generation_id
    GET    /api/status/{id}              progress for the polling loop
    GET    /api/video/{id}               information about a finished video
    GET    /api/media/{id}/{kind}        stream image / audio / video inline
    GET    /api/download/{id}            download the MP4
    GET    /api/generation/{id}          the full record
    DELETE /api/generation/{id}          delete a record and its files

The POST returns immediately. The heavy work runs in a background task
(app/services/pipeline.py) and the browser polls /api/status.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile

from app.config import settings
from app.models.schemas import (
    GenerationCreatedResponse,
    GenerationOut,
    GenerationStatus,
    SimpleMessage,
    StatusResponse,
)
from app.routes.auth import ensure_owner, get_current_user_id
from app.services import pipeline, supabase_service
from app.utils.file_utils import get_extension, guess_content_type, new_id
from app.utils.validation import (
    ValidationError,
    validate_consent,
    validate_duration,
    validate_image,
    validate_quality,
    validate_script,
    validate_voice,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generation"])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _as_bool(value: str | bool | None) -> bool:
    """Form fields arrive as strings, so 'true' / 'on' / '1' all mean True."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _to_out(record: dict[str, Any]) -> GenerationOut:
    """Turn a database row into the public API shape."""
    generation_id = record["id"]
    return GenerationOut(
        id=generation_id,
        user_id=record.get("user_id"),
        script_text=record.get("script_text", ""),
        duration=record.get("duration", 0),
        quality=record.get("quality", ""),
        status=record.get("status", GenerationStatus.PENDING.value),
        progress=record.get("progress", 0) or 0,
        current_stage=record.get("current_stage"),
        image_url=f"/api/media/{generation_id}/image" if record.get("image_path") else None,
        audio_url=f"/api/media/{generation_id}/audio" if record.get("generated_audio_path") else None,
        video_url=f"/api/media/{generation_id}/video" if record.get("generated_video_path") else None,
        error_message=record.get("error_message"),
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
    )


async def _load_owned(generation_id: str, user_id: str) -> dict[str, Any]:
    """Fetch a generation and check that it belongs to the caller."""
    record = await supabase_service.get_generation(generation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="That video could not be found.")
    ensure_owner(record, user_id)
    return record


# ----------------------------------------------------------------------
# POST /api/generate
# ----------------------------------------------------------------------


@router.post("/generate", response_model=GenerationCreatedResponse, status_code=202)
async def start_generation(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(..., description="Avatar image: JPG, PNG or WEBP"),
    voice: UploadFile = File(..., description="Reference voice: WAV, MP3 or M4A"),
    text: str = Form(..., description="The script the avatar will speak"),
    duration: int = Form(..., description="Requested video duration in seconds"),
    quality: str = Form(..., description="480p, 720p or 1080p"),
    consent: str = Form("false", description="Must be true: permission to use the image and voice"),
    user_id: str = Depends(get_current_user_id),
) -> GenerationCreatedResponse:
    """
    Validate the request, store the uploads, and queue the AI pipeline.

    Returns straight away with a generation_id. Nothing is generated inside
    this request - a long-running HTTP request would time out.
    """
    # 1-6. Validate everything before touching storage.
    validate_consent(_as_bool(consent))

    image_bytes = await image.read()
    voice_bytes = await voice.read()

    validate_image(image, image_bytes)
    validate_voice(voice, voice_bytes)
    script = validate_script(text)
    seconds = validate_duration(duration)
    resolution = validate_quality(quality)

    generation_id = new_id()
    image_ext = get_extension(image.filename, ".png")
    voice_ext = get_extension(voice.filename, ".wav")

    # 7-8. Upload the two source files.
    try:
        image_path = await supabase_service.upload_image(
            user_id,
            generation_id,
            image_bytes,
            image_ext,
            image.content_type or guess_content_type(image.filename or "a.png"),
        )
        voice_path = await supabase_service.upload_voice(
            user_id,
            generation_id,
            voice_bytes,
            voice_ext,
            voice.content_type or guess_content_type(voice.filename or "a.wav"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload failed for generation %s", generation_id)
        raise HTTPException(
            status_code=502,
            detail="Your files could not be uploaded. Please try again.",
        ) from exc

    # 9. Create the database record.
    await supabase_service.create_generation(
        generation_id=generation_id,
        user_id=user_id,
        image_path=image_path,
        voice_path=voice_path,
        script_text=script,
        duration=seconds,
        quality=resolution,
    )

    # 10. Start the pipeline in the background and return immediately.
    background_tasks.add_task(pipeline.run_generation, generation_id)
    logger.info("Queued generation %s (mock_mode=%s)", generation_id, settings.MOCK_MODE)

    return GenerationCreatedResponse(
        generation_id=generation_id, status=GenerationStatus.PENDING
    )


# ----------------------------------------------------------------------
# GET /api/status/{generation_id}
# ----------------------------------------------------------------------


@router.get("/status/{generation_id}", response_model=StatusResponse)
async def get_status(
    generation_id: str, user_id: str = Depends(get_current_user_id)
) -> StatusResponse:
    """
    Current progress of a generation. The frontend polls this every 2 seconds.

    Reads the in-memory cache first (cheap) and falls back to the database,
    which is also what happens after a server restart.
    """
    cached = pipeline.get_cached_progress(generation_id)
    record = await _load_owned(generation_id, user_id)

    status_value = cached.status if cached else record.get("status", GenerationStatus.PENDING.value)
    progress = cached.progress if cached else (record.get("progress") or 0)
    stage = cached.stage if cached else (record.get("current_stage") or "Preparing generation")
    error_message = record.get("error_message") or (cached.error_message if cached else None)

    video_url = (
        f"/api/media/{generation_id}/video" if record.get("generated_video_path") else None
    )

    return StatusResponse(
        generation_id=generation_id,
        status=status_value,
        progress=progress,
        stage=stage,
        error_message=error_message,
        video_url=video_url,
    )


# ----------------------------------------------------------------------
# GET /api/video/{generation_id} and /api/generation/{generation_id}
# ----------------------------------------------------------------------


@router.get("/video/{generation_id}", response_model=GenerationOut)
async def get_video(
    generation_id: str, user_id: str = Depends(get_current_user_id)
) -> GenerationOut:
    """Information about a finished video: urls, duration, quality, dates."""
    record = await _load_owned(generation_id, user_id)
    if not record.get("generated_video_path"):
        raise HTTPException(status_code=409, detail="That video is not ready yet.")
    return _to_out(record)


@router.get("/generation/{generation_id}", response_model=GenerationOut)
async def get_generation_detail(
    generation_id: str, user_id: str = Depends(get_current_user_id)
) -> GenerationOut:
    """The full record for one generation, ready or not."""
    record = await _load_owned(generation_id, user_id)
    return _to_out(record)


# ----------------------------------------------------------------------
# Media streaming and download
# ----------------------------------------------------------------------

_MEDIA_KINDS = {
    "image": ("image_path", "image/jpeg"),
    "voice": ("voice_path", "audio/mpeg"),
    "audio": ("generated_audio_path", "audio/wav"),
    "video": ("generated_video_path", "video/mp4"),
}


@router.get("/media/{generation_id}/{kind}")
async def get_media(
    generation_id: str, kind: str, user_id: str = Depends(get_current_user_id)
) -> Response:
    """
    Stream a stored file through the backend.

    Serving media this way (instead of exposing storage URLs) keeps the bucket
    private and lets the ownership check run on every request.
    """
    if kind not in _MEDIA_KINDS:
        raise HTTPException(status_code=404, detail="Unknown media type.")

    record = await _load_owned(generation_id, user_id)
    field, default_type = _MEDIA_KINDS[kind]
    path = record.get(field)
    if not path:
        raise HTTPException(status_code=404, detail="That file is not available yet.")

    try:
        data = await supabase_service.download_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="That file is no longer in storage.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not read %s from storage", path)
        raise HTTPException(status_code=502, detail="That file could not be loaded.") from exc

    return Response(
        content=data,
        media_type=guess_content_type(path, default_type),
        headers={"Cache-Control": "private, max-age=600"},
    )


@router.get("/download/{generation_id}")
async def download_video(
    generation_id: str, user_id: str = Depends(get_current_user_id)
) -> Response:
    """Download the finished MP4 as a file attachment."""
    record = await _load_owned(generation_id, user_id)
    path = record.get("generated_video_path")
    if not path:
        raise HTTPException(status_code=409, detail="That video is not ready yet.")

    try:
        data = await supabase_service.download_file(path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not read video %s", path)
        raise HTTPException(status_code=502, detail="The video could not be downloaded.") from exc

    filename = f"avatar-{generation_id[:8]}.mp4"
    return Response(
        content=data,
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ----------------------------------------------------------------------
# DELETE /api/generation/{generation_id}
# ----------------------------------------------------------------------


@router.delete("/generation/{generation_id}", response_model=SimpleMessage)
async def delete_generation(
    generation_id: str, user_id: str = Depends(get_current_user_id)
) -> SimpleMessage:
    """Delete a generation record together with every file it owns."""
    await _load_owned(generation_id, user_id)
    await supabase_service.delete_generation(generation_id)
    pipeline.forget(generation_id)
    return SimpleMessage(message="Video deleted.")
