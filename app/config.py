"""Application configuration and paths."""

from __future__ import annotations

from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = SERVICE_ROOT / "data"
AUDIO_UPLOAD_DIR = DATA_DIR / "audio"
DATABASE_PATH = DATA_DIR / "jobs.db"

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
