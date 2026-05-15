# Adaptive Chunked TTS Streaming Implementation Plan

## Purpose

This document explains how to implement adaptive chunked TTS streaming. It is not the task checklist. The phase-by-phase implementation checklist lives in `CHECKLIST.md`.

The implementation should be approached as an engine-first feature. The GUI is only a reference client and should not drive engine design decisions.

## Development Mode

Develop and test this feature in `both` mode until the core behavior is stable:

```env
PIPER_SERVICE_MODE=both
```

This keeps the engine endpoints and sample GUI available from one process while still preserving compatibility with `engine` and `gui` deployment modes.

## Implementation Order

Recommended order:

1. Preserve current `/speak` behavior with baseline tests.
2. Build a pure text splitter with no Piper dependency.
3. Add hardware profile detection.
4. Add model benchmark and speed estimation.
5. Add adaptive chunk sizing controller.
6. Add `/speak/chunks` as an additive endpoint.
7. Add sample GUI chunk playback.
8. Add timing feedback and adaptive adjustment between chunks.
9. Validate Docker and split GUI/engine modes.
10. Update public README after behavior is stable.

Do not start with the GUI. The GUI should consume engine behavior that already exists and is testable.

## Proposed Modules

Suggested module layout:

```text
piper_sandbox/
  chunks.py
  hardware.py
  benchmark.py
  chunk_controller.py
  api.py
  engine.py
```

Responsibilities:

```text
chunks.py           Pure text splitting logic.
hardware.py         CPU/memory/platform profile.
benchmark.py        Piper speed measurement and RTF calculation.
chunk_controller.py Chunk sizing decisions from env + hardware + benchmark + observations.
api.py              HTTP endpoints and NDJSON transport.
engine.py           Existing Piper synthesis wrapper.
```

Keep the splitter and controller independent from HTTP so they can be tested quickly without Piper.

## Data Types

Suggested dataclasses:

```python
@dataclass(frozen=True)
class ChunkConfig:
    target_chars: int
    min_chars: int
    max_chars: int
    prefetch: int
    max_workers: int
    safety_margin_seconds: float


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    chars: int
    split_reason: str


@dataclass(frozen=True)
class HardwareProfile:
    cpu_count: int
    memory_total_mb: int | None
    memory_available_mb: int | None
    platform: str


@dataclass(frozen=True)
class BenchmarkResult:
    model: str
    synthesis_seconds: float
    audio_duration_seconds: float
    rtf: float
```

Runtime chunk events can be plain dictionaries because they are serialized to NDJSON.

## Workflow: Text Splitting

The splitter should pack semantic units into chunks until adding the next unit would exceed the adaptive target too much.

High-level algorithm:

```text
normalize line endings
tokenize text into paragraphs while preserving punctuation
for each paragraph:
  if current chunk is empty:
    add paragraph if it fits target/max
  else if current + paragraph fits target:
    add paragraph
  else if current is above min_chars:
    emit current chunk
    start new chunk with paragraph
  else:
    try to split paragraph by sentence/comma/space to satisfy min/max
emit final chunk
```

Important behavior:

If `target_chars=1000` and 3 paragraphs total 917 characters, emit those 3 paragraphs together. If the next paragraph is 200 characters, do not add it just because 83 characters remain. Start the next chunk instead.

Boundary search should work near the target, not from the start of the text.

Suggested boundary priority:

```text
paragraph > sentence > strong_separator > comma > whitespace > hard
```

Suggested sentence punctuation:

```text
. ? !
```

Suggested strong separators:

```text
; :
```

## Workflow: Boundary Selection

For a candidate string longer than `target_chars`, find the best split point near the target.

Pseudocode:

```python
def find_split(text, config):
    window_start = max(config.min_chars, int(config.target_chars * 0.65))
    window_end = min(len(text), config.max_chars)

    for reason, chars in boundary_groups:
        idx = find_last_boundary(text, chars, window_start, window_end)
        if idx is not None:
            return idx, reason

    idx = find_last_whitespace(text, window_start, window_end)
    if idx is not None:
        return idx, "space"

    return min(config.max_chars, len(text)), "hard"
```

The splitter should keep punctuation in the previous chunk. That helps Piper produce more natural prosody at the end of each chunk.

## Workflow: Hardware Profile

Hardware detection should be lightweight and safe.

Initial implementation:

```python
cpu_count = os.cpu_count() or 1
memory_total_mb, memory_available_mb = parse_proc_meminfo_if_linux()
```

Do not add `psutil` initially. The feature should work without optional native dependencies.

Fallback behavior:

```text
unknown memory -> use conservative chunk config
unknown cpu -> use cpu_count=1
```

Container environments may not expose accurate host limits. Treat this profile as a hint, not a source of truth.

## Workflow: Benchmark

The benchmark should measure real Piper behavior for the selected model.

Pseudocode:

```python
def benchmark_model(engine, model):
    start = time.perf_counter()
    wav = engine.synthesize_bytes(BENCHMARK_TEXT, model=model)
    synthesis_seconds = time.perf_counter() - start
    audio_duration_seconds = read_wav_duration(wav)
    rtf = synthesis_seconds / audio_duration_seconds
    return BenchmarkResult(model, synthesis_seconds, audio_duration_seconds, rtf)
```

Interpretation:

```text
rtf < 0.35  -> very fast
rtf < 0.75  -> fast enough
rtf < 1.00  -> near realtime
rtf >= 1.00 -> slow
```

The benchmark must not make startup fragile. If benchmark-on-start fails, log the failure and fall back to static configuration.

## Workflow: Adaptive Chunk Controller

The controller combines env defaults, hardware, benchmark, and runtime observations.

Initial static bounds come from env:

```env
PIPER_CHUNK_TARGET_CHARS=350
PIPER_CHUNK_MIN_CHARS=120
PIPER_CHUNK_MAX_CHARS=700
```

Startup adjustment example:

```text
fast model + multiple CPUs -> increase target toward max
slow model or single CPU -> reduce target toward min
unknown benchmark -> keep env target
```

Runtime adjustment should be conservative.

Core rule:

```text
expected_generation_time(next_chunk) + delivery_overhead < current_audio_duration - safety_margin
```

If this rule is violated, reduce target for future chunks. If it is comfortably satisfied for several chunks, increase target slightly.

Avoid oscillation:

```text
change target by at most 10-20% per adjustment
keep target inside min/max
smooth observations over recent chunks
```

## Workflow: Chunk Endpoint

Add endpoint:

```text
POST /speak/chunks
```

Use NDJSON for the first implementation:

```text
Content-Type: application/x-ndjson
```

Event sequence:

```text
meta -> chunk -> chunk -> ... -> done
```

Example event generation:

```python
yield ndjson({"type": "meta", "chunks": len(chunks), "model": model})

for chunk in chunks:
    started = time.perf_counter()
    wav = engine.synthesize_bytes(chunk.text, model=model)
    synthesis_seconds = time.perf_counter() - started
    duration = wav_duration(wav)
    yield ndjson({
        "type": "chunk",
        "index": chunk.index,
        "text": chunk.text,
        "chars": chunk.chars,
        "split_reason": chunk.split_reason,
        "synthesis_seconds": synthesis_seconds,
        "duration_seconds": duration,
        "rtf": synthesis_seconds / duration,
        "audio_base64": base64.b64encode(wav).decode("ascii"),
    })

yield ndjson({"type": "done"})
```

Because this project currently uses `http.server`, true incremental flushing may require explicitly writing to `wfile` and flushing after every line rather than building one response body in memory.

## Workflow: HTTP Implementation With `http.server`

The current server is based on `BaseHTTPRequestHandler`. For chunked NDJSON, avoid `Content-Length` and use HTTP chunked transfer if practical, or write newline-delimited JSON with flushes.

Implementation sketch:

```python
self.send_response(HTTPStatus.OK)
self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
self.send_header("Cache-Control", "no-cache")
self._send_cors_headers()
self.end_headers()

for event in generate_events(...):
    self.wfile.write(json.dumps(event).encode("utf-8") + b"\n")
    self.wfile.flush()
```

If client/proxy buffering prevents progressive delivery, revisit transport later with SSE, WebSocket, or a small ASGI server. Do not introduce that complexity before proving the splitter and endpoint contract.

## Workflow: GUI Reference Client

The GUI should keep using `/speak` for short text and switch to `/speak/chunks` for long text.

Client algorithm:

```text
user clicks Speak
if text length < threshold:
  call /speak
  play one blob
else:
  call /speak/chunks
  parse NDJSON stream
  queue each audio blob
  start playback when first blob arrives
  continue with next blob on audio ended
```

The GUI does not need perfect gapless playback in the first version. It should demonstrate engine behavior clearly.

Later, use Web Audio API for tighter scheduling:

```text
decodeAudioData -> schedule buffer source at next exact playback time
```

## Workflow: Testing

Add tests in layers. Do not start with integration tests.

Recommended order:

1. Pure splitter tests.
2. Hardware parser tests.
3. Benchmark math tests with fake engine/audio duration.
4. Controller tests with deterministic inputs.
5. Endpoint contract tests with fake synthesis where possible.
6. Manual Docker smoke tests.

Keep expensive Piper synthesis out of the default unit test suite. Real synthesis tests can be marked/manual until there is a proper test runner configuration.

## Workflow: Backward Compatibility

Before and after every endpoint change:

```bash
python -m compileall piper_sandbox
```

Manual smoke test:

```bash
curl -X POST http://127.0.0.1:8000/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hola desde Piper","model":"es_MX-claude-high"}' \
  --output /tmp/speak.wav
```

`/speak` must continue returning `audio/wav` exactly as before.

## Workflow: Configuration

Introduce env variables in `.env.example` only when the implementation uses them.

Proposed variables:

```env
PIPER_CHUNKS_ENABLED=true
PIPER_CHUNK_THRESHOLD_CHARS=600
PIPER_CHUNK_ADAPTIVE=true
PIPER_CHUNK_TARGET_CHARS=350
PIPER_CHUNK_MIN_CHARS=120
PIPER_CHUNK_MAX_CHARS=700
PIPER_CHUNK_PREFETCH=1
PIPER_CHUNK_MAX_WORKERS=1
PIPER_CHUNK_SAFETY_MARGIN_SECONDS=1.5
PIPER_CHUNK_BENCHMARK_ON_START=false
```

Avoid adding configuration that is not read by code yet, except in documentation/spec.

## Workflow: Deployment Modes

After the endpoint works in `both` mode, validate:

```env
PIPER_SERVICE_MODE=engine
```

Expected:

```text
/health works
/models works
/speak works
/speak/chunks works
/ returns GUI disabled
```

Then validate:

```env
PIPER_SERVICE_MODE=gui
PIPER_ENGINE_URL=https://engine.example.com
```

Expected:

```text
/ works
/health works
GUI calls remote /models and /speak/chunks
```

## Notes On Prefetch

The first version can generate chunks sequentially and still improve perceived latency because chunk 0 is emitted before the full text is complete.

Only add worker-based prefetch after the endpoint contract is stable. Parallel Piper generation can overload CPU and harm latency on small machines.

When prefetch is added, keep default conservative:

```env
PIPER_CHUNK_PREFETCH=1
PIPER_CHUNK_MAX_WORKERS=1
```

This means one chunk ahead conceptually, but not necessarily multiple concurrent Piper processes.

## Done Definition

The feature is ready when:

```text
/speak remains unchanged
/speak/chunks streams valid NDJSON
long text starts playback earlier in the sample GUI
chunk metadata exposes timing information
splitter/controller have unit tests
both mode works for development
engine/gui split mode still works
```
