"""
All input validation lives here.

Every check raises ValidationError with a message that is safe (and friendly)
to show to a user. The API turns those into a clean JSON 400 response, so a
Python traceback is never sent to the browser.
"""

from __future__ import annotations

import math
import re

from fastapi import UploadFile

from app.config import settings
from app.utils.file_utils import get_extension, human_size


class ValidationError(Exception):
    """Raised when user input is not acceptable."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class PipelineError(Exception):
    """Raised when the AI pipeline fails. The message is user friendly."""


# ----------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------

_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def estimate_speech_seconds(text: str) -> int:
    """
    Rough estimate of how long a script takes to speak.

    Uses WORDS_PER_MINUTE from the settings (150 by default, a normal
    presenting pace). This is only an estimate - the real duration comes
    from the generated audio.
    """
    words = count_words(text)
    if words == 0:
        return 0
    return max(1, math.ceil(words / settings.WORDS_PER_MINUTE * 60))


# ----------------------------------------------------------------------
# File validation
# ----------------------------------------------------------------------


# Generic types that carry no information - not a reason to reject a file.
_VAGUE_MIME = {"", "application/octet-stream", "binary/octet-stream", "application/binary"}


def _mime_is_acceptable(content_type: str | None, allowed: set[str]) -> bool:
    """
    True when the browser's MIME type does not contradict the extension.

    Unknown or generic types pass: the extension check above is the real gate.
    """
    value = (content_type or "").split(";")[0].strip().lower()
    if value in _VAGUE_MIME:
        return True
    return value in allowed


def validate_image(file: UploadFile, data: bytes) -> None:
    """Check an uploaded avatar image."""
    if file is None or not data:
        raise ValidationError("Please upload an avatar image.", field="image")

    extension = get_extension(file.filename)
    if extension not in settings.ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(settings.ALLOWED_IMAGE_EXTENSIONS))
        raise ValidationError(
            f"The selected image format is not supported. Use {allowed}.",
            field="image",
        )

    # The extension decides. The MIME type is only a sanity check, because
    # browsers and tools report it inconsistently (an .m4a often arrives as
    # application/octet-stream), so a vague type is accepted.
    if not _mime_is_acceptable(file.content_type, settings.ALLOWED_IMAGE_MIME):
        raise ValidationError(
            "That file does not look like an image. Upload a JPG, PNG or WEBP.",
            field="image",
        )

    if len(data) > settings.MAX_IMAGE_BYTES:
        raise ValidationError(
            f"The image is too large ({human_size(len(data))}). "
            f"The limit is {settings.MAX_IMAGE_MB} MB.",
            field="image",
        )


def validate_voice(file: UploadFile, data: bytes) -> None:
    """Check an uploaded reference voice sample."""
    if file is None or not data:
        raise ValidationError("Please upload a voice sample.", field="voice")

    extension = get_extension(file.filename)
    if extension not in settings.ALLOWED_AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(settings.ALLOWED_AUDIO_EXTENSIONS))
        raise ValidationError(
            f"The selected audio format is not supported. Use {allowed}.",
            field="voice",
        )

    if not _mime_is_acceptable(file.content_type, settings.ALLOWED_AUDIO_MIME):
        raise ValidationError(
            "That file does not look like audio. Upload a WAV, MP3 or M4A file.",
            field="voice",
        )

    if len(data) > settings.MAX_VOICE_BYTES:
        raise ValidationError(
            f"The audio file is too large ({human_size(len(data))}). "
            f"The limit is {settings.MAX_VOICE_MB} MB.",
            field="voice",
        )


# ----------------------------------------------------------------------
# Field validation
# ----------------------------------------------------------------------


def validate_script(text: str) -> str:
    """Check the script and return it trimmed."""
    text = (text or "").strip()
    if not text:
        raise ValidationError("Please enter a script for your avatar to say.", field="text")

    if len(text) > settings.MAX_SCRIPT_CHARS:
        raise ValidationError(
            f"Your script is {len(text)} characters. "
            f"The limit is {settings.MAX_SCRIPT_CHARS} characters.",
            field="text",
        )
    return text


def validate_duration(duration: int) -> int:
    """Check the requested video duration, in seconds."""
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        raise ValidationError("Please choose a valid video duration.", field="duration")

    if duration <= 0:
        raise ValidationError("The video duration must be greater than zero.", field="duration")

    if duration < settings.MIN_DURATION_SECONDS:
        raise ValidationError(
            f"The shortest video is {settings.MIN_DURATION_SECONDS} seconds.",
            field="duration",
        )

    if duration > settings.MAX_DURATION_SECONDS:
        minutes = settings.MAX_DURATION_SECONDS / 60
        raise ValidationError(
            f"The longest video is {settings.MAX_DURATION_SECONDS} seconds "
            f"({minutes:g} minutes). Longer jobs are blocked to avoid runaway GPU cost.",
            field="duration",
        )
    return duration


def validate_quality(quality: str) -> str:
    """Check the requested resolution against the configured options."""
    quality = (quality or "").strip().lower()
    if quality not in [q.lower() for q in settings.ALLOWED_QUALITIES]:
        allowed = ", ".join(settings.ALLOWED_QUALITIES)
        raise ValidationError(
            f"'{quality}' is not an available quality. Choose one of: {allowed}.",
            field="quality",
        )
    return quality


def validate_consent(consent: bool) -> None:
    """The consent checkbox is mandatory before any generation starts."""
    if not consent:
        raise ValidationError(
            "Please confirm that you have permission to use this image and voice.",
            field="consent",
        )


def check_script_fits_duration(text: str, duration: int) -> str | None:
    """
    Compare the estimated speech length with the requested video duration.

    Returns a warning string when they are far apart, or None when the script
    is a reasonable fit. This is a warning, not an error - the script is never
    repeated or trimmed automatically.
    """
    estimated = estimate_speech_seconds(text)
    if estimated == 0:
        return None

    # Allow 25% slack in either direction before we say anything.
    if estimated < duration * 0.75:
        return (
            f"Your script is estimated at {estimated} seconds, "
            f"but you selected a {duration}-second video. "
            "The video may end with silence."
        )
    if estimated > duration * 1.25:
        return (
            f"Your script is estimated at {estimated} seconds, "
            f"but you selected a {duration}-second video. "
            "The speech may be cut off."
        )
    return None
