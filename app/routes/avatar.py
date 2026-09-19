"""
Website pages.

These routes only render HTML templates. All data is fetched by the browser
from the /api routes, which keeps the pages simple and the API reusable.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models.schemas import HealthResponse
from app.services import supabase_service

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(settings.TEMPLATES_DIR))


def _base_context(request: Request) -> dict:
    """
    Values every page needs.

    Only non-secret configuration is exposed here. The Supabase service key
    and the RunPod key are never passed to a template.
    """
    return {
        "request": request,
        "app_name": settings.APP_NAME,
        "tagline": settings.APP_TAGLINE,
        "mock_mode": settings.MOCK_MODE,
        "auth_enabled": settings.AUTH_ENABLED,
        "qualities": settings.ALLOWED_QUALITIES,
        "max_script_chars": settings.MAX_SCRIPT_CHARS,
        "max_image_mb": settings.MAX_IMAGE_MB,
        "max_voice_mb": settings.MAX_VOICE_MB,
        "min_duration": settings.MIN_DURATION_SECONDS,
        "max_duration": settings.MAX_DURATION_SECONDS,
        "words_per_minute": settings.WORDS_PER_MINUTE,
    }


@router.get("/", response_class=HTMLResponse, summary="Home page and generator")
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {**_base_context(request), "page": "home"})


@router.get("/history", response_class=HTMLResponse, summary="Past generations")
async def history_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "history.html", {**_base_context(request), "page": "history"})


@router.get("/about", response_class=HTMLResponse, summary="About page")
async def about_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "about.html", {**_base_context(request), "page": "about"})


@router.get("/login", response_class=HTMLResponse, summary="Sign in")
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {**_base_context(request), "page": "login"})


@router.get("/signup", response_class=HTMLResponse, summary="Create an account")
async def signup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "signup.html", {**_base_context(request), "page": "signup"})


@router.get("/api/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """
    Quick check that the app is running and how it is configured.

    Useful right after setup: it tells you whether you are on Supabase or the
    local fallback, and whether RunPod is wired up.
    """
    return HealthResponse(
        status="ok",
        mock_mode=settings.MOCK_MODE,
        storage_backend=supabase_service.backend_name(),
        auth_enabled=settings.AUTH_ENABLED,
        runpod_configured=settings.runpod_configured,
    )
