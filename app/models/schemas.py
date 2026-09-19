"""
Pydantic models (schemas).

These describe the shape of the JSON that the API returns, and they are what
FastAPI uses to build the automatic documentation at /docs.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GenerationStatus(str, Enum):
    """Lifecycle of one avatar generation job."""

    PENDING = "pending"
    UPLOADING = "uploading"
    GENERATING_VOICE = "generating_voice"
    GENERATING_AVATAR = "generating_avatar"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Stage(str, Enum):
    """Human readable sub-steps shown on the progress screen."""

    UPLOADING_FILES = "Uploading files"
    PREPARING = "Preparing generation"
    GENERATING_SPEECH = "Generating speech"
    PROCESSING_AVATAR = "Processing avatar"
    LIP_SYNC = "Applying lip-sync"
    EXPRESSIONS = "Generating facial expressions"
    RENDERING = "Rendering video"
    FINALIZING = "Finalizing video"
    COMPLETED = "Completed"
    FAILED = "Failed"


class GenerationCreatedResponse(BaseModel):
    """Returned immediately by POST /api/generate."""

    generation_id: str = Field(..., examples=["550e8400-e29b-41d4-a716-446655440000"])
    status: GenerationStatus = GenerationStatus.PENDING


class StatusResponse(BaseModel):
    """Returned by GET /api/status/{generation_id} while polling."""

    generation_id: str
    status: GenerationStatus
    progress: int = Field(0, ge=0, le=100)
    stage: str
    error_message: Optional[str] = None
    video_url: Optional[str] = None


class GenerationOut(BaseModel):
    """A full generation record, used by history and detail endpoints."""

    id: str
    user_id: Optional[str] = None
    script_text: str
    duration: int
    quality: str
    status: GenerationStatus
    progress: int = 0
    current_stage: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class HistoryResponse(BaseModel):
    """Returned by GET /api/history."""

    count: int
    generations: list[GenerationOut]


class SimpleMessage(BaseModel):
    """Generic {"message": "..."} response."""

    message: str


class HealthResponse(BaseModel):
    """Returned by GET /api/health - handy for checking configuration."""

    status: str
    mock_mode: bool
    storage_backend: str
    auth_enabled: bool
    runpod_configured: bool


# --- Authentication schemas (used when AUTH_ENABLED=true) ----------


class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    user_id: str
    email: str
    access_token: Optional[str] = None
