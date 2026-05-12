"""Integration tests for the transcription HTTP and pipeline flow."""

import os
from pathlib import Path

from pydub import AudioSegment

from app.audio_utils import load_and_normalize
from app.transcriber import transcribe


def test_normalize_and_transcribe_output_shape(tmp_path: Path) -> None:
    wav = tmp_path / "silent.wav"
    AudioSegment.silent(duration=5000).export(str(wav), format="wav")

    normalized_path = load_and_normalize(str(wav))
    try:
        result = transcribe(normalized_path)
    finally:
        if os.path.isfile(normalized_path):
            os.unlink(normalized_path)

    assert isinstance(result, dict)
    assert set(result.keys()) == {"text", "segments"}
    assert isinstance(result["text"], str)
    assert isinstance(result["segments"], list)

    for seg in result["segments"]:
        assert isinstance(seg, dict)
        assert set(seg.keys()) == {"start", "end", "text"}
        assert isinstance(seg["start"], float)
        assert isinstance(seg["end"], float)
        assert isinstance(seg["text"], str)
