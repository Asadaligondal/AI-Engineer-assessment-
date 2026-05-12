# Transcription Service

Async-first transcription API: upload audio, get a `job_id`, poll for status. A **FastAPI** app accepts uploads, stores jobs in **SQLite**, and runs **normalize → chunk (if long) → faster-whisper `'base'`** in **BackgroundTasks**. The design is deliberately small and runnable locally; production notes below describe how it scales.

## Quick Start

**Prerequisites:** Python **3.11+**; **ffmpeg** installed and on `PATH` (pydub uses it to decode MP3/M4A/FLAC/OGG; WAV-only works in a pinch).

**Install** (from `transcription_service/`):

```bash
pip install -r requirements.txt
```

**Run:**

```bash
uvicorn app.main:app --reload
```

Default URL: `http://127.0.0.1:8000`. OpenAPI: `http://127.0.0.1:8000/docs`.

**Example API calls** (replace `JOB_ID` with the value from the POST response):

```bash
curl -s "http://127.0.0.1:8000/health"

curl -s -X POST "http://127.0.0.1:8000/transcriptions" \
  -F "file=@tests/sample_audio.wav"

curl -s "http://127.0.0.1:8000/transcriptions/JOB_ID"
```

`POST /transcriptions` returns **202** with `{"job_id","status":"queued"}`. Poll **GET** until `status` is `completed` or `failed`.

## Architecture

```
  Client                    FastAPI app
    |                           |
    |  POST audio / GET job     |
    v                           v
+------------------+    +------------------+
|  curl / browser  |--->|  HTTP + CORS     |
+------------------+    +--------+---------+
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
             +-------------+          +------------------+
             |   SQLite    |          | BackgroundTasks|
             |  (job row)  |          | normalize/chunk  |
             +-------------+          | transcribe (CPU) |
                    ^                  +--------+---------+
                    |                           |
                    +-----------+---------------+
                                v
                         +-------------+
                         |faster-whisper|
                         |  ('base')    |
                         +-------------+
```

- **Client:** uploads bytes or polls JSON; no long-lived connection required.
- **FastAPI:** validates uploads (size, extension), writes to `data/audio/`, enqueues work, serves job status.
- **SQLite:** durable job state (`queued` → `processing` → `completed` | `failed`).
- **BackgroundTasks:** runs the CPU-heavy pipeline after the response is sent; same pattern as a worker, without extra infrastructure.
- **faster-whisper:** CTranslate2-backed inference; `'base'` keeps the repo usable on a laptop CPU.

## Part 1: Pipeline Design Decisions

### Audio Format Handling

All input is **normalized to 16 kHz mono PCM WAV** via **ffmpeg/pydub** before transcription. **Why:** the model sees one canonical format; decoding and sample-rate conversion stay a thin I/O layer instead of leaking into the transcriber. **Supported:** WAV, MP3, M4A, FLAC, OGG; unsupported or corrupt files fail fast with a clear error.

### Long Audio Files

**30 s windows, 2 s overlap.** Chunk-local timestamps are shifted by the chunk’s start offset and stitched into one timeline. Segments falling in the overlap tail of non-first chunks are dropped with a simple heuristic (skip when chunk-local start is under `overlap_seconds`). **Why not VAD:** VAD splits boundaries more accurately but adds dependency and tuning; for this scope, fixed windows plus overlap are the right complexity/quality tradeoff. **Production:** revisit if word-level accuracy at chunk edges is business-critical.

### Library Choice

**faster-whisper** over vanilla Whisper: roughly **~4× faster on CPU** via CTranslate2 with comparable quality for the same checkpoint. **`'base'`** here so reviewers can run without a GPU; **`'large-v3'` on GPU** is the production default I’d deploy for quality/latency.

## Part 2: Production Architecture

Each item: **decision → reason → tradeoff.**

### Concurrent Uploads

**Decouple upload from processing with Celery workers backed by Redis.** The API tier stays stateless and scales horizontally; workers scale on queue depth independently. **Back-pressure:** return **503** when the queue passes a threshold instead of accepting work that will time out or fail silently. **Tradeoff:** you operate Redis and worker pools—ops complexity in exchange for predictable load shedding.

### Storage

**Audio → S3 (or MinIO):** large, immutable blobs, write-once; **presigned URLs** so the API never streams bytes through itself. **Lifecycle** expiry after N days; **encryption at rest** enabled. **Transcripts + metadata → Postgres:** small, relational, indexed; schema matches SQLite plus **`user_id`** and audit columns (`created_by`, `updated_by`, timestamps). **Tradeoff:** two systems to secure and monitor instead of one folder on disk.

### Retry / Recovery

**Classify errors:** **transient** (network blips, OOM, transient S3 errors) vs **permanent** (corrupt media, unsupported format). Transient: **exponential backoff, cap at 3 retries** (same spirit as this repo). Permanent: **dead-letter queue + alert**, no infinite retry burn. **Idempotency:** **`job_id`** keys idempotent writes so retries don’t duplicate transcripts. **Worker heartbeats + visibility timeouts** so a crashed worker doesn’t leave jobs stuck in `processing`. **Tradeoff:** more moving parts than in-process retries; you gain durability and observability.

### API

**Async REST:** **POST → 202 + `job_id`**, client **polls GET**, or registers a **webhook URL** for completion callbacks. **API keys** and **per-key rate limits**. FastAPI exposes **OpenAPI/Swagger at `/docs`** without extra work. **Tradeoff:** polling adds client logic; webhooks add signature verification and delivery retries.

## What's Simplified Here

| Here | Production | Why the swap is simple |
|------|------------|-------------------------|
| SQLite | Postgres | Same relational shape; swap the driver and connection pooling. |
| `BackgroundTasks` | Celery + Redis | Same `process_job` body becomes a task; Redis holds queue state. |
| Local `data/audio/` | S3 presigned uploads | API already hands off bytes after validate; change storage target. |
| No auth | API keys + rate limits | Route middleware + dependency injection on existing routes. |
| `'base'` / CPU | `'large-v3'` / GPU | Same `faster-whisper` API; different model id and device flags. |

## Testing

From `transcription_service/`:

```bash
pip install -r requirements.txt
pytest
```

Focused integration test: `pytest tests/test_pipeline.py` (silent WAV through normalize + transcribe; asserts response shape).

## Future Improvements

- **Diarization** (speaker labels).
- **Language auto-detect** (explicit `language` vs auto).
- **Streaming transcription** for live captions.
- **GPU** deployment path and batching for throughput.
