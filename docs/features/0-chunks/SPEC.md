# Adaptive Chunked TTS Streaming

## Summary

Add chunked audio delivery to the Piper engine so long text starts playing earlier. The existing `/speak` endpoint returns one complete WAV; this feature adds an additive `/speak/chunks` endpoint that streams audio in pieces while later chunks are still being generated.

The whole feature is gated by a single env flag (`PIPER_CHUNKS_ENABLED`). When disabled, behavior is identical to today and `/speak/chunks` returns HTTP `501`.

## Scope: V1 vs V2

**V1 (this spec).** Sequential streaming. Server splits text, generates chunks one by one, emits each as soon as it is ready. Latency win comes from "chunk 0 reaches the client before chunk N has been synthesized," not from parallelism. Chunk sizes are static, configured by env.

**V2 (future, out of scope here).** Hardware-aware adaptive chunk sizing, per-model benchmark, multi-worker prefetch, binary transport, session-based jobs with cancellation, Web Audio API gapless playback. Captured in `## Future Improvements`. Do not implement in V1.

This split lets us ship a useful feature quickly and prove the endpoint contract before adding adaptive complexity that is hard to test.

## Goals (V1)

- Start audio playback earlier for long text.
- Split text intelligently using paragraph, sentence, clause and whitespace boundaries.
- Keep chunks large enough to avoid audible gaps and excessive overhead, small enough that chunk 0 ships fast.
- Expose the feature through an engine endpoint that any client can consume (the bundled GUI is a reference client only).
- Keep `/speak` byte-compatible with today's behavior.
- Gate the entire feature behind one env flag.

## Non-Goals (V1)

- No replacement of `/speak`.
- No true continuous WAV streaming (one WAV per chunk is fine).
- No hardware detection, benchmarking, or adaptive sizing.
- No prefetching with parallel Piper workers.
- No AI/LLM-based chunking.
- No perfect gapless playback at chunk boundaries.
- No persistence of chunk jobs between requests.

## Existing Behavior (unchanged)

```text
POST /speak
{"text": "Hello", "model": "es_MX-claude-high"}
→ 200 audio/wav (full WAV)
```

## Proposed Endpoint

```text
POST /speak/chunks
```

Input:

```json
{"text": "Long text...", "model": "es_MX-claude-high"}
```

Response when `PIPER_CHUNKS_ENABLED=true`:

```text
HTTP/1.1 200 OK
Content-Type: application/x-ndjson; charset=utf-8
Transfer-Encoding: chunked
Cache-Control: no-cache
X-Accel-Buffering: no
```

Response when `PIPER_CHUNKS_ENABLED=false`:

```text
HTTP/1.1 501 Not Implemented
Content-Type: text/plain
Chunked TTS is disabled
```

## NDJSON Event Stream

Each line is one JSON object, terminated by `\n`, flushed immediately after write.

Sequence:

```text
meta → chunk → chunk → ... → done
```

Or, on mid-stream synthesis failure:

```text
meta → chunk → ... → error
```

### `meta` (first line)

```json
{
  "type": "meta",
  "model": "es_MX-claude-high",
  "chunks": 4,
  "target_chars": 350,
  "min_chars": 120,
  "max_chars": 700
}
```

`chunks` is the planned chunk count (known up front because V1 splits the whole text before synthesizing).

### `chunk`

```json
{
  "type": "chunk",
  "index": 0,
  "chars": 318,
  "split_reason": "sentence",
  "synthesis_seconds": 1.42,
  "duration_seconds": 8.21,
  "rtf": 0.17,
  "audio_base64": "UklGRgA..."
}
```

The original chunk `text` is **not** included by default (the client already sent it, including it doubles payload). Clients that need it for debugging can pass `?include_text=1`.

### `done`

```json
{"type": "done"}
```

### `error`

```json
{"type": "error", "index": 2, "message": "Piper exited with code 1"}
```

Emitted after `meta` if synthesis fails partway through. The client should finish playing any already-queued chunks and then surface the error.

Pre-stream errors (invalid JSON, empty text, unknown model, feature disabled) return non-200 status codes before any NDJSON is written.

## Chunking Strategy

The splitter is the only non-trivial piece of V1. It is pure (text in, list of chunks out), independent from HTTP and Piper, and unit-testable without any external dependency.

### Inputs

```python
ChunkConfig(target_chars: int, min_chars: int, max_chars: int)
```

`target_chars` is the *preferred* chunk size, not a hard limit. `min_chars` and `max_chars` define the operating range.

### Boundary priority

Highest to lowest:

```text
paragraph (blank line, \n\n or \r\n\r\n)
sentence terminators: . ? !
strong separators: ; :
comma: ,
whitespace
hard split
```

Punctuation stays attached to the *previous* chunk so Piper renders prosody correctly at the end of each chunk.

### Packing rule

Pack semantic units (paragraphs first, then sentences inside an oversized paragraph) into the current chunk while the resulting length stays close to `target_chars`. If adding the next unit would overflow significantly, emit the current chunk and start a new one — do **not** stuff small units into every leftover byte of budget.

Concrete example: if `target_chars=1000`, three paragraphs together total 917, and the fourth paragraph is 200 characters, emit the first three as one chunk (close enough to target) and start the next chunk with paragraph four. Do not include paragraph four just because 83 bytes remain.

### When a single unit exceeds `max_chars`

Recurse into the unit using the next boundary level (sentence → strong separator → comma → whitespace → hard). Hard split is allowed only as last resort (text with no whitespace at all).

### Edge cases

- Empty/whitespace-only text → reject with HTTP `400` before streaming starts.
- Single very short text → one chunk, `split_reason: "single"`.
- Text shorter than `min_chars` → one chunk, no split.
- Trailing whitespace between chunks → strip on the *new* chunk side, leave punctuation/spacing on the previous side.
- Line endings: normalize `\r\n` → `\n` before splitting.

## Configuration

V1 env variables (added to `.env.example` only when read by code):

```env
PIPER_CHUNKS_ENABLED=false
PIPER_CHUNK_TARGET_CHARS=350
PIPER_CHUNK_MIN_CHARS=120
PIPER_CHUNK_MAX_CHARS=700
```

Default `PIPER_CHUNKS_ENABLED=false` keeps the feature off by default, matching the user requirement that audio behaves "as today" unless explicitly enabled.

Variables explicitly **deferred** to V2 (documented here, not implemented):

```env
PIPER_CHUNK_ADAPTIVE
PIPER_CHUNK_PREFETCH
PIPER_CHUNK_MAX_WORKERS
PIPER_CHUNK_SAFETY_MARGIN_SECONDS
PIPER_CHUNK_BENCHMARK_ON_START
```

The earlier `PIPER_CHUNK_THRESHOLD_CHARS` is **dropped**: with a single endpoint the splitter naturally emits one chunk for short text, so no threshold is needed.

## Client Contract

The bundled GUI is the reference client; any client should follow the same pattern.

1. On load, `GET /health`. Read `chunks_enabled` (see below).
2. If `chunks_enabled === true`: always call `POST /speak/chunks` and parse NDJSON. For short text the server returns one chunk, which is fine.
3. If `chunks_enabled === false` (or `/speak/chunks` returns 501): fall back to `POST /speak`.
4. While streaming: decode each chunk's `audio_base64` into a Blob, append to a playback queue, start playback on chunk 0 arrival, continue on `ended` for each queued chunk.
5. On `error` event: finish the queue, then show the message.

V1 accepts small audible gaps between WAV chunks in browser `<audio>` playback. V2 will move to Web Audio API for gapless scheduling.

## `/health` Additions

```json
{
  "status": "ok",
  "mode": "both",
  "engine": true,
  "gui": true,
  "engine_url": "",
  "chunks_enabled": false
}
```

Only `chunks_enabled` is new. No leaking of internal config (target/min/max) on `/health` — clients learn that from the `meta` event.

## Service Mode Compatibility

All three modes (`both`, `engine`, `gui`) must keep working.

- `engine` and `both`: expose `/speak/chunks` when enabled.
- `gui`: forwards nothing; the browser calls the remote engine directly using `PIPER_ENGINE_URL`. CORS must allow the GUI origin.

## Error Handling Summary

| Condition | HTTP | Body |
| --- | --- | --- |
| Feature disabled | 501 | `text/plain` "Chunked TTS is disabled" |
| Invalid JSON | 400 | text |
| Empty text | 400 | text |
| Unknown model | 400 | text |
| Piper fails *before* any chunk emitted | 500 | text |
| Piper fails *after* meta or chunk emitted | 200 (already sent) | in-band `{"type":"error"}` event |

## Risks (V1)

- Small audible gaps between WAV chunks in browser playback. Acceptable for V1; documented as a known limitation.
- Base64 inflates payload by ~33%. Acceptable for V1 because each chunk is ≤ ~1 MB raw WAV at typical Piper settings. V2 will switch to a binary transport.
- Reverse proxies that buffer responses (default nginx) can defeat progressive delivery. We set `X-Accel-Buffering: no`; deployment docs must mention disabling proxy buffering for this endpoint.
- Browser autoplay policies may block playback unless triggered by user interaction. Reference GUI initiates from a click, which satisfies all major browsers.

## Future Improvements (V2+)

Captured for posterity, not in V1 scope:

- Hardware profile (`hardware.py`: cpu_count, /proc/meminfo) feeding initial `target_chars`.
- Startup or per-model benchmark (`benchmark.py`) measuring real-time factor (RTF) and adjusting target accordingly.
- Adaptive chunk controller (`chunk_controller.py`) that observes per-chunk timing and adjusts future targets to satisfy `expected_generation_time + delivery < current_audio_duration − safety_margin`.
- Multi-worker prefetch (`PIPER_CHUNK_MAX_WORKERS > 1`) generating chunk N+1 while chunk N streams.
- Binary transport: NDJSON metadata + separate `GET /speak/chunks/{job}/{i}.wav` for the audio (removes base64 overhead, enables HTTP caching, but introduces job lifecycle state).
- Web Audio API queue in the reference GUI for gapless playback.
- Server-Sent Events / WebSocket variant for environments where chunked NDJSON over HTTP is buffered by proxies.
- Per-model benchmark cache persisted to disk.
- Language-specific abbreviation handling (e.g. "Sr." not ending a sentence) in the splitter.
