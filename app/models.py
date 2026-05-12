"""Pydantic models and SQLite schema (stdlib sqlite3 only)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from app.config import DATABASE_PATH, DATA_DIR


class TranscriptionStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TranscriptionResponse(BaseModel):
    job_id: str
    status: TranscriptionStatus
    transcript: Optional[str] = None
    segments: Optional[list] = None
    error: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Create the jobs table if it does not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                audio_path TEXT NOT NULL,
                transcript TEXT,
                segments TEXT,
                error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
