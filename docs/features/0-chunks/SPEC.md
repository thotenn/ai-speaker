# Adaptive Chunked TTS Streaming

## Summary

Add adaptive text chunking and chunked audio delivery to the Piper engine so long text can start playing quickly while the next chunks are synthesized in the background.

The current `/speak` endpoint generates one complete WAV before returning. That works well for short text, but long text creates a poor perceived latency because the user waits for the entire synthesis before hearing anything.

This feature keeps the existing `/speak` behavior and adds a chunked path for long text.

## Goals

- Start audio playback earlier for long text.
- Split text intelligently using paragraphs, sentence punctuation, clauses, and whitespace.
- Adapt chunk size to the current machine and selected model.
- Keep chunks large enough to avoid audible gaps and excessive overhead.
- Keep chunks small enough so the next chunk is ready before the current one finishes playing.
- Preserve the perception of one continuous audio response.
- Expose the feature through engine APIs that can be reused by a larger project.
- Keep the web GUI as a demonstration client only.

## Non-Goals

- Do not replace `/speak`; it remains the simple full-WAV endpoint.
- Do not implement true continuous WAV streaming in the first version.
- Do not require the GUI for engine functionality.
- Do not require external services.
- Do not use AI/LLM-based chunking.
- Do not optimize for perfect prosody at chunk boundaries in the first version.

## Service Mode For Development

All implementation and tests for this feature will be developed in `both` mode during the feature phases:

```env
PIPER_SERVICE_MODE=both
```

This allows the same process to expose both the engine APIs and the example GUI while tests are being developed.

## Existing Behavior

Current endpoint:

```text
POST /speak
```

Input:

```json
{
  "text": "Hello",
  "model": "es_MX-claude-high"
}
```

Output:

```text
Content-Type: audio/wav
```

The response is returned only after Piper finishes generating the whole WAV.

## Proposed Behavior

Add a chunked endpoint while keeping `/speak` unchanged.

Initial proposed endpoint:

```text
POST /speak/chunks
```

Input:

```json
{
  "text": "Long text...",
  "model": "es_MX-claude-high"
}
```

Output format for the first implementation:

```text
Content-Type: application/x-ndjson
```

Each line is one JSON event.

Example:

```jsonl
{"type":"meta","model":"es_MX-claude-high","chunks":4,"mode":"adaptive"}
{"type":"chunk","index":0,"text":"...","duration_seconds":8.4,"audio_base64":"..."}
{"type":"chunk","index":1,"text":"...","duration_seconds":9.1,"audio_base64":"..."}
{"type":"done"}
```

The NDJSON approach is intentionally simple and testable. A later version may move to a session-based API, binary multipart response, Server-Sent Events, WebSocket, or MediaSource-based playback.

## Chunking Strategy

The splitter receives text plus adaptive sizing constraints:

```text
target_chars
min_chars
max_chars
```

The target is not a hard limit. It is the preferred chunk size based on machine capability and model speed.

The splitter should preserve natural language boundaries whenever possible.

Boundary priority:

```text
paragraph boundary
sentence boundary: . ? !
strong separator: ; :
clause boundary: ,
whitespace
hard split
```

Important rule:

The first paragraph is not automatically the first chunk. If the adaptive target is 1000 characters and the first 3 paragraphs total 917 characters, those 3 paragraphs should become one chunk. If the next paragraph is 200 characters, the remaining 83 characters of target budget are not enough to justify adding it, so that next paragraph starts the next chunk.

The chunker should prefer complete semantic units over filling every character of the target budget.

## Chunk Sizing Rules

The chunker should aim for:

```text
min_chars <= chunk length <= max_chars
```

But it may exceed `max_chars` when a single sentence or paragraph is longer than `max_chars`. In that case it should split at the best lower-priority boundary inside that unit.

Recommended initial defaults:

```env
PIPER_CHUNKS_ENABLED=true
PIPER_CHUNK_THRESHOLD_CHARS=600
PIPER_CHUNK_TARGET_CHARS=350
PIPER_CHUNK_MIN_CHARS=120
PIPER_CHUNK_MAX_CHARS=700
PIPER_CHUNK_PREFETCH=1
PIPER_CHUNK_MAX_WORKERS=1
```

These defaults should be overridden by hardware detection and benchmark results when adaptive mode is enabled.

## Hardware Detection

At startup, the engine should collect a lightweight hardware profile.

Minimum profile:

```json
{
  "cpu_count": 8,
  "memory_total_mb": 16000,
  "memory_available_mb": 9000,
  "platform": "linux"
}
```

Implementation options:

- Use `os.cpu_count()` for CPU count.
- Use standard library only at first if possible.
- Optionally add `psutil` later for better memory detection.
- If memory cannot be detected, continue with conservative defaults.

The hardware profile should not be the only signal. Real Piper speed depends on the selected model, CPU type, current load, and container limits.

## Startup Benchmark

Hardware detection should be paired with a startup or first-use benchmark.

Benchmark idea:

1. Generate a short fixed phrase with the default model.
2. Measure synthesis wall time.
3. Measure generated audio duration.
4. Compute real-time factor.

Real-time factor:

```text
rtf = synthesis_seconds / audio_duration_seconds
```

Interpretation:

```text
rtf < 0.5  -> faster than realtime, good for larger chunks
rtf ~= 1.0 -> realtime, use moderate chunks
rtf > 1.0  -> slower than realtime, use smaller chunks and prefetch carefully
```

The benchmark result should be cached in memory by model name.

If benchmarking fails, use conservative defaults and keep the service available.

## Adaptive Timing Model

The goal is not only to split by characters. The engine should keep the next chunk ready before the current chunk finishes playing.

For each generated chunk, collect:

```text
text_chars
synthesis_seconds
audio_duration_seconds
delivery_overhead_seconds
```

Useful estimates:

```text
chars_per_audio_second = text_chars / audio_duration_seconds
chars_per_synthesis_second = text_chars / synthesis_seconds
```

The next chunk target should consider playback time:

```text
expected_generation_time(next_chunk) + delivery_overhead < current_audio_duration - safety_margin
```

Example:

If the current audio is 10 seconds long, the next chunk should be generated and delivered in less than roughly 8 seconds so there is buffer before playback ends.

If generation and delivery would take 25 seconds for a chunk that only plays for 10 seconds, the chunk is too large or the prefetch strategy is insufficient. The adaptive controller should reduce target chunk size and/or increase prefetch if hardware allows it.

## Prefetch Strategy

Initial implementation should use simple sequential generation:

```text
generate chunk 0
send chunk 0
generate chunk 1
send chunk 1
...
```

Then add prefetch:

```text
generate chunk 0
send chunk 0
start generating chunk 1 while client plays chunk 0
send chunk 1 as soon as ready
```

The first production-ready version should support:

```env
PIPER_CHUNK_PREFETCH=1
PIPER_CHUNK_MAX_WORKERS=1
```

Higher worker counts should be optional because parallel Piper processes can increase CPU contention and may make latency worse on small machines.

## Client Playback Model

The GUI is only a reference client.

Reference GUI behavior:

1. Request `/speak/chunks`.
2. Read NDJSON events as they arrive.
3. Decode each chunk's `audio_base64` into a Blob.
4. Queue chunks in memory.
5. Start playing as soon as chunk 0 arrives.
6. Play the next chunk immediately when the current chunk ends.
7. Keep status visible: generating, buffering, playing, done.

First version may use a normal `<audio>` element and accept tiny gaps.

Later versions may use Web Audio API for gapless scheduling.

## API Compatibility

The existing `/speak` endpoint remains unchanged.

The new endpoint should be additive.

The engine should be usable without the GUI.

The GUI should be able to call a remote engine through `PIPER_ENGINE_URL`.

## Error Handling

If chunk generation fails after some chunks were already sent, emit an error event:

```json
{"type":"error","message":"...","index":2}
```

The GUI should stop playback only if there are no queued chunks left. If there are queued chunks, it can finish playing them and then show the error.

For `/speak/chunks`, invalid input should return HTTP `400` before any stream starts.

## Configuration

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

Suggested behavior:

- If `PIPER_CHUNKS_ENABLED=false`, `/speak/chunks` returns `404` or `501`.
- If text length is below `PIPER_CHUNK_THRESHOLD_CHARS`, the GUI may keep using `/speak`.
- If adaptive mode is disabled, use static chunk sizes from env.
- If adaptive mode is enabled, use static values as initial bounds.

## Observability

Add metadata to chunk events so clients and tests can inspect behavior:

```json
{
  "type": "chunk",
  "index": 0,
  "chars": 320,
  "duration_seconds": 8.2,
  "synthesis_seconds": 1.4,
  "rtf": 0.17,
  "split_reason": "sentence"
}
```

The `/health` endpoint may later include chunking capability:

```json
{
  "chunks_enabled": true,
  "chunk_adaptive": true
}
```

## Risks

- WAV chunks may produce small audible gaps in browser playback.
- Too many small chunks can degrade prosody and add overhead.
- Too-large chunks reduce the perceived latency benefit.
- Parallel generation can overload small machines.
- Browser autoplay policies may block playback unless initiated by user action.
- Base64 audio in NDJSON increases payload size.

## Future Improvements

- Web Audio API playback queue for smoother transitions.
- Binary streaming or multipart audio chunks instead of base64.
- Server-Sent Events or WebSocket transport.
- Session-based chunk jobs with cancellation.
- Per-model benchmark cache persisted to disk.
- Smarter language-specific abbreviation detection.
- Optional text normalization before splitting.
