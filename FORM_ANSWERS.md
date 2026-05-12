# Form answers (paste into Google Form)

Tone matches this repo: FastAPI in `app/main.py`, pipeline in `app/audio_utils.py` / `app/transcriber.py`, jobs in `app/jobs.py`, SQLite schema in `app/models.py`.

---

## Implement a service or script that accepts an audio file

I shipped a **FastAPI** service (`app/main.py`) with `POST /transcriptions` that accepts multipart uploads, caps size at **100 MB**, and only allows extensions wired to pydub/ffmpeg in `app/audio_utils.py` (**WAV, MP3, M4A, FLAC, OGG**). Each upload is written to **`data/audio/{uuid}{ext}`** and registered in SQLite via `create_job` in `app/jobs.py` so the path and `job_id` stay aligned. **Tradeoff:** the API buffers the whole body in memory up to the cap—fine for this scope; production would stream to object storage with a presigned URL instead.

---

## Implement a service or script that transcribes spoken language into text

After a job is queued, **`process_job`** in `app/jobs.py` moves the row to `processing`, calls **`load_and_normalize`** then **`transcribe`** in `app/transcriber.py`, and stores the full string in the **`transcript`** column when the job completes. The model is **`faster-whisper`** with the **`base`** checkpoint, lazy-loaded on first transcribe so startup stays light. **Production note:** same code path swaps to **`large-v3`** on GPU without changing the API contract; `base` is deliberate so reviewers can run CPU-only.

---

## Implement a service or script that returns the transcription with timestamps per segment

`transcribe` returns **`segments`** as a list of **`{start, end, text}`** (seconds on the full timeline after stitching). Those are JSON-encoded into SQLite’s **`segments`** text column on completion and exposed on **`GET /transcriptions/{job_id}`** through **`TranscriptionResponse`** in `app/models.py` once **`status`** is **`completed`**. **Tradeoff:** the GET hides transcript/segments until completion so clients always see a consistent shape; polling cost is on the client unless you add webhooks later.

---

## How do you handle different audio formats?

Anything allowed by extension goes through **pydub’s `AudioSegment.from_file`**, then **`load_and_normalize`** forces **16 kHz mono 16‑bit PCM WAV** before Whisper sees it—one canonical format, decoding stays in **`audio_utils.py`**. Unsupported extensions and unreadable files raise **`UnsupportedFormatError`**, and `process_job` marks the job **`failed`** without useless retries. **Tradeoff:** this depends on **ffmpeg** being available; that’s intentional so format support stays a thin I/O layer, not logic inside the transcriber.

---

## How do you deal with long audio files?

`chunk_audio` in **`app/audio_utils.py`** splits audio longer than **30 s** into **30 s windows with 2 s overlap**, exports each slice to a temp WAV, and `transcribe` shifts every segment by the chunk’s start offset and skips chunk-local segments starting before **`overlap_seconds`** on non-first chunks to trim obvious duplicates in the overlap. **Reason:** fixed windows are predictable and don’t need VAD tuning here. **Tradeoff:** boundary accuracy is “good enough for an assessment”; production might add VAD or ASR-specific stitching if word-level edge cases mattered commercially.

---

## How would you handle concurrent uploads?

Today **`BackgroundTasks`** + SQLite serialize work per process; I’d move **`process_job`** to **Celery** with **Redis** as the broker so the API only validates, enqueues, and returns **202**. **Reason:** workers scale independently while FastAPI replicas stay stateless. **Tradeoff:** you run Redis, worker processes, and monitoring—but you gain back-pressure you can signal with **503** when the queue is over a threshold instead of silently stalling uploads.

---

## How would you store audio and transcripts?

Right now audio lives under **`data/audio/`** and transcripts plus metadata live in **`data/jobs.db`** via the **`jobs`** table defined in **`init_db()`**. In production I’d put blobs in **S3** (immutable, lifecycle expiry, encryption, presigned PUT so the API never proxies bytes) and move rows to **Postgres** with the same columns plus **`user_id`** and audit fields. **Tradeoff:** two systems to secure and operate, but object storage is the right price/performance for large audio while SQL stays queryable for job status and search.

---

## How do you retry or recover failed transcriptions?

`process_job` treats **`UnsupportedFormatError`** as permanent—**`failed`** immediately—because retries won’t fix a bad format. Other exceptions bump **`retry_count`** in SQLite, **sleep 1 s / 2 s / 4 s**, and recurse up to three backoff attempts, mirroring transient failure handling in a worker. **Production extension:** strict **idempotency on `job_id`**, a **DLQ** for permanent failures, **visibility timeouts / heartbeats** so a dead worker can’t leave a row stuck in **`processing`**, and alerts on DLQ depth.

---

## How would you expose this as an API?

The surface is already **REST**: **`POST /transcriptions`** returns **202 + `job_id`**, **`GET /transcriptions/{job_id}`** exposes **`TranscriptionResponse`**, **`GET /health`** for probes, **OpenAPI at `/docs`**. I’d keep that contract and add **API-key auth**, **per-key rate limits**, and optionally a **webhook URL** on the job to avoid polling—all orthogonal to the pipeline code in `jobs.py` / `transcriber.py`. **Tradeoff:** webhooks mean signature verification and delivery retries; keys mean a small identity service or shared secret management overhead.
