# Floating Lyrics (Agent Guidelines)

## Architecture (Canonical)
- **Backend canonical**: `main_server_headless.py` + `src/worker_headless.py` (threading + asyncio WebSocket).
- **Qt app**: `main.py` uses `src/worker.py`, which is a **Qt adapter** over `src/worker_headless.py` (no duplicate pipeline).
- **Protocol**: WebSocket events are emitted by `src/websocket_server.py` and consumed by Flutter in `flutter_ui/lib/services/websocket_service.dart`.

## Engineering Rules
- Do not block the UI thread. Network/audio work must remain in the worker/thread layer.
- Use monotonic clocks (`time.perf_counter`) for sync deltas; avoid `time.time` for timing math.
- Avoid repeated network calls for the same song; prefer in-memory/disk caches already present.
- Preserve Windows 10/11 WASAPI loopback compatibility (`pyaudiowpatch`).

## Config
- Keep configuration keys stable (`config.ini` + `config.py` fallbacks).
- When adding config, provide safe defaults and persist via `config.py`.
- Never commit real API keys or tokens (code, docs, examples).

## UX / Language
- Status messages and errors should be short, actionable, and in Portuguese.

## Repo Hygiene
- Prefer the headless backend path for new features.
- Do not vendor large artifacts into git (e.g., `node_modules`, model weights, training audio).

