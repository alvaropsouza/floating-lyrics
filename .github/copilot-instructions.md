# Floating Lyrics - GitHub Copilot Instructions

Prefer changes that keep the headless backend (`main_server_headless.py` + `src/worker_headless.py`) as the canonical implementation. The Qt app (`main.py`) should stay a thin UI wrapper using the Qt adapter worker (`src/worker.py`) and must not reintroduce a second recognition pipeline.

Key rules:
- Never block the UI thread; keep capture/network work in worker threads.
- Use `time.perf_counter()` for timing/sync deltas.
- Avoid repeated API calls for the same song; use caches where possible.
- Keep Windows WASAPI loopback support intact.
- Keep user-facing strings in Portuguese.
- Keep training dataset layout as `llm-music-api/training_audio/{artist}/{album}/{song_file}`.
- Do not add/commit real API keys or secrets.
- Always start the full project stack from the repository root using `start_all` (`start_all.bat` on Windows CMD/PowerShell or `./start_all.sh` on Git Bash), unless the user explicitly asks for a different startup flow.

