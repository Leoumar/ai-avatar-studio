"""
Authentication.

The app is built so that Supabase Auth can be switched on without rewriting
anything else:

    AUTH_ENABLED=false  (Phase 1)
        Every request is treated as DEV_USER_ID. The history page works and
        generations are still saved with a user_id column.

    AUTH_ENABLED=true
        The frontend signs in with Supabase and sends the access token as
        "Authorization: Bearer <token>". Every route that touches a generation
        checks that the row belongs to the caller.

Passwords are never stored or logged by this application - Supabase Auth
handles them.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.models.schemas import AuthResponse, LoginRequest, SignupRequest, SimpleMessage
from app.services import supabase_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ----------------------------------------------------------------------
# Dependency used by the other routers
# ----------------------------------------------------------------------


async def get_current_user_id(request: Request) -> str:
    """
    Return the id of the user making this request.

    With auth disabled this is always DEV_USER_ID, which keeps every other
    route identical in both modes.
    """
    if not settings.AUTH_ENABLED:
        return settings.DEV_USER_ID

    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please sign in to continue.",
        )

    token = header.split(" ", 1)[1].strip()

    def _verify() -> str | None:
        client = supabase_service.get_client()
        result = client.auth.get_user(token)
        user = getattr(result, "user", None)
        return getattr(user, "id", None) if user else None

    try:
        user_id = await asyncio.to_thread(_verify)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Token verification failed: %s", exc)
        user_id = None

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
        )
    return user_id


def ensure_owner(record: dict, user_id: str) -> None:
    """
    Make sure a generation belongs to the caller.

    Returns 404 rather than 403 so the API does not confirm that an id exists
    for somebody else.
    """
    if not settings.AUTH_ENABLED:
        return
    if record.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="That video could not be found.")


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------


@router.post("/signup", response_model=AuthResponse)
async def signup(payload: SignupRequest) -> AuthResponse:
    """Create an account with Supabase Auth."""
    _require_auth_enabled()

    def _do_signup():
        client = supabase_service.get_client()
        return client.auth.sign_up(
            {
                "email": payload.email,
                "password": payload.password,
                "options": {"data": {"full_name": payload.full_name}},
            }
        )

    try:
        result = await asyncio.to_thread(_do_signup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Signup failed for %s: %s", payload.email, exc)
        raise HTTPException(status_code=400, detail="Could not create that account.")

    user = getattr(result, "user", None)
    if user is None:
        raise HTTPException(status_code=400, detail="Could not create that account.")

    await supabase_service.upsert_profile(user.id, payload.email, payload.full_name)
    session = getattr(result, "session", None)
    return AuthResponse(
        user_id=user.id,
        email=payload.email,
        access_token=getattr(session, "access_token", None) if session else None,
    )


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest) -> AuthResponse:
    """Sign in and return a Supabase access token."""
    _require_auth_enabled()

    def _do_login():
        client = supabase_service.get_client()
        return client.auth.sign_in_with_password(
            {"email": payload.email, "password": payload.password}
        )

    try:
        result = await asyncio.to_thread(_do_login)
    except Exception as exc:  # noqa: BLE001
        logger.info("Login failed for %s: %s", payload.email, exc)
        raise HTTPException(status_code=401, detail="That email or password is incorrect.")

    user = getattr(result, "user", None)
    session = getattr(result, "session", None)
    if user is None or session is None:
        raise HTTPException(status_code=401, detail="That email or password is incorrect.")

    return AuthResponse(user_id=user.id, email=payload.email, access_token=session.access_token)


@router.post("/logout", response_model=SimpleMessage)
async def logout() -> SimpleMessage:
    """
    Sign out.

    The token lives in the browser, so signing out is mostly a frontend action.
    This endpoint exists so the flow is the same once server-side sessions
    are added.
    """
    return SimpleMessage(message="Signed out.")


def _require_auth_enabled() -> None:
    if not settings.AUTH_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Accounts are turned off. Set AUTH_ENABLED=true in .env to use them.",
        )
