"""
Configuration manager for Floating Lyrics.

Reads/writes config.ini located next to this file.
Provides typed accessors with safe fallbacks.
"""

import configparser
from pathlib import Path
from typing import Union

CONFIG_FILE = Path(__file__).parent / "config.ini"

# ── Default values ──────────────────────────────────────────────────────────
_DEFAULTS: dict[str, dict[str, str]] = {
    "API": {
        "audd_api_key": "",
        "llm_api_key": "",
        "musixmatch_api_key": "",
    },
    "LLMApi": {
        "base_url": "http://127.0.0.1:3000",
        "top_k": "3",
    },
    "ACRCloud": {
        "access_key": "",
        "access_secret": "",
        "host": "identify-eu-west-1.acrcloud.com",
    },
    "Audio": {
        "capture_device_index": "-1",
        "capture_device_name": "",
        "target_sample_rate": "44100",
        "max_normalize_gain": "2.0",
    },
    "Recognition": {
        "recognition_provider": "audd",
        "provider_fallback_order": "acrcloud,audd",
        "provider_attempts": "2",
        "use_llm_for_recognition": "false",
        "save_audio_for_training": "true",
        "auto_train_llm_on_new_audio": "true",
        "training_audio_same_song_cooldown_s": "90",
        "tracking_capture_duration": "5",
        "llm_train_debounce_s": "15",
        "disable_api_recognition_while_tracking": "true",
        "local_change_similarity_threshold": "0.72",
        "local_change_frames_threshold": "30",
        "local_change_min_energy": "0.08",
        "local_change_ema_alpha": "0.05",
        "local_change_cooldown_s": "5",
        "new_song_max_attempts": "12",
        "new_song_attempt_cooldown_s": "120",
        "capture_duration": "10",
        "recognition_interval": "2",
        "silence_threshold": "100",
        "tracking_miss_reset": "3",
        "save_audio_for_debug": "false",
        "confidence_threshold": "0.5",
        "confidence_trigger": "2",
        "confidence_interval": "2.0",
    },
    "Display": {
        "opacity": "0.85",
        "font_size": "16",
        "font_color": "#FFFFFF",
        "bg_color": "#1A1A2E",
        "always_on_top": "true",
        "window_x": "100",
        "window_y": "100",
        "window_width": "650",
        "window_height": "160",
        "lines_context": "2",
        "lrc_offset_ms": "0",
        "line_switch_hysteresis_ms": "70",
    },
    "Preferences": {
        "start_minimized": "false",
    },
}


class Config:
    def __init__(self) -> None:
        self._cp = configparser.ConfigParser()
        self._load_defaults()
        self._load_file()

    # ── Private helpers ─────────────────────────────────────────────────────

    def _load_defaults(self) -> None:
        for section, values in _DEFAULTS.items():
            self._cp[section] = values

    def _load_file(self) -> None:
        if CONFIG_FILE.exists():
            self._cp.read(CONFIG_FILE, encoding="utf-8")

    # ── Public API ──────────────────────────────────────────────────────────

    def save(self) -> None:
        """Persist current configuration to disk."""
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            self._cp.write(fh)

    def get(self, section: str, key: str, fallback: str = "") -> str:
        return self._cp.get(section, key, fallback=fallback)

    def set(self, section: str, key: str, value: Union[str, int, float, bool]) -> None:
        if not self._cp.has_section(section):
            self._cp.add_section(section)
        self._cp.set(section, key, str(value))

    def getint(self, section: str, key: str, fallback: int = 0) -> int:
        return self._cp.getint(section, key, fallback=fallback)

    def getfloat(self, section: str, key: str, fallback: float = 0.0) -> float:
        return self._cp.getfloat(section, key, fallback=fallback)

    def getboolean(self, section: str, key: str, fallback: bool = False) -> bool:
        return self._cp.getboolean(section, key, fallback=fallback)
