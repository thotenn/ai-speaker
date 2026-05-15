# Adaptive Chunked TTS Streaming — V1 Implementation Plan

This is the engineering playbook. The phase-by-phase task list lives in `CHECKLIST.md`. The behavior contract lives in `SPEC.md`. This document explains *how* to build V1 and *why* the boring decisions were made the way they were.

## Scope Reminder

V1 only: splitter, single NDJSON endpoint, reference GUI updates, env flag gate. No hardware detection, no benchmark, no adaptive controller, no prefetch, no parallel workers. Those are V2 and have their own future spec.

Development mode: `PIPER_SERVICE_MODE=both`. After the endpoint stabilizes, validate `engine` and `gui` modes once.

## New Modules (V1)

```text
piper_sandbox/
  chunks.py        # pure text splitter; no HTTP, no Piper
  api.py           # adds POST /speak/chunks handler + /health field
  engine.py        # unchanged (PiperEngine.synthesize_bytes already returns bytes)
```

Not in V1: `hardware.py`, `benchmark.py`, `chunk_controller.py`. Those go in V2 when adaptive sizing is needed.

## Data Types

```python
@dataclass(frozen=True)
class ChunkConfig:
    target_chars: int
    min_chars: int
    max_chars: int


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    chars: int
    split_reason: str  # "paragraph" | "sentence" | "strong" | "comma" | "space" | "hard" | "single"
```

`ChunkConfig` is built once from env at startup. Per-request events are plain dicts because they are serialized to NDJSON immediately.

`split_reason` is the boundary type that produced this chunk's *end* cut. The first/only chunk uses `"single"`.

## Splitter Algorithm

The splitter is the only non-trivial logic in V1. Keep it deterministic, pure, and exhaustively unit-tested.

### Pre-processing

```python
text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
if not text:
    raise ValueError("empty")
```

### Two-level algorithm

**Outer pass — pack paragraphs**:

```python
paragraphs = re.split(r"\n{2,}", text)
current = ""
for p in paragraphs:
    p = p.strip()
    if not p:
        continue
    candidate = f"{current}\n\n{p}" if current else p

    if len(candidate) <= config.target_chars:
        current = candidate
        continue

    # candidate exceeds target
    if current and len(current) >= config.min_chars:
        emit(current, reason="paragraph")
        current = p
        continue

    # current is empty or too small; we *must* include p, which may overflow.
    # If p alone exceeds max_chars, recurse into sentences.
    if len(p) > config.max_chars:
        for sub in split_oversized(p, config):
            if current:
                emit(current, reason="paragraph")
                current = ""
            emit(sub.text, reason=sub.reason)
    else:
        current = candidate  # accept overflow up to max_chars

emit_if_any(current, reason="paragraph" if multiple else "single")
```

**Inner pass — split an oversized unit** (`split_oversized`):

Used when a single paragraph is longer than `max_chars`. Walk the boundary priority list, looking for the *last* matching boundary inside `[window_start, window_end]`:

```python
def split_oversized(text, config):
    pieces = []
    remaining = text
    while len(remaining) > config.max_chars:
        idx, reason = find_split(remaining, config)
        pieces.append(TextChunk(text=remaining[:idx].rstrip(), reason=reason, ...))
        remaining = remaining[idx:].lstrip()
    if remaining:
        pieces.append(TextChunk(text=remaining, reason="tail", ...))
    return pieces


def find_split(text, config):
    window_start = max(config.min_chars, int(config.target_chars * 0.65))
    window_end = min(len(text), config.max_chars)

    boundary_levels = [
        ("sentence", ".!?"),
        ("strong",   ";:"),
        ("comma",    ","),
    ]

    for reason, chars in boundary_levels:
        # find the LAST char in chars within the window, then advance past it
        idx = last_index_of_any(text, chars, window_start, window_end)
        if idx is not None:
            # include the boundary char and any following whitespace in the left piece
            cut = idx + 1
            while cut < len(text) and text[cut] == " ":
                cut += 1
            return cut, reason

    # whitespace fallback
    idx = text.rfind(" ", window_start, window_end)
    if idx > 0:
        return idx + 1, "space"

    return window_end, "hard"
```

### Properties to test

- Total reassembled chunks equal the original text modulo whitespace normalization.
- Order preserved.
- No empty chunks.
- Every `chars` matches `len(text)`.
- `split_reason` matches the boundary actually used.
- Three short paragraphs (sum ≤ target) → one chunk.
- A fourth paragraph that would overflow target by a lot → new chunk.
- Single sentence longer than max → split at comma.
- Run-on string with no punctuation or whitespace → hard split at exactly `max_chars`.

## Endpoint Handler

`api.py` gains one method route (`do_POST` already exists for `/speak`; add a branch for `/speak/chunks`).

### Pre-stream validation

```python
if not chunks_enabled:
    return _send_error(501, "Chunked TTS is disabled")
try:
    payload = json.loads(body)
    text = str(payload.get("text", "")).strip()
    model = str(payload.get("model", DEFAULT_MODEL))
    if not text:
        return _send_error(400, "text cannot be empty")
    get_model_spec(model)  # raises KeyError → 400
    chunks = split_text(text, chunk_config)  # raises ValueError → 400
except (json.JSONDecodeError, KeyError, ValueError) as exc:
    return _send_error(400, str(exc))
```

### Streaming response

```python
self.send_response(200)
self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
self.send_header("Cache-Control", "no-cache")
self.send_header("X-Accel-Buffering", "no")
self._send_cors_headers()
# do NOT send Content-Length; rely on Transfer-Encoding: chunked or connection close
self.end_headers()

def write_event(obj):
    self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    self.wfile.write(b"\n")
    self.wfile.flush()

write_event({"type": "meta", "model": model, "chunks": len(chunks),
             "target_chars": cfg.target_chars,
             "min_chars": cfg.min_chars,
             "max_chars": cfg.max_chars})

for chunk in chunks:
    try:
        t0 = time.perf_counter()
        wav = self.engine.synthesize_bytes(chunk.text, model=model)
        synth = time.perf_counter() - t0
        duration = wav_duration_seconds(wav)
    except PiperError as exc:
        write_event({"type": "error", "index": chunk.index, "message": str(exc)})
        return

    write_event({
        "type": "chunk",
        "index": chunk.index,
        "chars": chunk.chars,
        "split_reason": chunk.split_reason,
        "synthesis_seconds": round(synth, 3),
        "duration_seconds": round(duration, 3),
        "rtf": round(synth / duration, 3) if duration > 0 else None,
        "audio_base64": base64.b64encode(wav).decode("ascii"),
    })

write_event({"type": "done"})
```

### HTTP chunked transfer note

`BaseHTTPRequestHandler` does not automatically apply `Transfer-Encoding: chunked`. Two options:

1. **Connection: close** (simplest). Omit `Content-Length`, let the server close the TCP connection when the generator finishes. NDJSON consumers handle this fine.
2. **Manual chunked encoding**. Write `<size hex>\r\n<bytes>\r\n` per line and `0\r\n\r\n` at the end. Better for keep-alive but more code.

V1 uses option 1. If proxies misbehave, revisit.

### WAV duration

Reuse `PiperEngine.audio_duration_seconds` (already exists). It takes a file path; for the bytes path either write to a temp file (already done inside `synthesize_bytes`) or add a sibling that reads from `io.BytesIO`:

```python
def wav_duration_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate())
```

Adding this helper is cheaper than touching `synthesize_bytes` to return both the wav and the duration.

## Config Loading

Add to `config.py` (or inline in `api.py`):

```python
chunks_enabled = env_bool("PIPER_CHUNKS_ENABLED", default=False)
chunk_config = ChunkConfig(
    target_chars=env_int("PIPER_CHUNK_TARGET_CHARS", 350),
    min_chars=env_int("PIPER_CHUNK_MIN_CHARS", 120),
    max_chars=env_int("PIPER_CHUNK_MAX_CHARS", 700),
)
```

Validate bounds at startup (`min <= target <= max`, all > 0) and fail loud if misconfigured.

## `/health` Update

Add a single field. Other fields unchanged.

```python
self._send_json({
    ...existing fields...,
    "chunks_enabled": self.chunks_enabled,
})
```

## Reference GUI Changes

The HTML inlined in `api.py` (`INDEX_HTML`) needs:

1. On load, fetch `/health` and read `chunks_enabled`.
2. If enabled, replace `say()` to call `/speak/chunks` and parse NDJSON.
3. Maintain an audio queue and play chunks in order.
4. Show status: `Generando...`, `Reproduciendo (i/N)`, `Listo`, `Error`.

NDJSON parsing in fetch streams (the standard browser pattern):

```javascript
const response = await fetch(apiUrl('/speak/chunks'), { method: 'POST', ... });
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';
let queue = [];
let playing = false;

async function pump() {
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl);
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      handleEvent(JSON.parse(line));
    }
  }
}
```

`handleEvent` decodes `audio_base64` into a Blob (`atob` → `Uint8Array` → `Blob(['audio/wav'])`), pushes to queue, and triggers playback if idle. On `audio.ended`, shift the queue and play next.

Small audible gaps between chunks are acceptable in V1.

## Tests

Add pytest as an optional dev dependency:

```toml
[project.optional-dependencies]
dev = ["pytest>=8"]
```

Install with `pip install -e '.[dev]'`. Run with `pytest`. No conftest needed.

Tests in `tests/`:

- `tests/test_chunks.py` — splitter, ~15 cases (see CHECKLIST).
- `tests/test_speak_chunks_endpoint.py` — uses a fake engine that returns a canned WAV; spins up `ThreadingHTTPServer` on an ephemeral port; asserts NDJSON sequence, status codes, headers, base64 decoding.
- `tests/test_health.py` — confirms `chunks_enabled` field.

No real Piper synthesis in the default suite. Manual smoke tests stay in `## Manual Validation` below.

To stub the engine, monkeypatch `PiperRequestHandler.engine = FakeEngine()` in a fixture. `FakeEngine.synthesize_bytes` returns a precomputed 1-second silent WAV (tests/data/silence.wav, ~44 bytes header + samples).

## Manual Validation

After implementation, run these by hand:

```bash
# Disabled flag → 501
PIPER_CHUNKS_ENABLED=false python -m piper_sandbox.api &
curl -i -X POST http://127.0.0.1:8000/speak/chunks \
  -H 'Content-Type: application/json' -d '{"text":"hola","model":"es_MX-ald-medium"}'
# expect HTTP/1.1 501

# Enabled, short text → 1 chunk
PIPER_CHUNKS_ENABLED=true python -m piper_sandbox.api &
curl -N -X POST http://127.0.0.1:8000/speak/chunks \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hola mundo.","model":"es_MX-ald-medium"}'
# expect: meta {chunks:1} ... chunk {index:0,split_reason:"single"} ... done

# Long text → multiple chunks streamed progressively
curl -N -X POST http://127.0.0.1:8000/speak/chunks \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg t "$(printf '%.0s Lorem ipsum dolor sit amet. ' {1..50})" '{text:$t,model:"es_MX-ald-medium"}')"
# expect: meta {chunks:N>1} ... N chunk events arriving over time ... done

# /speak unchanged
curl -X POST http://127.0.0.1:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hola","model":"es_MX-ald-medium"}' --output /tmp/out.wav
file /tmp/out.wav   # expect: RIFF (little-endian) data, WAVE audio
```

After each change run `python -m compileall piper_sandbox` to catch syntax breaks fast.

## Service-Mode Validation

Once `both` mode is green:

- `PIPER_SERVICE_MODE=engine PIPER_CHUNKS_ENABLED=true` → `/speak/chunks` works, `/` returns 404.
- `PIPER_SERVICE_MODE=gui PIPER_ENGINE_URL=http://localhost:8001 PIPER_CHUNKS_ENABLED=true` → GUI HTML loads but `/speak/chunks` returns 404; browser calls remote engine. `chunks_enabled` in `/health` is `false` in this process (gui-only) and `true` on the remote engine; the GUI should read `/health` from the engine URL, not from itself.

That last point matters: the GUI JavaScript must fetch `${ENGINE_URL || ''}/health` to get the right `chunks_enabled` value. The handler templates `__ENGINE_URL__` into the page; reuse that variable in the health fetch too.

## Deployment Notes

- Ampere arm64, 8 GB RAM target: `piper-tts` wheels for `linux/arm64` exist on PyPI. Confirm before deploying; if not, fall back to the official Piper binary on `PATH`. This is independent of the feature.
- Reverse proxies (nginx, Coolify's default) buffer responses by default. For `/speak/chunks` we set `X-Accel-Buffering: no`; for nginx the operator must also ensure `proxy_buffering off` on the route (or accept that the first chunk arrives only after the whole stream ends, defeating the feature).
- Docker volume `piper-models` already persists downloaded models — no change.

## Out of Scope (V2)

When V2 starts, the relevant additions will be:

1. `hardware.py` — `os.cpu_count()`, `/proc/meminfo` parser; conservative fallback.
2. `benchmark.py` — synthesize a fixed phrase once per model, cache RTF in memory.
3. `chunk_controller.py` — produce `ChunkConfig` from `(env_bounds, hardware, benchmark, recent_observations)`; smooth changes to avoid oscillation.
4. Multi-worker prefetch — `concurrent.futures.ThreadPoolExecutor` with `max_workers=PIPER_CHUNK_MAX_WORKERS`; serialize wfile writes from a single thread that drains a future queue in order.
5. Binary transport — replace `audio_base64` with `{audio_url: "/speak/chunks/{job}/{i}.wav"}`; introduce a per-request job table with TTL.
6. Web Audio API playback queue in the GUI.

V1 deliberately leaves all of these alone.

## Done Definition

V1 ships when:

- `/speak` byte-compatible with main.
- `/speak/chunks` streams valid NDJSON when `PIPER_CHUNKS_ENABLED=true`, returns 501 otherwise.
- Splitter packs paragraphs/sentences naturally and has unit tests covering the cases in CHECKLIST Phase 1.
- Long text in the reference GUI starts playing before full synthesis completes.
- `both`, `engine`, `gui` modes all work.
- `compileall` passes; `pytest` passes.
