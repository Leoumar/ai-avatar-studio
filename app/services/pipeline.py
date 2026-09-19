"""
The generation pipeline.

This is the orchestrator that runs in the background after POST /api/generate
returns. It moves a generation through its stages, writes progress to the
database, and stores the finished video.

Two modes:

    MOCK_MODE=true   -> no GPU, no RunPod. Stages are simulated on a timer and
                        a bundled sample video is returned. Use this to build
                        and test the whole app for free.

    MOCK_MODE=false  -> the real flow:
                        CosyVoice  -> speech
                        SoulX-Flash Lite -> talking avatar video

--------------------------------------------------------------------------
About the in-memory progress cache at the bottom of this file:
it makes status polling cheap, but it only exists inside ONE uvicorn worker
process. It is a development convenience, not a production job queue. For
production, move job state to Redis + a real worker (Celery, RQ, Dramatiq,
or RunPod webhooks) and run this pipeline outside the web process.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.schemas import GenerationStatus, Stage
from app.services import cosyvoice_service, soulxflash_service, supabase_service
from app.utils.file_utils import get_extension
from app.utils.validation import PipelineError

logger = logging.getLogger(__name__)

SAMPLE_VIDEO = settings.STATIC_DIR / "assets" / "sample_output.mp4"


# ----------------------------------------------------------------------
# In-memory progress cache (development only)
# ----------------------------------------------------------------------


@dataclass
class JobProgress:
    status: str
    progress: int
    stage: str
    error_message: str | None = None


_progress: dict[str, JobProgress] = {}


def get_cached_progress(generation_id: str) -> JobProgress | None:
    return _progress.get(generation_id)


def forget(generation_id: str) -> None:
    _progress.pop(generation_id, None)


async def _set_progress(
    generation_id: str,
    status: GenerationStatus,
    progress: int,
    stage: Stage | str,
    error_message: str | None = None,
) -> None:
    """Update both the in-memory cache and the database row."""
    stage_text = stage.value if isinstance(stage, Stage) else str(stage)
    _progress[generation_id] = JobProgress(
        status=status.value, progress=progress, stage=stage_text, error_message=error_message
    )
    await supabase_service.update_generation_status(
        generation_id, status, stage=stage_text, progress=progress
    )


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


async def run_generation(generation_id: str) -> None:
    """
    Run one generation from start to finish.

    Called as a FastAPI background task, so it must never raise: every failure
    is caught, logged in full, and stored as a short friendly message.
    """
    try:
        record = await supabase_service.get_generation(generation_id)
        if record is None:
            logger.error("Generation %s disappeared before processing", generation_id)
            return

        if settings.MOCK_MODE:
            await _run_mock(record)
        else:
            await _run_real(record)

    except PipelineError as exc:
        logger.warning("Generation %s failed: %s", generation_id, exc)
        await _fail(generation_id, str(exc))

    except Exception as exc:  # noqa: BLE001 - last line of defence
        logger.exception("Unexpected error in generation %s", generation_id)
        await _fail(generation_id, "Video generation failed. Please try again.")


async def _fail(generation_id: str, message: str) -> None:
    _progress[generation_id] = JobProgress(
        status=GenerationStatus.FAILED.value, progress=100, stage=Stage.FAILED.value,
        error_message=message,
    )
    await supabase_service.mark_failed(generation_id, message)


# ----------------------------------------------------------------------
# Mock pipeline
# ----------------------------------------------------------------------


async def _run_mock(record: dict[str, Any]) -> None:
    """
    Simulate a generation without touching a GPU.

    Everything else is real: the database is updated, files are written to
    storage, and the finished video is served from storage exactly as it will
    be once the AI models are connected.
    """
    generation_id = record["id"]
    user_id = record.get("user_id")
    total = max(4, settings.MOCK_DURATION_SECONDS)

    steps: list[tuple[GenerationStatus, Stage, int]] = [
        (GenerationStatus.UPLOADING, Stage.UPLOADING_FILES, 10),
        (GenerationStatus.PROCESSING, Stage.PREPARING, 20),
        (GenerationStatus.GENERATING_VOICE, Stage.GENERATING_SPEECH, 40),
        (GenerationStatus.GENERATING_AVATAR, Stage.PROCESSING_AVATAR, 55),
        (GenerationStatus.GENERATING_AVATAR, Stage.LIP_SYNC, 70),
        (GenerationStatus.GENERATING_AVATAR, Stage.EXPRESSIONS, 80),
        (GenerationStatus.PROCESSING, Stage.RENDERING, 90),
        (GenerationStatus.PROCESSING, Stage.FINALIZING, 96),
    ]
    pause = total / len(steps)

    for status, stage, progress in steps:
        await _set_progress(generation_id, status, progress, stage)
        await asyncio.sleep(pause)

    # "Generated" audio: reuse the uploaded sample so the audio player works.
    try:
        voice_bytes = await supabase_service.download_file(record["voice_path"])
        audio_path = await supabase_service.upload_generated_audio(
            user_id or "anonymous",
            generation_id,
            voice_bytes,
            get_extension(record["voice_path"], ".wav"),
        )
        await supabase_service.save_generated_audio(generation_id, audio_path)
    except Exception as exc:  # noqa: BLE001 - not important in mock mode
        logger.warning("Mock mode could not copy the sample audio: %s", exc)

    # "Generated" video: the bundled placeholder MP4.
    video_bytes = _read_sample_video()
    video_path = await supabase_service.upload_generated_video(
        user_id or "anonymous", generation_id, video_bytes
    )
    await supabase_service.save_generated_video(generation_id, video_path)

    await _set_progress(generation_id, GenerationStatus.COMPLETED, 100, Stage.COMPLETED)
    await supabase_service.mark_completed(generation_id)
    logger.info("Mock generation %s completed", generation_id)


def _read_sample_video() -> bytes:
    if not SAMPLE_VIDEO.exists():
        raise PipelineError(
            "Mock mode needs a placeholder video at static/assets/sample_output.mp4. "
            "Add any short MP4 with that name, or set MOCK_MODE=false."
        )
    return SAMPLE_VIDEO.read_bytes()


# ----------------------------------------------------------------------
# Real pipeline
# ----------------------------------------------------------------------


async def _run_real(record: dict[str, Any]) -> None:
    """
    The real flow.

        1. read the uploaded image and voice back from storage
        2. CosyVoice        : script + voice sample -> speech
        3. store the speech
        4. SoulX-Flash Lite : image + speech        -> talking avatar video
        5. store the video and mark the job completed
    """
    generation_id = record["id"]
    user_id = record.get("user_id") or "anonymous"

    if not settings.runpod_configured:
        raise PipelineError(
            "The AI pipeline is not configured. Add your RunPod API key and endpoint "
            "ids to .env, or set MOCK_MODE=true."
        )

    # --- 1. load inputs ------------------------------------------------
    await _set_progress(generation_id, GenerationStatus.PROCESSING, 8, Stage.PREPARING)
    image_bytes = await supabase_service.download_file(record["image_path"])
    voice_bytes = await supabase_service.download_file(record["voice_path"])
    image_ext = get_extension(record["image_path"], ".png")
    voice_ext = get_extension(record["voice_path"], ".wav")

    # --- 2. CosyVoice --------------------------------------------------
    await _set_progress(
        generation_id, GenerationStatus.GENERATING_VOICE, 20, Stage.GENERATING_SPEECH
    )

    async def save_voice_job(job_id: str) -> None:
        await supabase_service.save_cosyvoice_job_id(generation_id, job_id)

    async def voice_poll(status: str, elapsed: float) -> None:
        # Creep from 20% to 45% while we wait, so the bar keeps moving.
        progress = min(45, 20 + int(elapsed / 4))
        await _set_progress(
            generation_id, GenerationStatus.GENERATING_VOICE, progress, Stage.GENERATING_SPEECH
        )

    audio_bytes, audio_ext = await cosyvoice_service.generate_voice(
        text=record["script_text"],
        reference_audio=voice_bytes,
        reference_format=voice_ext,
        target_duration=record.get("duration"),
        on_job_id=save_voice_job,
        on_poll=voice_poll,
    )

    # --- 3. store the speech -------------------------------------------
    audio_path = await supabase_service.upload_generated_audio(
        user_id, generation_id, audio_bytes, audio_ext
    )
    await supabase_service.save_generated_audio(generation_id, audio_path)

    # --- 4. SoulX-Flash Lite -------------------------------------------
    await _set_progress(
        generation_id, GenerationStatus.GENERATING_AVATAR, 50, Stage.PROCESSING_AVATAR
    )

    async def save_avatar_job(job_id: str) -> None:
        await supabase_service.save_soulxflash_job_id(generation_id, job_id)

    async def avatar_poll(status: str, elapsed: float) -> None:
        # 50% -> 92% while the avatar renders. The stage label changes as we go
        # so the user sees lip-sync / expressions / rendering.
        progress = min(92, 50 + int(elapsed / 3))
        if progress < 62:
            stage = Stage.LIP_SYNC
        elif progress < 76:
            stage = Stage.EXPRESSIONS
        else:
            stage = Stage.RENDERING
        await _set_progress(
            generation_id, GenerationStatus.GENERATING_AVATAR, progress, stage
        )

    video_bytes, video_ext = await soulxflash_service.generate_avatar_video(
        image_bytes=image_bytes,
        image_format=image_ext,
        audio_bytes=audio_bytes,
        audio_format=audio_ext,
        quality=record["quality"],
        on_job_id=save_avatar_job,
        on_poll=avatar_poll,
    )

    # --- 5. store the video and finish ---------------------------------
    await _set_progress(generation_id, GenerationStatus.PROCESSING, 95, Stage.FINALIZING)
    video_path = await supabase_service.upload_generated_video(
        user_id, generation_id, video_bytes, video_ext
    )
    await supabase_service.save_generated_video(generation_id, video_path)

    await _set_progress(generation_id, GenerationStatus.COMPLETED, 100, Stage.COMPLETED)
    await supabase_service.mark_completed(generation_id)
    logger.info("Generation %s completed", generation_id)
