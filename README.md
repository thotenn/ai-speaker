# Piper Sandbox

Small Python library, HTTP API, and optional web GUI for testing TTS voices with Piper. The default model is `es_MX-ald-medium`, a Latin American Spanish voice available in `rhasspy/piper-voices`.

The first generation automatically downloads the selected model into `PIPER_MODELS_DIR`.

## Requirements

- Python 3.11 or 3.12 recommended.
- `piper` binary available in `PATH`, or `PIPER_BIN=/path/to/piper`.
- Web browser if you want to use the GUI.

Python 3.13 may work for the API, but `piper-tts` can fail because of binary dependencies such as `onnxruntime`.

## Local Installation

Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install the library without Piper:

```bash
pip install -e .
```

Install the library and try to include the `piper` executable from PyPI:

```bash
pip install -e '.[tts]'
```

Verify that `piper` exists:

```bash
piper --help
```

If it cannot be installed with `pip`, install the official Piper binary and make sure it is available in your `PATH`. You can also point directly to the binary:

```bash
export PIPER_BIN=/path/to/piper
```

## Configuration

Copy the environment example:

```bash
cp .env.example .env
```

Available variables:

```env
PIPER_HOST=127.0.0.1
PIPER_PORT=8000
PIPER_SERVICE_MODE=both
PIPER_ENGINE_URL=
PIPER_CORS_ORIGIN=*
PIPER_MODELS_DIR=models/piper
PIPER_HF_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main
PIPER_DEFAULT_MODEL=es_MX-ald-medium
PIPER_MODEL_NAMES=["es_MX-claude-high","es_MX-ald-medium","es_ES-carlfm-x_low"]
# PIPER_BIN=/usr/local/bin/piper
```

`PIPER_SERVICE_MODE` controls which part of the app is exposed:

```env
PIPER_SERVICE_MODE=both
```

Available modes:

- `both`: serves the engine endpoints and the web GUI from the same process.
- `engine`: serves only `/health`, `/models`, and `/speak`.
- `gui`: serves only the web GUI. The GUI calls the remote engine defined by `PIPER_ENGINE_URL`.

To run only the engine API:

```env
PIPER_SERVICE_MODE=engine
```

To run only the GUI and point it to a remote engine:

```env
PIPER_SERVICE_MODE=gui
PIPER_ENGINE_URL=https://tts-engine.example.com
```

You can also use flags:

```bash
python -m piper_sandbox.api --mode both
python -m piper_sandbox.api --mode engine
python -m piper_sandbox.api --mode gui --engine-url https://tts-engine.example.com
python -m piper_sandbox.api --no-gui
python -m piper_sandbox.api --gui
```

`PIPER_ENABLE_GUI=true|false` is still supported for compatibility, but `PIPER_SERVICE_MODE` is preferred.

`PIPER_CORS_ORIGIN` controls the CORS header returned by the engine. Keep `*` for simple public testing, or restrict it to your GUI origin in production:

```env
PIPER_CORS_ORIGIN=https://tts-gui.example.com
```

`PIPER_MODEL_NAMES` can be a JSON array or a comma-separated list:

```env
PIPER_MODEL_NAMES=["es_MX-claude-high","es_MX-ald-medium"]
```

```env
PIPER_MODEL_NAMES=es_MX-claude-high,es_MX-ald-medium
```

Each name must follow Piper's model naming format:

```text
language_COUNTRY-voice-quality
```

Example with `es_MX-claude-high`:

```text
language = es
country = MX
voice = claude
quality = high
```

That pattern automatically generates the URL:

```text
{PIPER_HF_BASE}/es/es_MX/claude/high/es_MX-claude-high.onnx
```

## Run

```bash
python -m piper_sandbox.api
```

With the default configuration, open:

```text
http://127.0.0.1:8000
```

The web GUI lets you type text, choose a model, and play the generated audio. Press `Ctrl+Enter` or the `Hablar` button.

### Run Everything Together

```env
PIPER_SERVICE_MODE=both
```

```bash
python -m piper_sandbox.api
```

This serves:

- GUI: `GET /`
- Engine: `GET /models`, `POST /speak`

### Run Engine Only

Use this on the server that has Piper installed and enough CPU/RAM for TTS generation.

```env
PIPER_SERVICE_MODE=engine
PIPER_HOST=0.0.0.0
PIPER_PORT=8000
PIPER_CORS_ORIGIN=*
```

```bash
python -m piper_sandbox.api
```

This exposes only:

- `GET /health`
- `GET /models`
- `POST /speak`

### Run GUI Only

Use this on any PC or small server. It does not need Piper installed because it delegates synthesis to the remote engine.

```env
PIPER_SERVICE_MODE=gui
PIPER_ENGINE_URL=https://tts-engine.example.com
```

```bash
python -m piper_sandbox.api
```

This exposes only:

- `GET /`
- `GET /health`

## Endpoints

### `GET /health`

Health check for servers and Docker.

Response:

```json
{
  "status": "ok",
  "mode": "both",
  "engine": true,
  "gui": true
}
```

### `GET /models`

Lists the models available in this app. Available in `both` and `engine` modes.

Example:

```bash
curl http://127.0.0.1:8000/models
```

### `POST /speak`

Generates WAV audio. Receives JSON with `text` and `model`. Available in `both` and `engine` modes.

Example:

```bash
curl -X POST http://127.0.0.1:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hola desde Piper","model":"es_MX-ald-medium"}' \
  --output salida.wav
```

Successful response:

```text
Content-Type: audio/wav
```

### `GET /`

Shows the web GUI in `both` and `gui` modes.

If the GUI is disabled, it returns `404` with `GUI is disabled`.

## Library Usage

```python
from piper_sandbox import PiperEngine

engine = PiperEngine()
engine.synthesize_to_file(
    "Hola, esta es una prueba.",
    "salida.wav",
    model="es_MX-ald-medium",
)
```

## Wyoming Piper

The included API generates WAV files using the `piper` binary. There is also a small wrapper for starting `wyoming-piper` if you install it separately.

```python
from piper_sandbox import WyomingPiperService

service = WyomingPiperService(voice="es_MX-ald-medium")
service.start()
```

Approximate terminal equivalent:

```bash
wyoming-piper \
  --uri tcp://127.0.0.1:10200 \
  --voice es_MX-ald-medium \
  --data-dir models/wyoming-piper \
  --download-dir models/wyoming-piper
```

## Docker

Build and run with Docker Compose:

```bash
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

Run engine only in Docker:

```bash
PIPER_SERVICE_MODE=engine docker compose up --build
```

Run GUI only in Docker and connect it to a remote engine:

```bash
PIPER_SERVICE_MODE=gui PIPER_ENGINE_URL=https://tts-engine.example.com docker compose up --build
```

The image installs the `.[tts]` extra, which attempts to install `piper-tts`. Models are stored in the `piper-models` volume.

## Coolify With GitHub Apps

Recommended steps:

1. Push this project to a GitHub repository.
2. In Coolify, create a new resource from GitHub Apps.
3. Select the repository.
4. Choose deployment with `Docker Compose`.
5. Use the included `docker-compose.yml` file.
6. Configure environment variables if you want to change behavior.

Useful variables for Coolify:

```env
PIPER_SERVICE_MODE=both
PIPER_DEFAULT_MODEL=es_MX-ald-medium
PIPER_MODEL_NAMES=["es_MX-claude-high","es_MX-ald-medium","es_ES-carlfm-x_low"]
```

Coolify usually provides the public domain and external proxy. The container listens on `0.0.0.0:8000`.

If you want it as a public API only, use:

```env
PIPER_SERVICE_MODE=engine
```

If you want a GUI-only deployment that points to another engine server, use:

```env
PIPER_SERVICE_MODE=gui
PIPER_ENGINE_URL=https://tts-engine.example.com
```

## Included Models

- `es_MX-ald-medium`: Spanish Mexico, recommended for Latin American testing.
- `es_ES-carlfm-x_low`: Spanish Spain, lightweight alternative.
