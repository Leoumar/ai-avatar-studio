"""
CosyVoice service (text + reference voice  ->  generated speech).

    script text  +  sample voice
                 |
             CosyVoice  (on your RunPod Serverless endpoint)
                 |
          generated speech audio

=======================================================================
WORKER-SPECIFIC CONFIGURATION - THIS IS THE FILE YOU EDIT
=======================================================================
CosyVoice has no single official hosted HTTP API. It is deployed as a
RunPod Serverless worker, and the field names in the request and the
response are whatever YOUR worker handler defines.

Nothing here is invented as "the CosyVoice API". `build_payload()` below is
a plain adapter. When you have your worker's schema, change two things:

    1. build_payload()      -> the "input" object your handler expects
    2. the key lists in generate_voice() -> where the audio comes back

Everything else in the project keeps working unchanged.
=======================================================================
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Awaitable, Callable

from app.config import settings
from app.services import runpod_service
from app.utils.media_utils import result_to_bytes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# 1. REQUEST ADAPTER - edit to match your worker
# ---------------------------------------------------------------------

# Keys the worker might use when returning the generated audio.
AUDIO_BASE64_KEYS = ("audio_base64", "audio", "audio_b64", "base64", "wav_base64")
AUDIO_URL_KEYS = ("audio_url", "url", "output_url", "file_url", "s3_url")

# File type your worker produces. Change to ".mp3" if that is what comes back.
OUTPUT_EXTENSION = ".wav"


def build_payload(
    *,
    text: str,
    reference_audio_base64: str,
    reference_audio_format: str,
    target_duration: int | None = None,
) -> dict[str, Any]:
    """
    Build the "input" object sent to the CosyVoice RunPod worker.

    The keys below are placeholders that match a common zero-shot CosyVoice
    handler. Replace them with your worker's real field names.
    """
    payload: dict[str, Any] = {
        # The script to speak.
        "text": text,
        # The reference voice, sent inline as base64 so the worker needs no
        # access to your Supabase bucket.
        "reference_audio": reference_audio_base64,
        "reference_audio_format": reference_audio_format.lstrip("."),
        # CosyVoice zero-shot cloning usually also wants a transcript of the
        # reference clip. Leave empty if your worker runs cross-lingual mode.
        "prompt_text": "",
    }

    # Optional hint only - the real length comes from the synthesised speech.
    if target_duration:
        payload["target_duration"] = target_duration

    return payload


# ---------------------------------------------------------------------
# 2. SERVICE FUNCTION - used by the pipeline
# ---------------------------------------------------------------------


async def generate_voice(
    *,
    text: str,
    reference_audio: bytes,
    reference_format: str = ".wav",
    target_duration: int | None = None,
    on_job_id: Callable[[str], Awaitable[None]] | None = None,
    on_poll: Callable[[str, float], Awaitable[None]] | None = None,
) -> tuple[bytes, str]:
    """
    Run CosyVoice on RunPod and return (audio_bytes, extension).

    Raises PipelineError with a user-friendly message on any failure.
    """
    encoded_reference = base64.b64encode(reference_audio).decode("ascii")

    payload = build_payload(
        text=text,
        reference_audio_base64=encoded_reference,
        reference_audio_format=reference_format,
        target_duration=target_duration,
    )

    logger.info("Starting CosyVoice job (%d characters of script)", len(text))

    output = await runpod_service.run_and_wait(
        settings.COSYVOICE_ENDPOINT_ID,
        payload,
        on_job_id=on_job_id,
        on_poll=on_poll,
    )

    audio_bytes = await result_to_bytes(
        output,
        base64_keys=AUDIO_BASE64_KEYS,
        url_keys=AUDIO_URL_KEYS,
        what="audio",
    )
    logger.info("CosyVoice returned %d bytes of audio", len(audio_bytes))
    return audio_bytes, OUTPUT_EXTENSION
