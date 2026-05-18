"""FastAPI application entrypoint and HTTP routes."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.audio_utils import SUPPORTED_EXTENSIONS
from app.config import AUDIO_UPLOAD_DIR, DATA_DIR, MAX_UPLOAD_BYTES
from app.jobs import create_job, get_job, process_job
from app.models import TranscriptionResponse, TranscriptionStatus, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(title="Transcription Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/transcriptions", status_code=status.HTTP_202_ACCEPTED)
async def create_transcription(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    if not file.filename:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Uploaded file must have a filename"
        )

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type {ext!r}; allowed: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {MAX_UPLOAD_BYTES} bytes",
        ) 

    job_id = str(uuid.uuid4())
    dest = AUDIO_UPLOAD_DIR / f"{job_id}{ext}"
    dest.write_bytes(body)

    create_job(str(dest.resolve()))
    background_tasks.add_task(process_job, job_id)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"job_id": job_id, "status": TranscriptionStatus.QUEUED.value},
    )


@app.get("/transcriptions/{job_id}", response_model=TranscriptionResponse)
def get_transcription(job_id: str) -> TranscriptionResponse:
    row = get_job(job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")

    st = TranscriptionStatus(row["status"])
    transcript = row.get("transcript")
    segments = row.get("segments")
    err = row.get("error")

    if st != TranscriptionStatus.COMPLETED:
        transcript = None
        segments = None
    if st != TranscriptionStatus.FAILED:
        err = None

    return TranscriptionResponse(
        job_id=row["job_id"],
        status=st,
        transcript=transcript,
        segments=segments,
        error=err,
    )
