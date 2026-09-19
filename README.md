# AI Avatar Studio

Turn any image into a talking AI avatar.

Upload a photo, upload a short voice sample, type a script, and the app generates a
talking avatar video: **CosyVoice** clones the voice and speaks the script, then
**SoulX-Flash Lite** animates the photo against that audio. Both models run on
**RunPod Serverless**, so no GPU work happens on the web server.

The app ships with a **mock mode** that simulates the whole pipeline, so you can build and
test the website, the API, the database and the progress bar without spending a cent on GPU time.

---

## Table of contents

1. [Features](#1-features)
2. [Technologies](#2-technologies)
3. [Architecture](#3-architecture)
4. [Folder structure](#4-folder-structure)
5. [Local setup on Windows](#5-local-setup-on-windows)
6. [Supabase setup](#6-supabase-setup)
7. [Environment variables](#7-environment-variables)
8. [Running the app](#8-running-the-app)
9. [Mock mode](#9-mock-mode)
10. [Real AI mode: RunPod, CosyVoice, SoulX-Flash Lite](#10-real-ai-mode)
11. [API endpoints](#11-api-endpoints)
12. [Authentication](#12-authentication)
13. [Troubleshooting](#13-troubleshooting)
14. [Deployment](#14-deployment)
15. [Known limits](#15-known-limits)

---

## 1. Features

- Drag-and-drop upload for the avatar image, with preview, file size and remove
- Drag-and-drop upload for the voice sample, with an inline audio player and duration
- Script box with live word count, character count and estimated speech duration
- Duration presets (15s / 30s / 1m / 2m / 3m / 5m) plus a custom value in seconds or minutes
- A warning when the script length and the requested duration disagree — the text is never
  repeated or trimmed automatically
- Quality selector (480p / 720p / 1080p), validated against what your worker actually supports
- Mandatory consent checkbox — the Generate button stays disabled until it is ticked
- Job-based generation: the POST returns immediately, the browser polls for progress
- Staged progress display with a percentage and a checklist
- Video player, download button and a history page of past generations
- Supabase PostgreSQL + Storage, with Row Level Security and a private bucket
- Mock mode for free end-to-end testing
- Automatic API docs at `/docs` and `/redoc`

---

## 2. Technologies

| Layer | What is used |
|---|---|
| Backend | Python, FastAPI, Uvicorn, Pydantic, HTTPX, python-multipart, python-dotenv, aiofiles |
| Frontend | HTML5, CSS3, vanilla JavaScript (no React, Vue, Bootstrap or Tailwind) |
| Database | Supabase PostgreSQL |
| File storage | Supabase Storage (private bucket) |
| GPU | RunPod Serverless (RTX 3090 24 GB class or better) |
| Speech | CosyVoice |
| Video | SoulX-Flash Lite |

---

## 3. Architecture

```
User
  |
  |  image + voice + script + duration + quality + consent
  v
FastAPI backend  (validation)
  |
  v
Supabase Storage  (images/, voices/)
  |
  v
avatar_generations row created -> generation_id returned immediately
  |
  |  background task
  v
RunPod Serverless -> CosyVoice        -> generated speech
  |
  v
Supabase Storage  (generated-audio/)
  |
  v
RunPod Serverless -> SoulX-Flash Lite -> talking avatar video
  |
  v
Supabase Storage  (generated-videos/)  + database updated to "completed"
  |
  v
Browser polls /api/status/{id} -> plays and downloads the video
```

Two design rules hold this together:

**Nothing long-running happens inside an HTTP request.** `POST /api/generate` validates,
uploads, creates a row, queues the work and returns a `generation_id` within a second. All
progress is read through `GET /api/status/{generation_id}`.

**Only one module knows where data lives.** Routes never talk to Supabase or RunPod directly.
They call `app/services/`, which means swapping the storage backend or the worker payload
touches one file, not twenty.

---

## 4. Folder structure

```
ai-avatar-studio/
├── main.py                     FastAPI app: CORS, static files, routers, error handlers
├── requirements.txt
├── .env                        your real secrets (never commit this)
├── .env.example                template with no secrets
├── .gitignore
├── database.sql                run this in the Supabase SQL Editor
├── README.md
│
├── app/
│   ├── config.py               every setting, read from .env
│   │
│   ├── routes/
│   │   ├── avatar.py           HTML pages + /api/health
│   │   ├── generation.py       generate, status, video, media, download, delete
│   │   ├── history.py          /api/history
│   │   └── auth.py             signup, login, logout + the current-user dependency
│   │
│   ├── services/
│   │   ├── supabase_service.py Supabase database + storage (the only data module)
│   │   ├── local_store.py      local disk fallback so the app runs without Supabase
│   │   ├── runpod_service.py   generic RunPod client: submit, poll, cancel
│   │   ├── cosyvoice_service.py    << EDIT: your CosyVoice worker payload
│   │   ├── soulxflash_service.py   << EDIT: your SoulX-Flash worker payload
│   │   └── pipeline.py         the orchestrator: mock mode and the real flow
│   │
│   ├── models/
│   │   └── schemas.py          Pydantic request/response models and status enums
│   │
│   └── utils/
│       ├── file_utils.py       extensions, unique storage paths, temp files, cleanup
│       ├── validation.py       all input validation and friendly error messages
│       └── media_utils.py      decode a worker result (base64 or URL) into bytes
│
├── static/
│   ├── css/style.css
│   ├── js/app.js
│   └── assets/sample_output.mp4    placeholder video used by mock mode
│
├── templates/
│   ├── base.html               shared header, footer and config bootstrap
│   ├── index.html              hero + the five-step generator
│   ├── history.html
│   ├── login.html
│   ├── signup.html
│   └── about.html
│
└── storage/                    created at runtime: local fallback files + temp files
```

Three files are additions to the original plan, and here is why:

- `local_store.py` — lets the app run before Supabase exists, with identical function signatures
- `pipeline.py` — the orchestrator; putting a multi-minute job inside a route handler would be wrong
- `media_utils.py` — shared by both model services, so the decoding logic is written once

---

## 5. Local setup on Windows

Open **Command Prompt** or **PowerShell** in the project folder.

```bat
:: 1. create a virtual environment
python -m venv venv

:: 2. activate it (Command Prompt)
venv\Scripts\activate

:: PowerShell uses this instead:
:: venv\Scripts\Activate.ps1

:: 3. install the packages
pip install -r requirements.txt

:: 4. copy the environment template
copy .env.example .env

:: 5. run the server
uvicorn main:app --reload
```

Then open:

- Website — http://127.0.0.1:8000
- API docs — http://127.0.0.1:8000/docs
- Config check — http://127.0.0.1:8000/api/health

You should see the hero, the five-step generator, and an amber "Mock mode is on" banner.

> If PowerShell refuses to run the activate script, run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window first.

**You do not need Supabase yet.** With no Supabase keys in `.env`, the app writes to
`./storage` and works completely. The startup log tells you which backend is active:

```
storage backend : local
```

---

## 6. Supabase setup

### 6.1 Create the project

1. Go to https://supabase.com and create a new project.
2. Wait for it to finish provisioning (about two minutes).

### 6.2 Create the tables

1. Open **SQL Editor → New query**.
2. Paste the entire contents of `database.sql`.
3. Click **Run**.

This creates:

- `profiles` — one row per account, keyed to `auth.users`
- `avatar_generations` — one row per job, with UUID primary key and timestamps
- indexes on `user_id`, `created_at`, `status`, and a combined `(user_id, created_at desc)`
- `updated_at` triggers on both tables
- a trigger that creates a profile automatically when someone signs up
- Row Level Security policies: a user reads and writes only their own rows
- the private `avatar-files` storage bucket and its per-user policies

### 6.3 Check the storage bucket

Open **Storage**. You should see a bucket named `avatar-files` marked **private**.
Folders are created on first upload:

```
images/{user_id}/{generation_id}.png
voices/{user_id}/{generation_id}.wav
generated-audio/{user_id}/{generation_id}.wav
generated-videos/{user_id}/{generation_id}.mp4
```

Because `{generation_id}` is a fresh UUID every time, nothing is ever overwritten.

### 6.4 Copy your keys

Open **Project Settings → API** and copy three values into `.env`:

| Supabase field | `.env` variable | Who may see it |
|---|---|---|
| Project URL | `SUPABASE_URL` | Safe in a browser |
| `anon` `public` key | `SUPABASE_KEY` | Safe in a browser |
| `service_role` key | `SUPABASE_SERVICE_KEY` | **Server only. Never.** |

The backend uses the service-role key because it writes to Storage and updates rows on a
user's behalf. That key bypasses RLS by design, so it must never appear in JavaScript, in a
template, in a screenshot or in a git commit.

### 6.5 Development user and the foreign key

With `AUTH_ENABLED=false`, every generation is saved under `DEV_USER_ID`. But
`avatar_generations.user_id` references `profiles.id`, which references `auth.users`. So pick one:

- **Recommended** — create a user in **Authentication → Users**, copy its UUID into
  `DEV_USER_ID` in `.env`. Everything works with the foreign key intact.
- **Quick and dirty** — uncomment the last line of `database.sql` to drop that foreign key.

---

## 7. Environment variables

Copy `.env.example` to `.env` and fill it in. `.env` is already in `.gitignore`.

| Variable | Default | What it does |
|---|---|---|
| `MOCK_MODE` | `true` | `true` simulates generation with no GPU |
| `MOCK_DURATION_SECONDS` | `12` | How long a simulated job takes |
| `STORAGE_BACKEND` | `auto` | `auto`, `supabase` or `local` |
| `SUPABASE_URL` | — | Project URL |
| `SUPABASE_KEY` | — | anon key |
| `SUPABASE_SERVICE_KEY` | — | service-role key, server only |
| `SUPABASE_BUCKET` | `avatar-files` | Storage bucket name |
| `AUTH_ENABLED` | `false` | Turn Supabase Auth on |
| `DEV_USER_ID` | a fixed UUID | Owner of generations when auth is off |
| `RUNPOD_API_KEY` | — | RunPod API key |
| `RUNPOD_BASE_URL` | `https://api.runpod.ai/v2` | RunPod Serverless base URL |
| `COSYVOICE_ENDPOINT_ID` | — | Your CosyVoice endpoint id |
| `SOULXFLASH_ENDPOINT_ID` | — | Your SoulX-Flash Lite endpoint id |
| `RUNPOD_POLL_INTERVAL` | `3` | Seconds between job status checks |
| `RUNPOD_TIMEOUT_SECONDS` | `1800` | Give up on a job after this |
| `MAX_IMAGE_MB` | `10` | Image size limit |
| `MAX_VOICE_MB` | `25` | Audio size limit |
| `MAX_SCRIPT_CHARS` | `5000` | Script length limit |
| `MIN_DURATION_SECONDS` | `5` | Shortest allowed video |
| `MAX_DURATION_SECONDS` | `300` | Longest allowed video — guards against runaway GPU bills |
| `ALLOWED_QUALITIES` | `480p,720p,1080p` | Trim this to what your worker really supports |
| `WORDS_PER_MINUTE` | `150` | Used for the speech duration estimate |
| `CORS_ORIGINS` | `*` | Lock this down before deploying |

Values still holding a placeholder like `your_runpod_api_key` are treated as **not set**, so
`/api/health` reports the truth rather than pretending the keys are configured.

---

## 8. Running the app

```bat
venv\Scripts\activate
uvicorn main:app --reload
```

Check `/api/health` first — it tells you exactly how the app is configured:

```json
{
  "status": "ok",
  "mock_mode": true,
  "storage_backend": "local",
  "auth_enabled": false,
  "runpod_configured": false
}
```

---

## 9. Mock mode

With `MOCK_MODE=true` the app never calls RunPod, CosyVoice or SoulX-Flash Lite. Instead it:

- walks through the real stages on a timer, writing progress to the database
- copies your uploaded voice into `generated-audio/` so the audio player has something to play
- returns `static/assets/sample_output.mp4` as the finished video

Everything else is real: validation, uploads, the database row, status polling, the progress
bar, the video player, the download endpoint and the history page. This is how you should
build and test Phases 1–4.

To use your own placeholder, replace `static/assets/sample_output.mp4` with any short MP4.

---

## 10. Real AI mode

### 10.1 What you need from RunPod

Deploy **two** RunPod Serverless endpoints — one running CosyVoice, one running SoulX-Flash
Lite — then put their ids in `.env`:

```
MOCK_MODE=false
RUNPOD_API_KEY=rpa_xxxxxxxxxxxxxxxx
COSYVOICE_ENDPOINT_ID=xxxxxxxxxxxx
SOULXFLASH_ENDPOINT_ID=xxxxxxxxxxxx
```

### 10.2 The important part: the payloads are yours, not mine

CosyVoice and SoulX-Flash Lite have no single official hosted HTTP API. What the request and
response look like is decided entirely by the handler you deploy in your worker. So this
project does **not** pretend to know them. Both model services are written as adapters with a
clearly marked edit point, and nothing else in the app depends on their shape.

**`app/services/cosyvoice_service.py`** — change two things:

```python
def build_payload(...):
    return {
        "text": text,                         # <- your field name
        "reference_audio": reference_audio_base64,
        "reference_audio_format": "wav",
        "prompt_text": "",
    }

AUDIO_BASE64_KEYS = ("audio_base64", "audio", ...)   # where the audio comes back
AUDIO_URL_KEYS    = ("audio_url", "url", ...)
OUTPUT_EXTENSION  = ".wav"                            # or ".mp3"
```

**`app/services/soulxflash_service.py`** — same idea:

```python
def build_payload(...):
    return {
        "image": image_base64,
        "audio": audio_base64,
        "resolution": height,
        "fps": 25,
    }

VIDEO_BASE64_KEYS = ("video_base64", "video", ...)
VIDEO_URL_KEYS    = ("video_url", "url", ...)
RESOLUTION_MAP    = {"480p": 480, "720p": 720, "1080p": 1080}
```

`media_utils.result_to_bytes()` already handles both return styles — inline base64 (with or
without a `data:` prefix) and a URL it downloads — and it looks one level into a `{"output": …}`
or `{"result": …}` wrapper. In many cases you will only need to edit `build_payload()`.

**If your worker cannot do 1080p**, remove it from `ALLOWED_QUALITIES` in `.env` and from
`RESOLUTION_MAP`. The API will then reject that request with a clear message instead of
quietly returning a smaller video.

### 10.3 What to send me / write down from your worker

To finish the wiring, you need four facts from each worker's `handler.py`:

1. the exact keys inside `input`
2. whether files go in as base64 or as a URL
3. the exact keys inside `output`
4. the file format that comes back (`wav` / `mp3`, `mp4` / `webm`)

### 10.4 Test each stage on its own

Test the RunPod endpoint directly first, before involving this app:

```bat
curl -X POST https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/run ^
  -H "Authorization: Bearer YOUR_API_KEY" ^
  -H "Content-Type: application/json" ^
  -d "{\"input\": {\"text\": \"hello\"}}"
```

That returns a job id. Poll it:

```bat
curl https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/status/JOB_ID ^
  -H "Authorization: Bearer YOUR_API_KEY"
```

Once that works by hand, set `MOCK_MODE=false` and run a generation. The server log prints
every stage, and `cosyvoice_job_id` / `soulxflash_job_id` are saved on the row so you can look
a failed job up in the RunPod dashboard.

A useful halfway step: keep `MOCK_MODE=false` but point **both** endpoint ids at CosyVoice
only, to confirm the speech stage end to end before the video stage exists.

---

## 11. API endpoints

Full interactive docs: `/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Home page and generator |
| `GET` | `/history` | History page |
| `GET` | `/about`, `/login`, `/signup` | Other pages |
| `GET` | `/api/health` | Status and configuration |
| `POST` | `/api/generate` | Start a generation. Returns `generation_id` |
| `GET` | `/api/status/{id}` | Status, progress percentage, current stage |
| `GET` | `/api/video/{id}` | Details of a finished video |
| `GET` | `/api/media/{id}/{kind}` | Stream `image`, `voice`, `audio` or `video` |
| `GET` | `/api/download/{id}` | Download the MP4 |
| `GET` | `/api/history` | The user's past generations |
| `GET` | `/api/generation/{id}` | One full record |
| `DELETE` | `/api/generation/{id}` | Delete a record and its files |
| `POST` | `/api/auth/signup`, `/login`, `/logout` | Accounts (when enabled) |

`POST /api/generate` takes multipart form data: `image`, `voice`, `text`, `duration`
(seconds), `quality`, `consent`. It responds `202`:

```json
{ "generation_id": "550e8400-e29b-41d4-a716-446655440000", "status": "pending" }
```

`GET /api/status/{id}`:

```json
{
  "generation_id": "550e8400-...",
  "status": "generating_avatar",
  "progress": 70,
  "stage": "Applying lip-sync",
  "error_message": null,
  "video_url": null
}
```

Status values: `pending`, `uploading`, `generating_voice`, `generating_avatar`,
`processing`, `completed`, `failed`.

Media is streamed through the backend rather than served from public storage URLs. That keeps
the bucket private and runs the ownership check on every request. `create_signed_url()` is in
`supabase_service.py` if you later want direct CDN links instead.

---

## 12. Authentication

Phase 1 runs without accounts. `AUTH_ENABLED=false` makes every request resolve to
`DEV_USER_ID`, so the history page and the `user_id` column behave exactly as they will later.

To turn accounts on:

1. Set `AUTH_ENABLED=true` in `.env`.
2. In Supabase, open **Authentication → Providers** and enable Email.
3. Restart the server.

The frontend then signs in through `/api/auth/login`, stores the Supabase access token, and
sends it as `Authorization: Bearer …`. Every route that touches a generation calls
`ensure_owner()`, which returns **404** rather than 403 for someone else's id — so the API
never confirms that an id exists for another user.

No route handler changes between the two modes. That is the whole point of the
`get_current_user_id` dependency.

---

## 13. Troubleshooting

**`ModuleNotFoundError: No module named 'fastapi'`**
The virtual environment is not active. Run `venv\Scripts\activate` — the prompt should start
with `(venv)`.

**`'uvicorn' is not recognized`**
Same cause. Or run it as `python -m uvicorn main:app --reload`.

**Port 8000 is already in use**
`uvicorn main:app --reload --port 8001`.

**`/api/health` says `"storage_backend": "local"` but I configured Supabase**
A key is missing or still holds a `your_…` placeholder. Set all three Supabase values, or
force it with `STORAGE_BACKEND=supabase` to get a loud error instead of a silent fallback.

**"new row violates row-level security policy"**
The backend is using the anon key. Set `SUPABASE_SERVICE_KEY` in `.env`.

**"insert or update violates foreign key constraint … user_id"**
`DEV_USER_ID` is not a real `auth.users` id. See section 6.5.

**"The selected image format is not supported"**
Only `.jpg`, `.jpeg`, `.png`, `.webp` are accepted. The extension is what counts.

**"That file does not look like audio"**
The extension is not `.wav`, `.mp3` or `.m4a`. A generic MIME type from the browser is fine —
only a contradicting one is rejected.

**The progress bar sticks at 0% and never moves**
Open the browser console. If `/api/status/{id}` returns 404, the row was not created — check
the server log. If it 401s, your session expired; log in again.

**"RunPod is currently unavailable"**
Wrong or missing `RUNPOD_API_KEY`, or the endpoint is asleep. Test the endpoint with the curl
commands in section 10.4.

**"The GPU worker returned a video in an unexpected format"**
Your worker's output keys don't match. Look at the server log — it prints the first 500
characters of the output — then add the right key to `VIDEO_BASE64_KEYS` or `VIDEO_URL_KEYS`.

**"The generation timed out"**
Raise `RUNPOD_TIMEOUT_SECONDS`, or use a shorter script and a lower quality. A cold RunPod
worker can spend several minutes loading model weights before it starts.

**Progress resets after I restart the server**
Expected. The in-memory progress cache lives in one process; the database row is the durable
record, and the status endpoint falls back to it.

**The history page is empty even though I generated a video**
With auth off, history is filtered by `DEV_USER_ID`. If you changed that value, older rows
belong to the previous id.

---

## 14. Deployment

The backend is a normal FastAPI app and deploys anywhere Python runs — Render, Railway, Fly.io,
a VPS behind nginx. RunPod handles the GPU, so the web host needs no graphics card.

Before you deploy:

- `DEBUG=false`
- `CORS_ORIGINS` set to your real domain, not `*`
- real secrets in the host's environment variables, not in a committed `.env`
- `AUTH_ENABLED=true`, so RLS and the ownership checks actually mean something
- run behind HTTPS

```bat
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## 15. Known limits

Worth being straight about, since these matter the moment real traffic arrives:

**The in-memory job tracker is not production-safe.** It lives inside one uvicorn process. Run
`--workers 4` and a status poll may land on a worker that never saw the job — it falls back to
the database, which is correct but less immediate. Background tasks also die if the process
restarts mid-generation, and that job will sit in `processing` forever. For production, move
the pipeline out of the web process: Redis plus Celery/RQ/Dramatiq, or RunPod webhooks
writing status straight to the database.

**Files are held in memory.** Uploads and generated videos are read fully into RAM before
being stored. Fine at the configured size limits; stream to disk in chunks if you raise them.

**Progress percentages between RunPod polls are estimated**, not reported by the worker. The
stage labels are accurate; the numbers between them creep on a timer.

**There is no rate limiting.** Anyone who can reach `/api/generate` can queue GPU jobs. Add
per-user quotas before opening this to the public.

**Consent is a checkbox, not verification.** The app cannot tell whether someone actually owns
the face and voice being uploaded. If this goes anywhere near real users, add provenance
labelling on the output and a way to report misuse — synthetic video of a real person saying
things they never said causes real harm, and a tick box is not a safeguard on its own.
