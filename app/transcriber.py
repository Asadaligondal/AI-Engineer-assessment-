"""faster-whisper transcription wrapper (base model, CPU-friendly)."""

from __future__ import annotations

import os
from typing import Any

from faster_whisper import WhisperModel

from app.audio_utils import chunk_audio

_model: WhisperModel | None = None

CHUNK_SECONDS = 30.0
OVERLAP_SECONDS = 2.0


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model


def transcribe(audio_path: str) -> dict[str, Any]:
    """
    Transcribe one file. Long audio is chunked with 30s / 2s overlap; segment times are
    offset by chunk start, and segments in the overlap tail of non-first chunks are skipped
    when segment.start < overlap (seconds, chunk-local).
    """
    model = _get_model()
    text_parts: list[str] = []
    all_segments: list[dict[str, Any]] = []

    chunk_index = 0
    for chunk_path, start_offset in chunk_audio(
        audio_path, chunk_seconds=CHUNK_SECONDS, overlap_seconds=OVERLAP_SECONDS
    ):
        try:
            segments_gen, _info = model.transcribe(chunk_path)
            for seg in segments_gen:
                local_start = float(seg.start)
                if chunk_index > 0 and local_start < OVERLAP_SECONDS:
                    continue
                stripped = seg.text.strip()
                if stripped:
                    text_parts.append(stripped)
                all_segments.append(
                    {
                        "start": start_offset + local_start,
                        "end": start_offset + float(seg.end),
                        "text": seg.text,
                    }
                )
        finally:
            if chunk_path != audio_path:
                try:
                    os.unlink(chunk_path)
                except OSError:
                    pass
        chunk_index += 1

    return {"text": " ".join(text_parts).strip(), "segments": all_segments}
