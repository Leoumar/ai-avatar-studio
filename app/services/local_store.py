"""
Local fallback store.

Supabase is the intended database and file store for this project, but during
Phase 1 and Phase 2 you often do not have it configured yet. This module gives
the exact same interface backed by:

    ./storage/files/...        -> uploaded and generated files
    ./storage/local_db.json    -> generation records

It exists so that `uvicorn main:app --reload` works the moment you clone the
project. It is NOT a production database: there are no transactions, no
concurrent-writer safety across processes, and no row level security.
Set STORAGE_BACKEND=supabase (or fill in the Supabase keys) for anything real.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

FILES_DIR = settings.STORAGE_DIR / "files"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalStore:
    """A tiny JSON-file "database" plus on-disk file storage."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        if not settings.LOCAL_DB_FILE.exists():
            self._write_db({"profiles": {}, "avatar_generations": {}})

    # -- raw json helpers -------------------------------------------------

    def _read_db(self) -> dict[str, Any]:
        try:
            with open(settings.LOCAL_DB_FILE, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"profiles": {}, "avatar_generations": {}}

    def _write_db(self, data: dict[str, Any]) -> None:
        settings.LOCAL_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings.LOCAL_DB_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, default=str)
        tmp.replace(settings.LOCAL_DB_FILE)

    # -- storage ----------------------------------------------------------

    async def upload(self, path: str, data: bytes, content_type: str) -> str:
        """Write bytes to ./storage/files/<path> and return <path>."""
        target = FILES_DIR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)
        return path

    async def download(self, path: str) -> bytes:
        target = FILES_DIR / path
        if not target.exists():
            raise FileNotFoundError(path)
        return await asyncio.to_thread(target.read_bytes)

    async def delete_file(self, path: str) -> None:
        target = FILES_DIR / path
        try:
            await asyncio.to_thread(target.unlink)
        except FileNotFoundError:
            pass

    def local_path(self, path: str) -> Path:
        return FILES_DIR / path

    # -- database ---------------------------------------------------------

    async def insert_generation(self, record: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            db = self._read_db()
            record = dict(record)
            record.setdefault("created_at", _now())
            record["updated_at"] = _now()
            db["avatar_generations"][record["id"]] = record
            self._write_db(db)
            return record

    async def get_generation(self, generation_id: str) -> dict[str, Any] | None:
        async with self._lock:
            db = self._read_db()
            return db["avatar_generations"].get(generation_id)

    async def update_generation(self, generation_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            db = self._read_db()
            record = db["avatar_generations"].get(generation_id)
            if record is None:
                return None
            record.update(changes)
            record["updated_at"] = _now()
            db["avatar_generations"][generation_id] = record
            self._write_db(db)
            return record

    async def list_generations(self, user_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            db = self._read_db()
            rows = list(db["avatar_generations"].values())

        if user_id:
            rows = [row for row in rows if row.get("user_id") == user_id]
        rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        return rows[:limit]

    async def delete_generation(self, generation_id: str) -> bool:
        async with self._lock:
            db = self._read_db()
            existed = db["avatar_generations"].pop(generation_id, None) is not None
            if existed:
                self._write_db(db)
            return existed
