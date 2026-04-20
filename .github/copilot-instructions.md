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

# Best Practices for Code Changes

- Avoid creating too many .md files for documentation; prefer adding to existing docs when possible.
- focus docs files in the `docs/` folder, and keep them organized by topic (e.g., `docs/STT_README.md`, `docs/ARCHITECTURE_VISUALIZATION.md`, etc.).

# Logging Guidelines

- Every log message must be self-describing: always include the **new value / current state** — never just say that something changed. Bad: `"status changed"`. Good: `"status mudou para 'Aguardando música...'"`. Bad: `"song reset"`. Good: `"Música resetada por silêncio de 1200ms seguido de retorno de áudio"`. Bad: `"No audio captured"`. Good: `"Nenhum áudio capturado após 10s — dispositivo retornou vazio"`.
- Log level discipline: use `_LOG.debug` for high-frequency internal state (spectrum frames, timecode deltas); use `_LOG.info` for lifecycle transitions (song found/lost, STT match, lyrics loaded); use `_LOG.warning` for recoverable unexpected conditions; use `_LOG.error` for failures requiring attention.
- Always include identifiers in log context (song title, artist, line index, file name, etc.) so logs are actionable without needing to cross-reference other messages.

