# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`piper-sandbox` is a small Python package that wraps the [Piper](https://github.com/rhasspy/piper) TTS binary behind a stdlib HTTP server (`http.server`, no FastAPI/Flask) plus an optional single-page web GUI and a Tkinter desktop GUI. The default voice is `es_MX-ald-medium` (Latin-American Spanish). There are **no tests** in this repository.

## Common Commands

Local setup (Python 3.11/3.12; 3.13 may fail because of `onnxruntime` wheels for `piper-tts`):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[tts]'   # installs the app plus piper-tts; use `pip install -e .` for GUI-only deployments
```

Run the combined HTTP server + web GUI:

```bash
python -m piper_sandbox.api                              # honours .env / PIPER_SERVICE_MODE
python -m piper_sandbox.api --mode engine                # API only (/health, /models, /speak)
python -m piper_sandbox.api --mode gui --engine-url URL  # GUI only, delegates synthesis to a remote engine
```

Tkinter desktop client (talks to a local API on `127.0.0.1:8000`):

```bash
python -m piper_sandbox.gui
```

Docker:

```bash
docker compose up --build
PIPER_SERVICE_MODE=engine PIPER_INSTALL_TARGET='.[tts]' docker compose up --build
PIPER_SERVICE_MODE=gui PIPER_INSTALL_TARGET=. PIPER_ENGINE_URL=https://engine.example.com docker compose up --build
```

There is no lint config, no test runner, and no formatter wired up; do not invent commands for them.

## Architecture

### Service modes (`PIPER_SERVICE_MODE`)

A single process can run in one of three modes, all selected at startup in `api.py:main`:

- `both` — serves the GUI at `/` **and** the engine endpoints (`/health`, `/models`, `/speak`).
- `engine` — engine endpoints only; `/` returns 404. Use on hosts that have Piper + onnxruntime.
- `gui` — only `/` and `/health`. The HTML is templated with `__ENGINE_URL__`, so the browser calls a *remote* engine for `/models` and `/speak`. This mode does not need `piper-tts` installed.

`engine_enabled` / `gui_enabled` are derived from `service_mode` on `PiperRequestHandler` and gate every route. When adding endpoints, mirror this gating or you will leak engine functionality into GUI-only deployments.

The legacy `PIPER_ENABLE_GUI` boolean is still honoured (maps to `both`/`engine`) but `PIPER_SERVICE_MODE` wins when both are set. The `--gui`/`--no-gui` CLI flags also map onto the mode.

### Module layout (`piper_sandbox/`)

- `config.py` — tiny hand-rolled `.env` loader (no `python-dotenv`) plus `env_bool`/`env_int`. `load_env` uses `os.environ.setdefault`, so real environment variables always win over the file.
- `models.py` — `ModelSpec` dataclass and `MODELS` registry. `model_spec_from_name` parses Piper's `language_COUNTRY-voice-quality` convention to derive the Hugging Face URL `{HF_BASE}/{lang}/{lang}_{COUNTRY}/{voice}/{quality}/{name}.onnx`. `parse_model_names` accepts either a JSON array or a comma-separated string in `PIPER_MODEL_NAMES`. The registry is built **at import time** from env vars — changing `PIPER_MODEL_NAMES` after import has no effect.
- `engine.py` — `PiperEngine` shells out to the `piper` binary (located via `shutil.which(PIPER_BIN)`) with `--model`/`--output_file` and pipes text on stdin. `ensure_model` lazily downloads `.onnx` + `.onnx.json` into `PIPER_MODELS_DIR` (default `models/piper/<name>/`) using `urllib.request.urlretrieve` with a `.download` temp suffix. All failures raise `PiperError`.
- `api.py` — stdlib `ThreadingHTTPServer` + `BaseHTTPRequestHandler`. The web GUI is one inlined `INDEX_HTML` constant; `__ENGINE_URL__` is string-replaced at request time. CORS headers are emitted on every JSON/binary response and on `OPTIONS` preflight, governed by `PIPER_CORS_ORIGIN`.
- `gui.py` — Tkinter desktop client. Hardcodes `API_URL = http://127.0.0.1:8000/speak`; for audio playback it probes for `paplay`, `aplay`, then `ffplay` in that order.
- `wyoming.py` — thin `subprocess.Popen` wrapper around the separately-installed `wyoming-piper` binary. Independent of the HTTP API; only re-exported via `__init__.py` for library users.

### Request flow for `POST /speak`

`api.PiperRequestHandler.do_POST` → `PiperEngine.synthesize_bytes` → `synthesize_to_file` → `subprocess.run([piper, --model, ..., --output_file, <tempfile>])` with text on stdin → temp WAV is read back and returned with `Content-Type: audio/wav`. The temp file is always unlinked in `finally`.

### Configuration cheatsheet

Key env vars (full list in `.env.example`):

- `PIPER_HOST` / `PIPER_PORT` — what the Python process binds to.
- `PIPER_HOST_PORT` — only used by `docker-compose.yml` to map the host port; irrelevant when running locally.
- `PIPER_INSTALL_TARGET` — Docker build arg: `.[tts]` for engine/both, `.` for gui-only images.
- `PIPER_BIN` — explicit path to the `piper` executable when it is not on `PATH`.
- `PIPER_HF_BASE` — override the Hugging Face mirror used by `model_spec_from_name`.
- `PIPER_MODELS_DIR` — where downloaded `.onnx` files live (mounted as the `piper-models` volume in Compose).

## Gotchas

- Adding a new model is just adding its name to `PIPER_MODEL_NAMES`; the URL is derived. The name **must** match `language_COUNTRY-voice-quality` or `model_spec_from_name` raises `ValueError`.
- `models.py` calls `load_env()` at import. Don't add side effects that assume env vars set later in `main()` are visible to `MODELS`.
- The `models/piper/` directory is gitignored content; the first synthesis call downloads ~tens of MB per voice.
- `gui.py` (Tkinter) requires a running local API on port 8000 — it does not respect `PIPER_ENGINE_URL`. The browser GUI in `api.py` does.
