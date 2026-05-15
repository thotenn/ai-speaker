from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .config import load_env


@dataclass(frozen=True)
class ModelSpec:
    name: str
    language: str
    country: str
    quality: str
    onnx_url: str
    json_url: str


load_env()

DEFAULT_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
DEFAULT_MODEL_NAMES = [
    "es_MX-claude-high",
    "es_MX-ald-medium",
    "es_ES-carlfm-x_low",
]

HF_BASE = os.environ.get("PIPER_HF_BASE", DEFAULT_HF_BASE).rstrip("/")
DEFAULT_MODEL = os.environ.get("PIPER_DEFAULT_MODEL", "es_MX-ald-medium")


def parse_model_names(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_MODEL_NAMES

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in value.split(",")]

    if not isinstance(parsed, list):
        raise ValueError("PIPER_MODEL_NAMES must be a JSON array or a comma-separated list")

    names = [str(item).strip() for item in parsed if str(item).strip()]
    return names or DEFAULT_MODEL_NAMES


def model_spec_from_name(name: str) -> ModelSpec:
    try:
        locale, voice_and_quality = name.split("-", 1)
        language, country = locale.split("_", 1)
        voice, quality = voice_and_quality.rsplit("-", 1)
    except ValueError as exc:
        raise ValueError(
            f"Invalid Piper model name {name!r}. Expected format: language_COUNTRY-voice-quality"
        ) from exc

    model_path = f"{language}/{language}_{country}/{voice}/{quality}/{name}.onnx"
    return ModelSpec(
        name=name,
        language=language,
        country=country,
        quality=quality,
        onnx_url=f"{HF_BASE}/{model_path}",
        json_url=f"{HF_BASE}/{model_path}.json",
    )


MODELS: dict[str, ModelSpec] = {
    name: model_spec_from_name(name) for name in parse_model_names(os.environ.get("PIPER_MODEL_NAMES"))
}


def get_model_spec(name: str) -> ModelSpec:
    try:
        return MODELS[name]
    except KeyError as exc:
        available = ", ".join(sorted(MODELS))
        raise KeyError(f"Unknown model {name!r}. Available models: {available}") from exc
