# Adaptive Chunked TTS Streaming Checklist

## Phase 0: Review And Baseline

- [ ] Confirm current `/speak` behavior remains unchanged.
- [ ] Confirm current GUI can still generate a full WAV through `/speak`.
- [ ] Confirm `PIPER_SERVICE_MODE=both` works locally.
- [ ] Confirm Docker can run the app in `both` mode.
- [ ] Record a baseline for a short text generation time.
- [ ] Record a baseline for a long text generation time.
- [ ] Add baseline tests under `tests/test_engine_baseline.py`.
- [ ] Test `/health` returns engine and GUI enabled in `both` mode.
- [ ] Test `/models` returns configured models.
- [ ] Test `/speak` returns `audio/wav` for short text.
- [ ] Test `/speak` rejects empty text.

## Phase 1: Text Splitter

- [ ] Add `piper_sandbox/chunks.py`.
- [ ] Add `ChunkConfig` dataclass.
- [ ] Add `TextChunk` dataclass.
- [ ] Implement `split_text(text, config) -> list[TextChunk]`.
- [ ] Track `split_reason` for each chunk.
- [ ] Split paragraphs without blindly making every paragraph a chunk.
- [ ] Combine multiple small paragraphs when they fit the target.
- [ ] Leave the next paragraph for the next chunk when only a tiny target budget remains.
- [ ] Prefer sentence boundaries near the target.
- [ ] Fall back to strong separators.
- [ ] Fall back to comma boundaries.
- [ ] Fall back to whitespace.
- [ ] Hard split only when no better boundary exists.
- [ ] Preserve punctuation at the end of chunks.
- [ ] Avoid empty chunks.
- [ ] Preserve original reading order.
- [ ] Add splitter tests under `tests/test_chunks_splitter.py`.
- [ ] Test short text returns one chunk.
- [ ] Test three short paragraphs totaling less than target become one chunk.
- [ ] Test a fourth paragraph that would exceed target starts the next chunk.
- [ ] Test long sentence over max chars is split by comma if possible.
- [ ] Test text with no punctuation is split by whitespace.
- [ ] Test text with no whitespace is hard split.
- [ ] Test chunk lengths respect min/target/max where practical.
- [ ] Test `split_reason` for representative cases.

## Phase 2: Hardware Profile

- [ ] Add `piper_sandbox/hardware.py`.
- [ ] Implement `get_hardware_profile()`.
- [ ] Detect CPU count with `os.cpu_count()`.
- [ ] Detect memory on Linux from `/proc/meminfo`.
- [ ] Return conservative fallback if memory cannot be detected.
- [ ] Avoid requiring `psutil` in the first version.
- [ ] Add hardware tests under `tests/test_hardware_profile.py`.
- [ ] Test CPU count is an integer or conservative fallback.
- [ ] Test `/proc/meminfo` parser handles valid input.
- [ ] Test memory parser handles missing fields.
- [ ] Test hardware profile creation does not raise on unsupported platforms.

## Phase 3: Piper Benchmark

- [ ] Add `piper_sandbox/benchmark.py`.
- [ ] Generate a short benchmark phrase with selected/default model.
- [ ] Measure synthesis wall time.
- [ ] Measure WAV duration.
- [ ] Compute real-time factor.
- [ ] Cache benchmark result by model name in memory.
- [ ] Make benchmark disableable by env.
- [ ] Ensure benchmark failure does not stop service startup.
- [ ] Use conservative defaults when benchmark is unavailable.
- [ ] Add benchmark tests under `tests/test_benchmark.py`.
- [ ] Test RTF calculation.
- [ ] Test failed synthesis returns fallback result.
- [ ] Test cached benchmark prevents duplicate synthesis.
- [ ] Test chunk target selection changes based on RTF thresholds.

## Phase 4: Adaptive Chunk Controller

- [ ] Add `piper_sandbox/chunk_controller.py`.
- [ ] Load chunk config from env defaults.
- [ ] Combine env defaults, hardware profile, benchmark result, and model name.
- [ ] Produce `ChunkConfig`.
- [ ] Reduce target chars for slow benchmark.
- [ ] Increase target chars for fast benchmark within bounds.
- [ ] Keep worker count at 1 for low CPU count.
- [ ] Respect configured max workers.
- [ ] Apply safety margin in readiness estimates.
- [ ] Keep controller deterministic for the same inputs.
- [ ] Add controller tests under `tests/test_chunk_controller.py`.
- [ ] Test fast RTF creates larger target than slow RTF.
- [ ] Test target never exceeds configured max.
- [ ] Test target never goes below configured min.
- [ ] Test worker count respects configured maximum.
- [ ] Test missing benchmark returns default config.

## Phase 5: Chunked Engine Endpoint

- [ ] Add `POST /speak/chunks`.
- [ ] Use `application/x-ndjson` response type.
- [ ] Parse JSON input.
- [ ] Validate text before streaming starts.
- [ ] Validate model before streaming starts.
- [ ] Split text into chunks.
- [ ] Emit `meta` event first.
- [ ] Generate audio for each chunk.
- [ ] Emit `chunk` events with base64 WAV.
- [ ] Include index, text, chars, duration, synthesis time, RTF, and split reason in chunk events.
- [ ] Emit `done` event after all chunks.
- [ ] Emit `error` event if Piper fails after streaming begins.
- [ ] Flush each NDJSON event as it is written.
- [ ] Keep existing `/speak` endpoint unchanged.
- [ ] Add endpoint tests under `tests/test_speak_chunks_endpoint.py`.
- [ ] Test endpoint returns NDJSON content type.
- [ ] Test short text produces one chunk.
- [ ] Test long text produces multiple chunks.
- [ ] Test meta event appears first.
- [ ] Test done event appears last.
- [ ] Test chunk indexes are sequential.
- [ ] Test audio payload decodes from base64.
- [ ] Test existing `/speak` tests still pass.

## Phase 6: Reference GUI Chunk Playback

- [ ] Keep current full-WAV path for short text.
- [ ] Switch to `/speak/chunks` when text exceeds threshold.
- [ ] Parse NDJSON progressively in browser.
- [ ] Decode base64 audio into blobs.
- [ ] Queue chunk audio blobs.
- [ ] Start playback as soon as chunk 0 arrives.
- [ ] Play queued chunks sequentially.
- [ ] Display generating/buffering/playing/done status.
- [ ] Handle stream error events.
- [ ] Work against same-origin engine in `both` mode.
- [ ] Work against remote engine via `PIPER_ENGINE_URL`.
- [ ] Add static GUI tests under `tests/test_gui_static_html.py`.
- [ ] Test HTML contains chunk endpoint code.
- [ ] Test HTML injects configured engine URL.
- [ ] Test threshold config is present or fetchable.

## Phase 7: Prefetch And Timing Adaptation

- [ ] Track synthesis time per chunk.
- [ ] Track audio duration per chunk.
- [ ] Compute RTF per chunk.
- [ ] Estimate whether next chunk can be ready before current playback ends.
- [ ] Reduce future target size when generation cannot keep up.
- [ ] Increase future target size when generation is comfortably faster than playback.
- [ ] Keep target inside min/max bounds.
- [ ] Avoid oscillation with smoothing or conservative adjustment.
- [ ] Keep first implementation single-worker unless explicitly configured.
- [ ] Add adaptive timing tests under `tests/test_adaptive_timing.py`.
- [ ] Test slow observed generation reduces next target.
- [ ] Test fast observed generation increases next target.
- [ ] Test adjustment never violates min/max.
- [ ] Test safety margin is applied.
- [ ] Test controller does not overreact to one outlier.

## Phase 8: Docker And Deployment Validation

- [ ] Validate `both` mode in Docker.
- [ ] Validate `engine` mode exposes `/speak/chunks`.
- [ ] Validate `gui` mode calls remote `/speak/chunks` through `PIPER_ENGINE_URL`.
- [ ] Validate CORS for remote GUI to engine calls.
- [ ] Validate health endpoint exposes chunking status.
- [ ] Validate Docker volume stores downloaded models.
- [ ] Document manual Docker smoke commands.

## Phase 9: Documentation

- [ ] Add `/speak/chunks` to README endpoints.
- [ ] Document chunk env variables.
- [ ] Document `both` mode for development.
- [ ] Document engine-only deployment.
- [ ] Document GUI-only deployment.
- [ ] Document known limitations around WAV chunk gaps.
- [ ] Document recommended future Web Audio API path.

## Acceptance Criteria

- [ ] `/speak` remains backwards compatible.
- [ ] `/speak/chunks` streams valid NDJSON events.
- [ ] Long text is split into natural chunks.
- [ ] First audio chunk arrives before full text synthesis completes.
- [ ] Chunk metadata exposes timing information.
- [ ] Reference GUI can play chunked audio in order.
- [ ] The system can run in `both`, `engine`, and `gui` modes.
- [ ] The feature has unit tests for splitter, hardware profile, benchmark math, controller, and endpoint contracts.
