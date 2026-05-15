FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIPER_HOST=0.0.0.0 \
    PIPER_PORT=8000 \
    PIPER_SERVICE_MODE=both \
    PIPER_ENGINE_URL= \
    PIPER_CORS_ORIGIN=* \
    PIPER_MODELS_DIR=/app/models/piper \
    PIPER_HF_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main \
    PIPER_DEFAULT_MODEL=es_MX-ald-medium \
    PIPER_MODEL_NAMES='["es_MX-claude-high","es_MX-ald-medium","es_ES-carlfm-x_low"]'

WORKDIR /app

COPY pyproject.toml README.md ./
COPY piper_sandbox ./piper_sandbox

ARG PIPER_INSTALL_TARGET='.[tts]'
RUN pip install --upgrade pip && pip install "$PIPER_INSTALL_TARGET"

EXPOSE 8000

CMD ["python", "-m", "piper_sandbox.api"]
