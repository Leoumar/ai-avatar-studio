"""
History API.

    GET /api/history  -> the signed-in user's previous generations, newest first

The history page reads only from here, so nothing on that page is hard-coded.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.models.schemas import HistoryResponse
from app.routes.auth import get_current_user_id
from app.routes.generation import _to_out
from app.services import supabase_service

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    limit: int = Query(50, ge=1, le=200, description="How many records to return"),
    user_id: str = Depends(get_current_user_id),
) -> HistoryResponse:
    """Return past generations for the current user."""
    rows = await supabase_service.get_user_generations(user_id, limit=limit)
    generations = [_to_out(row) for row in rows]
    return HistoryResponse(count=len(generations), generations=generations)
