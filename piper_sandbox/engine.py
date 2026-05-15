from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
import wave
from pathlib import Path

from .models import DEFAULT_MODEL, get_model_spec


DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_USER_AGENT = "piper-sandbox/0.1"


class PiperError(RuntimeError):
    pass


class PiperEngine:
    def __init__(self, models_dir: str | Path | None = None, piper_bin: str | None = None) -> None:
        self.models_dir = Path(models_dir or os.environ.get("PIPER_MODELS_DIR", "models/piper"))
        self.piper_bin = piper_bin or os.environ.get("PIPER_BIN", "piper")
        self._piper_path: str | None = None
        self._model_path_cache: dict[str, Path] = {}

    def synthesize_to_file(self, text: str, output_file: str | Path, model: str = DEFAULT_MODEL) -> Path:
        text = text.strip()
        if not text:
            raise PiperError("Text cannot be empty")

        piper_path = self._resolve_piper()
        model_path = self.ensure_model(model)
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        process = subprocess.run(
            [piper_path, "--model", str(model_path), "--output_file", str(output_path)],
            input=text,
            text=True,
            capture_output=True,
            check=False,
        )

        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "unknown Piper error"
            raise PiperError(detail)

        return output_path

    def synthesize_bytes(self, text: str, model: str = DEFAULT_MODEL) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            self.synthesize_to_file(text, tmp_path, model=model)
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

    def ensure_model(self, model: str = DEFAULT_MODEL) -> Path:
        cached = self._model_path_cache.get(model)
        if cached is not None:
            return cached

        spec = get_model_spec(model)
        model_dir = self.models_dir / spec.name
        onnx_path = model_dir / f"{spec.name}.onnx"
        json_path = model_dir / f"{spec.name}.onnx.json"

        model_dir.mkdir(parents=True, exist_ok=True)
        self._download_if_missing(spec.onnx_url, onnx_path)
        self._download_if_missing(spec.json_url, json_path)

        self._model_path_cache[model] = onnx_path
        return onnx_path

    def audio_duration_seconds(self, wav: str | Path | bytes | bytearray) -> float:
        if isinstance(wav, (bytes, bytearray)):
            source: object = io.BytesIO(wav)
        else:
            source = str(wav)
        with wave.open(source, "rb") as audio:  # type: ignore[arg-type]
            return audio.getnframes() / float(audio.getframerate())

    def _resolve_piper(self) -> str:
        if self._piper_path is not None:
            return self._piper_path

        resolved = shutil.which(self.piper_bin)
        if resolved is None:
            raise PiperError(
                f"Piper executable {self.piper_bin!r} was not found. Install it or set PIPER_BIN."
            )
        self._piper_path = resolved
        return resolved

    def _download_if_missing(self, url: str, path: Path) -> None:
        if path.exists() and path.stat().st_size > 0:
            return

        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.download")
        request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, open(
                tmp_path, "wb"
            ) as out:
                shutil.copyfileobj(response, out)
            tmp_path.replace(path)
        except Exception as exc:  # noqa: BLE001 - preserve the URL in the user-facing error.
            tmp_path.unlink(missing_ok=True)
            raise PiperError(f"Could not download Piper model from {url}: {exc}") from exc
