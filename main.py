"""
AI Avatar Studio - application entry point.

Run locally with:

    uvicorn main:app --reload

Then open http://127.0.0.1:8000
Interactive API docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import auth, avatar, generation, history
from app.services import supabase_service
from app.utils.validation import PipelineError, ValidationError

# ----------------------------------------------------------------------
# Logging: detailed on the server, never shown to the user.
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger("ai-avatar-studio")


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Upload an image and a voice sample, enter a script, and generate a "
        "talking avatar video.\n\n"
        "Speech comes from CosyVoice and the video from SoulX-Flash Lite, both "
        "running on RunPod Serverless. Set MOCK_MODE=true to exercise the whole "
        "app without a GPU."
    ),
    version="1.0.0",
)

# ----------------------------------------------------------------------
# CORS
# ----------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials="*" not in settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Static files (CSS, JS, assets)
# ----------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR)), name="static")

# ----------------------------------------------------------------------
# Routers
# ----------------------------------------------------------------------
app.include_router(avatar.router)
app.include_router(generation.router)
app.include_router(history.router)
app.include_router(auth.router)


# ----------------------------------------------------------------------
# Error handling
#
# Users see a short sentence they can act on. The full detail goes to the log.
# ----------------------------------------------------------------------


@app.exception_handler(ValidationError)
async def handle_validation_error(request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": exc.message, "field": exc.field},
    )


@app.exception_handler(PipelineError)
async def handle_pipeline_error(request: Request, exc: PipelineError) -> JSONResponse:
    logger.warning("Pipeline error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def handle_request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Turn FastAPI's field errors into one readable sentence."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", []) if part != "body") or "request"
    logger.info("Bad request on %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=400,
        content={"detail": f"Something is missing or invalid in your request ({field})."},
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log the traceback, show the user a plain message."""
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our side. Please try again."},
    )


# ----------------------------------------------------------------------
# Startup
# ----------------------------------------------------------------------


@app.on_event("startup")
async def on_startup() -> None:
    backend = supabase_service.backend_name()
    logger.info("%s starting", settings.APP_NAME)
    logger.info("  mock mode       : %s", settings.MOCK_MODE)
    logger.info("  storage backend : %s", backend)
    logger.info("  auth enabled    : %s", settings.AUTH_ENABLED)
    logger.info("  runpod ready    : %s", settings.runpod_configured)

    if backend == "local":
        logger.warning(
            "Using the LOCAL storage fallback (./storage). Fine for development; "
            "add your Supabase keys to .env when you are ready."
        )
    if not settings.MOCK_MODE and not settings.runpod_configured:
        logger.warning(
            "MOCK_MODE is false but RunPod is not fully configured. "
            "Generations will fail until the endpoint ids are set."
        )
