"""Audio normalization and chunking helpers (pydub + ffmpeg)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydub import AudioSegment

SUPPORTED_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg"})


class UnsupportedFormatError(Exception):
    """Raised when the input file is missing, corrupt, or not a supported format."""


def load_and_normalize(file_path: str) -> str:
    """Load audio with pydub, normalize to 16 kHz mono PCM WAV; return path to a new WAV file."""
    path = Path(file_path)
    if not path.is_file():
        raise UnsupportedFormatError(f"File not found: {file_path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported extension {ext!r}; expected one of {sorted(SUPPORTED_EXTENSIONS)}"
        )

    try:
        audio = AudioSegment.from_file(str(path))
    except Exception as e:
        raise UnsupportedFormatError(
            f"Could not read audio (unsupported or corrupt file): {file_path}"
        ) from e

    # 16-bit PCM mono @ 16 kHz (sample_width 2 = 16-bit)
    normalized = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        normalized.export(tmp_path, format="wav")
    except Exception:
        if os.path.isfile(tmp_path):
            os.unlink(tmp_path)
        raise

    return tmp_path


def chunk_audio(
    audio_path: str,
    chunk_seconds: float = 30,
    overlap_seconds: float = 2,
):
    """
    Yield (chunk_path, start_offset_seconds).

    If total duration <= chunk_seconds, yields (audio_path, 0.0) once.
    Otherwise exports overlapping chunks to temporary WAV files (caller should delete those
    paths when they differ from audio_path).
    """
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    if overlap_seconds < 0:
        raise ValueError("overlap_seconds must be non-negative")
    if overlap_seconds >= chunk_seconds:
        raise ValueError("overlap_seconds must be less than chunk_seconds")

    try:
        audio = AudioSegment.from_file(audio_path)
    except Exception as e:
        raise UnsupportedFormatError(f"Could not read audio for chunking: {audio_path}") from e

    duration_ms = len(audio)
    chunk_ms = int(chunk_seconds * 1000)
    overlap_ms = int(overlap_seconds * 1000)
    step_ms = chunk_ms - overlap_ms

    if duration_ms <= chunk_ms:
        yield (audio_path, 0.0)
        return

    start_ms = 0
    while start_ms < duration_ms:
        end_ms = min(start_ms + chunk_ms, duration_ms)
        if end_ms <= start_ms:
            break

        chunk = audio[start_ms:end_ms]
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        chunk_path = tmp.name
        tmp.close()
        try:
            chunk.export(chunk_path, format="wav")
        except Exception:
            if os.path.isfile(chunk_path):
                os.unlink(chunk_path)
            raise

        yield (chunk_path, start_ms / 1000.0)

        if end_ms >= duration_ms:
            break
        start_ms += step_ms
