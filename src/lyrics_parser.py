"""
Parser for LRC (Lyric Rich Content) format.

Standard time-tag syntax:  [mm:ss.cc]  or  [mm:ss.xxx]
Multiple time tags on one line (e.g. two choruses) are supported.

Also extracts common metadata tags such as:
  [ti:Title]  [ar:Artist]  [al:Album]  [length:mm:ss]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Matches a single LRC time tag at the start of a string, e.g. [01:23.45]
_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{2})\.(\d{2,3})\]")
# Matches metadata tags like [ti:Song Title]
_META_RE = re.compile(r"^\[([a-zA-Z]+):([^\]]*)\]$")


@dataclass(frozen=True)
class LrcLine:
    time_ms: int
    text: str


def parse_lrc(content: str) -> tuple[list[LrcLine], dict[str, str]]:
    """
    Parse an LRC string into sorted timed lines and a metadata dict.

    Returns:
        ``(lines, metadata)`` where *lines* is sorted by ``time_ms`` and
        *metadata* is a lower-cased key → value mapping (e.g. ``{"ti": "…"}``).
    """
    lines: list[LrcLine] = []
    metadata: dict[str, str] = {}

    for raw in content.splitlines():
        raw = raw.strip()
        if not raw:
            continue

        # ── Metadata tag ────────────────────────────────────────────────────
        meta_m = _META_RE.match(raw)
        if meta_m and not _TIME_RE.search(raw):
            metadata[meta_m.group(1).lower()] = meta_m.group(2).strip()
            continue

        # ── Time-tagged lyric line ───────────────────────────────────────────
        # A single raw line can carry multiple time tags before the text,
        # e.g.  [00:12.34][01:45.67]Chorus text here
        remaining = raw
        time_tags: list[int] = []

        while True:
            m = _TIME_RE.match(remaining)
            if not m:
                break
            mins = int(m.group(1))
            secs = int(m.group(2))
            frac_str = m.group(3)
            # Normalise 2-digit centiseconds (×10) or 3-digit milliseconds.
            frac_ms = int(frac_str) if len(frac_str) == 3 else int(frac_str) * 10
            time_ms = (mins * 60 + secs) * 1000 + frac_ms
            time_tags.append(time_ms)
            remaining = remaining[m.end():]  # advance past the matched tag

        if time_tags:
            text = remaining.strip()
            for t in time_tags:
                lines.append(LrcLine(time_ms=t, text=text))

    lines.sort(key=lambda ln: ln.time_ms)
    return lines, metadata


def find_current_line(lines: list[LrcLine], elapsed_ms: int) -> int:
    """
    Return the index of the lyric line that should be active at *elapsed_ms*.

    Uses binary search (O(log n)).

    Returns:
        Index ≥ 0, or ``-1`` if *lines* is empty or *elapsed_ms* is before
        the first line's timestamp.
    """
    if not lines:
        return -1

    lo, hi = 0, len(lines) - 1
    result = -1

    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid].time_ms <= elapsed_ms:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1

    return result
