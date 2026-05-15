# Adaptive Chunked TTS Streaming — V1 Checklist

V1 scope only. Hardware detection, benchmark, adaptive controller, prefetch and binary transport are V2 and intentionally absent from this list.

## Phase 1 — Splitter (`piper_sandbox/chunks.py`)

- [x] Add `ChunkConfig` dataclass (`target_chars`, `min_chars`, `max_chars`).
- [x] Add `TextChunk` dataclass (`index`, `text`, `chars`, `split_reason`).
- [x] Implement `split_text(text: str, config: ChunkConfig) -> list[TextChunk]`.
- [x] Normalize `\r\n` and `\r` to `\n` before splitting.
- [x] Raise `ValueError` on empty/whitespace-only text.
- [x] Outer pass: pack paragraphs separated by `\n{2,}` into the current chunk.
- [x] Emit current chunk when adding the next paragraph would overflow target significantly.
- [x] Never include the next paragraph just to consume residual target budget.
- [x] Inner pass: when a single paragraph exceeds `max_chars`, split inside it using `find_split` (sentence → strong → comma → space → hard).
- [x] `find_split` searches in window `[max(min_chars, 0.65 * target_chars), max_chars]`.
- [x] Boundary char stays in the *previous* chunk; leading whitespace stripped from the *next*.
- [x] First/only chunk uses `split_reason="single"`.
- [x] Sequential `index` starting at 0.

### Splitter tests (`tests/test_chunks.py`)

- [x] `pip install -e '.[dev]'` works and `pytest` discovers the file.
- [x] Short text returns exactly one chunk with `split_reason="single"`.
- [x] Three short paragraphs summing to ≤ target become one chunk.
- [x] A fourth paragraph that would clearly overflow target starts a new chunk.
- [x] Sentence longer than max splits at a comma.
- [x] Paragraph with no punctuation splits at whitespace.
- [x] Single token longer than max is hard-split at exactly `max_chars`.
- [x] Order is preserved: concatenated chunks equal the input modulo whitespace normalization.
- [x] No empty chunks ever returned.
- [x] `chars` matches `len(text)` in every chunk.
- [x] `\r\n` line endings produce the same chunks as `\n`.
- [x] Empty input raises `ValueError`.

## Phase 2 — Endpoint (`piper_sandbox/api.py`)

- [x] Read env at startup: `PIPER_CHUNKS_ENABLED` (default false), `PIPER_CHUNK_TARGET_CHARS=350`, `PIPER_CHUNK_MIN_CHARS=120`, `PIPER_CHUNK_MAX_CHARS=700`.
- [x] Validate `0 < min <= target <= max`; fail at startup otherwise.
- [x] Add `wav_duration_seconds(data: bytes)` helper using `wave.open(io.BytesIO(...))`.
- [x] Route `POST /speak/chunks` in `do_POST`.
- [x] Return 501 with `text/plain` when feature flag is off.
- [x] Return 404 when service mode is `gui` (engine disabled).
- [x] Validate JSON, non-empty `text`, known `model` before any streaming → 400 on failure.
- [x] Call `split_text` before opening the stream so chunk count is known for `meta`.
- [x] Send response headers: `200`, `Content-Type: application/x-ndjson; charset=utf-8`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`, CORS, **no** `Content-Length`.
- [x] Emit `meta` event first with `model`, `chunks`, `target_chars`, `min_chars`, `max_chars`.
- [x] For each chunk: synthesize, measure `synthesis_seconds` and `duration_seconds`, base64-encode WAV, emit `chunk` event with `index`, `chars`, `split_reason`, `synthesis_seconds`, `duration_seconds`, `rtf`, `audio_base64`.
- [x] Omit chunk `text` by default; include only when `?include_text=1`.
- [x] Flush after every event.
- [x] On `PiperError` mid-stream: emit `{"type":"error","index":i,"message":...}` and return.
- [x] Emit `done` event last on success.
- [x] `/speak` handler untouched (still byte-compatible).

### Health update

- [x] Add `chunks_enabled` boolean to `/health` JSON response.

### Endpoint tests (`tests/test_speak_chunks_endpoint.py`)

- [x] Fixture: monkeypatch `PiperRequestHandler.engine` to a `FakeEngine` returning a canned ~1-second silent WAV.
- [x] Fixture: start `ThreadingHTTPServer` on an ephemeral port; teardown shuts it down.
- [x] When disabled: `POST /speak/chunks` → 501.
- [x] When enabled, valid short text → 200, `application/x-ndjson`, sequence: `meta` (chunks=1), one `chunk`, `done`.
- [x] When enabled, long text → multiple `chunk` events with sequential indexes.
- [x] `chunk.audio_base64` decodes to bytes starting with `RIFF`.
- [x] Empty text → 400 before any NDJSON.
- [x] Unknown model → 400 before any NDJSON.
- [x] Invalid JSON → 400.
- [x] `?include_text=1` adds `text` field; default request omits it.
- [x] `/health` exposes `chunks_enabled` matching the env value.

## Phase 3 — Reference GUI

- [ ] `INDEX_HTML` fetches `/health` on load and reads `chunks_enabled`.
- [ ] When chunks enabled: `say()` calls `/speak/chunks`, parses NDJSON from `response.body.getReader()`.
- [ ] When chunks disabled: `say()` calls `/speak` (current behavior).
- [ ] Decode `audio_base64` → `Uint8Array` → `Blob({type:'audio/wav'})`.
- [ ] Maintain FIFO playback queue; start playback on first chunk; on `audio.ended` play next.
- [ ] Status text reflects state: `Generando`, `Reproduciendo (i/N)`, `Listo`, `Error`.
- [ ] Handle in-band `error` event: finish queued chunks, then alert.
- [ ] Health fetch uses the same `__ENGINE_URL__` as `/speak/chunks` (works in `gui` mode pointing to remote engine).
- [ ] Manually click "Hablar" with a long paragraph and confirm playback starts before all chunks have arrived.

## Phase 4 — Config and docs

- [ ] Add new variables to `.env.example`: `PIPER_CHUNKS_ENABLED`, `PIPER_CHUNK_TARGET_CHARS`, `PIPER_CHUNK_MIN_CHARS`, `PIPER_CHUNK_MAX_CHARS`. Default `PIPER_CHUNKS_ENABLED=false`.
- [ ] Add the same env vars (with the same defaults) to `Dockerfile` `ENV` block.
- [ ] Add `pytest>=8` to `pyproject.toml` `[project.optional-dependencies].dev`.
- [ ] README: add `/speak/chunks` section under `## Endpoints`, document NDJSON event shape, document `PIPER_CHUNKS_ENABLED` and chunk env vars.
- [ ] README: document the known proxy-buffering caveat.
- [ ] CLAUDE.md: add one line under `## Architecture` noting the new endpoint and module, and add `chunks.py` to the module list.

## Phase 5 — Validation

- [ ] `python -m compileall piper_sandbox` passes.
- [ ] `pytest` passes.
- [ ] Manual: `/speak` returns identical bytes for a fixed input compared to the previous commit (record hash before changes).
- [ ] Manual: `/speak/chunks` with feature off → 501.
- [ ] Manual: `/speak/chunks` with feature on, short text → 1 chunk.
- [ ] Manual: `/speak/chunks` with feature on, long paragraph → ≥3 chunks, first chunk arrives noticeably before the last.
- [ ] Mode `engine`: `/speak/chunks` works, `/` returns 404.
- [ ] Mode `gui` against a remote engine: browser GUI loads, calls remote `/health` and `/speak/chunks`, plays audio. CORS allows it.
- [ ] Docker compose `up --build` reaches healthy `/health` with `chunks_enabled=true` when the env override is set.

## Acceptance Criteria

- [ ] `/speak` remains byte-compatible.
- [ ] `/speak/chunks` streams valid NDJSON when enabled, returns 501 when disabled.
- [ ] Long text plays earlier than today in the reference GUI.
- [ ] Splitter covered by unit tests; endpoint covered by integration tests against a fake engine.
- [ ] All three service modes still work.
- [ ] No regression in startup time when the flag is off (no new imports on the hot path beyond `chunks.py` config parsing).
