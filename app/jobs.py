"""SQLite job persistence and background task orchestration."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.audio_utils import UnsupportedFormatError, load_and_normalize
from app.config import DATABASE_PATH
from app.models import TranscriptionStatus, _utc_now_iso
from app.transcriber import transcribe


def create_job(audio_path: str) -> str:
    """
    Insert a queued job row. The basename without extension must be the job UUID
    (the upload handler writes to ``data/audio/{job_id}{ext}`` first).
    """
    job_id = Path(audio_path).stem
    if not job_id:
        raise ValueError("audio_path must include a filename stem (job UUID)")
    now = _utc_now_iso()
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, status, audio_path, transcript, segments, error,
                retry_count, created_at, updated_at
            )
            VALUES (?, ?, ?, NULL, NULL, NULL, 0, ?, ?)
            """,
            (job_id, TranscriptionStatus.QUEUED.value, audio_path, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = _utc_now_iso()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE job_id = ?", values)
        conn.commit()
    finally:
        conn.close()


def get_job(job_id: str) -> dict | None:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        raw_segments = d.get("segments")
        if raw_segments is not None:
            d["segments"] = json.loads(raw_segments)
        return d
    finally:
        conn.close()


def process_job(job_id: str) -> None:
    job = get_job(job_id)
    if job is None:
        return

    update_job(job_id, status=TranscriptionStatus.PROCESSING.value)

    try:
        normalized = load_and_normalize(job["audio_path"])
        try:
            result = transcribe(normalized)
        finally:
            if os.path.isfile(normalized):
                os.unlink(normalized)

        update_job(
            job_id,
            status=TranscriptionStatus.COMPLETED.value,
            transcript=result["text"],
            segments=json.dumps(result["segments"]),
            error=None,
        )
    except UnsupportedFormatError as e:
        update_job(
            job_id,
            status=TranscriptionStatus.FAILED.value,
            error=str(e),
        )
    except Exception as e:
        job = get_job(job_id)
        if job is None:
            return
        rc = int(job["retry_count"])
        if rc < 3:
            update_job(job_id, retry_count=rc + 1)
            time.sleep(2**rc)
            process_job(job_id)
        else:
            update_job(
                job_id,
                status=TranscriptionStatus.FAILED.value,
                error=str(e),
            )
