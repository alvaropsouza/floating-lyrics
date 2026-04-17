"""
Development runner with automatic restart on file changes.

Usage:
    python dev_auto_restart.py

It starts `main.py`, watches project files, and restarts the app whenever
supported files change.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENTRYPOINT = ROOT / "main.py"

WATCH_EXTENSIONS = {".py", ".ini", ".txt", ".md"}
IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}

POLL_INTERVAL_S = 0.8
RESTART_DEBOUNCE_S = 0.4


def _iter_watched_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        base = Path(dirpath)
        for filename in filenames:
            file_path = base / filename
            if file_path.suffix.lower() in WATCH_EXTENSIONS:
                yield file_path


def _snapshot_mtimes(root: Path) -> dict[Path, float]:
    mtimes: dict[Path, float] = {}
    for file_path in _iter_watched_files(root):
        try:
            mtimes[file_path] = file_path.stat().st_mtime
        except OSError:
            continue
    return mtimes


def _diff_changes(
    previous: dict[Path, float],
    current: dict[Path, float],
) -> list[Path]:
    changed: list[Path] = []

    for path, mtime in current.items():
        if path not in previous or previous[path] != mtime:
            changed.append(path)

    for path in previous:
        if path not in current:
            changed.append(path)

    changed.sort()
    return changed


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return

    if os.name == "nt":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _start_process() -> subprocess.Popen:
    print("[dev] Starting main.py")
    return subprocess.Popen([sys.executable, str(ENTRYPOINT)], cwd=str(ROOT))


def main() -> int:
    if not ENTRYPOINT.exists():
        print(f"[dev] Entrypoint not found: {ENTRYPOINT}")
        return 1

    proc = _start_process()
    known = _snapshot_mtimes(ROOT)

    try:
        while True:
            time.sleep(POLL_INTERVAL_S)

            current = _snapshot_mtimes(ROOT)
            changed = _diff_changes(known, current)
            known = current

            if changed:
                first = changed[0].relative_to(ROOT)
                more = len(changed) - 1
                suffix = f" (+{more} files)" if more > 0 else ""
                print(f"[dev] Change detected: {first}{suffix}. Restarting...")
                _terminate_process(proc)
                time.sleep(RESTART_DEBOUNCE_S)
                proc = _start_process()
                continue

            # If child died, start it again (useful during crash loops).
            if proc.poll() is not None:
                print(f"[dev] main.py exited with code {proc.returncode}. Restarting...")
                time.sleep(RESTART_DEBOUNCE_S)
                proc = _start_process()

    except KeyboardInterrupt:
        print("\n[dev] Stopping...")
        _terminate_process(proc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
