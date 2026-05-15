import io
import json
import threading
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from typing import Iterator

import pytest

from piper_sandbox.api import PiperRequestHandler
from piper_sandbox.chunks import ChunkConfig
from piper_sandbox.engine import PiperError
from piper_sandbox.models import DEFAULT_MODEL


def _silent_wav(seconds: float = 0.1, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class _FakeEngine:
    def __init__(self, wav: bytes, duration: float) -> None:
        self._wav = wav
        self._duration = duration
        self.calls = 0

    def synthesize_bytes(self, text: str, model: str = DEFAULT_MODEL) -> bytes:
        if not text.strip():
            raise PiperError("Text cannot be empty")
        self.calls += 1
        return self._wav

    def audio_duration_seconds(self, wav) -> float:
        return self._duration


@pytest.fixture
def server(monkeypatch) -> Iterator[str]:
    wav = _silent_wav(0.1)
    fake = _FakeEngine(wav, 0.1)
    monkeypatch.setattr(PiperRequestHandler, "engine", fake)
    monkeypatch.setattr(PiperRequestHandler, "chunks_enabled", True)
    monkeypatch.setattr(
        PiperRequestHandler,
        "chunk_config",
        ChunkConfig(target_chars=50, min_chars=20, max_chars=100),
    )
    monkeypatch.setattr(PiperRequestHandler, "service_mode", "both")
    monkeypatch.setattr(PiperRequestHandler, "engine_url", "")
    monkeypatch.setattr(PiperRequestHandler, "cors_origin", "*")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), PiperRequestHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def _post(url: str, body: dict) -> urllib.request.http.client.HTTPResponse:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(req, timeout=5)


def _post_raw(url: str, body: bytes, content_type: str = "application/json"):
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": content_type}
    )
    return urllib.request.urlopen(req, timeout=5)


def _read_ndjson(response) -> list[dict]:
    events = []
    for raw in response:
        line = raw.decode("utf-8").strip()
        if line:
            events.append(json.loads(line))
    return events


def test_health_exposes_chunks_enabled(server):
    response = urllib.request.urlopen(f"{server}/health", timeout=5)
    body = json.loads(response.read())
    assert body["chunks_enabled"] is True


def test_health_chunks_disabled_when_flag_off(server, monkeypatch):
    monkeypatch.setattr(PiperRequestHandler, "chunks_enabled", False)
    response = urllib.request.urlopen(f"{server}/health", timeout=5)
    body = json.loads(response.read())
    assert body["chunks_enabled"] is False


def test_health_chunks_false_in_gui_mode(server, monkeypatch):
    monkeypatch.setattr(PiperRequestHandler, "service_mode", "gui")
    response = urllib.request.urlopen(f"{server}/health", timeout=5)
    body = json.loads(response.read())
    assert body["chunks_enabled"] is False


def test_disabled_returns_501(server, monkeypatch):
    monkeypatch.setattr(PiperRequestHandler, "chunks_enabled", False)
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{server}/speak/chunks", {"text": "hola", "model": DEFAULT_MODEL})
    assert exc.value.code == 501


def test_gui_mode_returns_404(server, monkeypatch):
    monkeypatch.setattr(PiperRequestHandler, "service_mode", "gui")
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{server}/speak/chunks", {"text": "hola", "model": DEFAULT_MODEL})
    assert exc.value.code == 404


def test_short_text_emits_meta_chunk_done(server):
    response = _post(f"{server}/speak/chunks", {"text": "Hola.", "model": DEFAULT_MODEL})
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("application/x-ndjson")
    events = _read_ndjson(response)
    assert [e["type"] for e in events] == ["meta", "chunk", "done"]
    assert events[0]["chunks"] == 1
    assert events[1]["index"] == 0
    assert events[1]["split_reason"] == "single"


def test_long_text_emits_multiple_chunks_sequentially(server):
    text = "Una frase de prueba bastante larga. " * 20
    response = _post(f"{server}/speak/chunks", {"text": text, "model": DEFAULT_MODEL})
    events = _read_ndjson(response)
    chunk_events = [e for e in events if e["type"] == "chunk"]
    assert len(chunk_events) >= 2
    assert [e["index"] for e in chunk_events] == list(range(len(chunk_events)))
    assert events[0]["type"] == "meta"
    assert events[-1]["type"] == "done"
    assert events[0]["chunks"] == len(chunk_events)


def test_chunk_audio_base64_decodes_to_riff(server):
    response = _post(f"{server}/speak/chunks", {"text": "Hola.", "model": DEFAULT_MODEL})
    events = _read_ndjson(response)
    chunk = next(e for e in events if e["type"] == "chunk")
    import base64
    audio = base64.b64decode(chunk["audio_base64"])
    assert audio.startswith(b"RIFF")


def test_empty_text_returns_400(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{server}/speak/chunks", {"text": "   ", "model": DEFAULT_MODEL})
    assert exc.value.code == 400


def test_unknown_model_returns_400(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{server}/speak/chunks", {"text": "Hola.", "model": "es_ZZ-nope-low"})
    assert exc.value.code == 400


def test_invalid_json_returns_400(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_raw(f"{server}/speak/chunks", b"{not json")
    assert exc.value.code == 400


def test_default_request_omits_chunk_text(server):
    response = _post(f"{server}/speak/chunks", {"text": "Hola.", "model": DEFAULT_MODEL})
    events = _read_ndjson(response)
    chunk = next(e for e in events if e["type"] == "chunk")
    assert "text" not in chunk


def test_include_text_query_adds_chunk_text(server):
    response = _post(
        f"{server}/speak/chunks?include_text=1", {"text": "Hola.", "model": DEFAULT_MODEL}
    )
    events = _read_ndjson(response)
    chunk = next(e for e in events if e["type"] == "chunk")
    assert chunk["text"] == "Hola."


def test_speak_unchanged_returns_audio_wav(server):
    response = _post(f"{server}/speak", {"text": "Hola.", "model": DEFAULT_MODEL})
    assert response.status == 200
    assert response.headers["Content-Type"] == "audio/wav"
    body = response.read()
    assert body.startswith(b"RIFF")


def test_meta_event_includes_config_bounds(server):
    response = _post(f"{server}/speak/chunks", {"text": "Hola.", "model": DEFAULT_MODEL})
    events = _read_ndjson(response)
    meta = events[0]
    assert meta["target_chars"] == 50
    assert meta["min_chars"] == 20
    assert meta["max_chars"] == 100
    assert meta["model"] == DEFAULT_MODEL


def test_synthesis_error_mid_stream_emits_error_event(server, monkeypatch):
    # Engine succeeds for chunk 0 then fails on chunk 1.
    fake = PiperRequestHandler.engine
    original_synth = fake.synthesize_bytes
    state = {"calls": 0}

    def failing_synth(text, model=DEFAULT_MODEL):
        state["calls"] += 1
        if state["calls"] == 2:
            raise PiperError("synthetic failure")
        return original_synth(text, model=model)

    monkeypatch.setattr(fake, "synthesize_bytes", failing_synth)

    text = "Una frase. " * 25
    response = _post(f"{server}/speak/chunks", {"text": text, "model": DEFAULT_MODEL})
    events = _read_ndjson(response)
    types = [e["type"] for e in events]
    assert "error" in types
    assert "done" not in types
    error_event = next(e for e in events if e["type"] == "error")
    assert "synthetic failure" in error_event["message"]


def test_payload_too_large_returns_413(server):
    big = b"x" * (1024 * 1024 + 10)
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_raw(f"{server}/speak/chunks", big)
    assert exc.value.code == 413
