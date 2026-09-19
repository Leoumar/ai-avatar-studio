"""
Application configuration.

Everything configurable lives here. Values are read from the environment
(loaded from the .env file), so no secret is ever hard-coded in the code.

Usage anywhere in the project:

    from app.config import settings
    print(settings.MOCK_MODE)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder that contains main.py
BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env into the process environment (does not overwrite real env vars).
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    """Read a boolean env var. 'true', '1', 'yes', 'on' all count as True."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    """Read an integer env var, falling back to the default if it is invalid."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _secret(name: str, default: str = "") -> str:
    """
    Read a secret-ish value.

    Values still holding an .env.example placeholder (like 'your_runpod_api_key')
    are treated as not configured, so the app reports its real state instead of
    pretending the keys are set.
    """
    value = os.getenv(name, default).strip()
    if value.lower().startswith("your_"):
        return ""
    return value


def _list(name: str, default: str) -> list[str]:
    """Read a comma separated env var into a list of clean strings."""
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    """All application settings in one object."""

    # --- Paths ---------------------------------------------------
    BASE_DIR: Path = BASE_DIR
    STATIC_DIR: Path = BASE_DIR / "static"
    TEMPLATES_DIR: Path = BASE_DIR / "templates"
    # Used by the local storage fallback and for temporary processing files.
    STORAGE_DIR: Path = BASE_DIR / "storage"
    TEMP_DIR: Path = BASE_DIR / "storage" / "tmp"
    LOCAL_DB_FILE: Path = BASE_DIR / "storage" / "local_db.json"

    # --- Application ---------------------------------------------
    APP_NAME: str = os.getenv("APP_NAME", "AI Avatar Studio")
    APP_TAGLINE: str = "Turn any image into a talking AI avatar"
    DEBUG: bool = _bool("DEBUG", True)

    MOCK_MODE: bool = _bool("MOCK_MODE", True)
    MOCK_DURATION_SECONDS: int = _int("MOCK_DURATION_SECONDS", 12)

    # --- Storage / database --------------------------------------
    # "auto" | "supabase" | "local"
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "auto").strip().lower()

    SUPABASE_URL: str = _secret("SUPABASE_URL")
    SUPABASE_KEY: str = _secret("SUPABASE_KEY")
    SUPABASE_SERVICE_KEY: str = _secret("SUPABASE_SERVICE_KEY")
    SUPABASE_BUCKET: str = os.getenv("SUPABASE_BUCKET", "avatar-files").strip()

    # --- Auth -----------------------------------------------------
    AUTH_ENABLED: bool = _bool("AUTH_ENABLED", False)
    DEV_USER_ID: str = os.getenv(
        "DEV_USER_ID", "00000000-0000-0000-0000-000000000001"
    ).strip()

    # --- RunPod ---------------------------------------------------
    RUNPOD_API_KEY: str = _secret("RUNPOD_API_KEY")
    RUNPOD_BASE_URL: str = os.getenv("RUNPOD_BASE_URL", "https://api.runpod.ai/v2").rstrip("/")
    COSYVOICE_ENDPOINT_ID: str = _secret("COSYVOICE_ENDPOINT_ID")
    SOULXFLASH_ENDPOINT_ID: str = _secret("SOULXFLASH_ENDPOINT_ID")
    RUNPOD_POLL_INTERVAL: int = _int("RUNPOD_POLL_INTERVAL", 3)
    RUNPOD_TIMEOUT_SECONDS: int = _int("RUNPOD_TIMEOUT_SECONDS", 1800)

    # --- Validation limits ----------------------------------------
    MAX_IMAGE_MB: int = _int("MAX_IMAGE_MB", 10)
    MAX_VOICE_MB: int = _int("MAX_VOICE_MB", 25)
    MAX_SCRIPT_CHARS: int = _int("MAX_SCRIPT_CHARS", 5000)
    MIN_DURATION_SECONDS: int = _int("MIN_DURATION_SECONDS", 5)
    MAX_DURATION_SECONDS: int = _int("MAX_DURATION_SECONDS", 300)
    WORDS_PER_MINUTE: int = _int("WORDS_PER_MINUTE", 150)

    ALLOWED_IMAGE_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp"}
    ALLOWED_IMAGE_MIME: set[str] = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
    ALLOWED_AUDIO_EXTENSIONS: set[str] = {".wav", ".mp3", ".m4a"}
    ALLOWED_AUDIO_MIME: set[str] = {
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/x-m4a",
        "audio/m4a",
        "video/mp4",  # some browsers report .m4a like this
    }

    ALLOWED_QUALITIES: list[str] = _list("ALLOWED_QUALITIES", "480p,720p,1080p")

    # --- CORS ------------------------------------------------------
    CORS_ORIGINS: list[str] = _list("CORS_ORIGINS", "*")

    # --- Derived helpers -------------------------------------------
    @property
    def MAX_IMAGE_BYTES(self) -> int:
        return self.MAX_IMAGE_MB * 1024 * 1024

    @property
    def MAX_VOICE_BYTES(self) -> int:
        return self.MAX_VOICE_MB * 1024 * 1024

    @property
    def supabase_configured(self) -> bool:
        """True only when we have enough information to talk to Supabase."""
        return bool(self.SUPABASE_URL and (self.SUPABASE_SERVICE_KEY or self.SUPABASE_KEY))

    @property
    def use_supabase(self) -> bool:
        """Decide which storage/database backend the app should use."""
        if self.STORAGE_BACKEND == "local":
            return False
        if self.STORAGE_BACKEND == "supabase":
            return True
        # "auto"
        return self.supabase_configured

    @property
    def runpod_configured(self) -> bool:
        return bool(
            self.RUNPOD_API_KEY
            and self.COSYVOICE_ENDPOINT_ID
            and self.SOULXFLASH_ENDPOINT_ID
        )


settings = Settings()

# Make sure the local folders exist so nothing fails at runtime.
settings.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
