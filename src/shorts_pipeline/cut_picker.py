"""Forehead cut picker — ffprobe + window enumeration + history filter."""
from __future__ import annotations

import json
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Window:
    source_path: str
    start_s: float
    end_s: float


def ffprobe_duration(path: str | Path) -> float:
    """Return duration in seconds via ffprobe."""
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        text=True,
    )
    data = json.loads(out)
    return float(data["format"]["duration"])


def enumerate_windows(duration_s: float, clip_duration_s: int) -> list[Window]:
    """Enumerate non-overlapping clip_duration_s windows in middle 60% of a take.

    Trims the first 20% (camera-roll / framing-adjust) and last 20%
    (wind-down) so a single long take produces only "content" windows.
    """
    middle_start = duration_s * 0.2
    middle_end = duration_s * 0.8
    if middle_end - middle_start < clip_duration_s:
        return []

    windows: list[Window] = []
    start = middle_start
    while start + clip_duration_s <= middle_end:
        windows.append(Window("dummy", round(start, 2), round(start + clip_duration_s, 2)))
        start += clip_duration_s
    return windows


def list_takes(forehead_dir: Path) -> list[Path]:
    return sorted(p for p in Path(forehead_dir).glob("*.mp4") if p.is_file())


def pick_cuts(
    forehead_dir: str | Path,
    count: int,
    clip_duration_s: int,
    already_used: set[tuple[str, float, float]],
) -> list[Window]:
    """Pick up to `count` unused windows from takes in `forehead_dir`.

    Returns fewer than `count` if the pool is partially exhausted (graceful
    degradation — channels still produce what they can). Raises only when
    zero cuts are available so the orchestrator can skip the channel.
    """
    takes = list_takes(Path(forehead_dir))
    if not takes:
        raise RuntimeError(f"No .mp4 takes found in {forehead_dir}")

    candidates: list[Window] = []
    for take in takes:
        dur = ffprobe_duration(take)
        for w in enumerate_windows(dur, clip_duration_s):
            cand = Window(str(take), w.start_s, w.end_s)
            if (cand.source_path, cand.start_s, cand.end_s) not in already_used:
                candidates.append(cand)

    if not candidates:
        raise RuntimeError(
            f"Forehead pool fully exhausted: need {count} cuts, 0 available "
            f"(deliver more long takes to {forehead_dir})"
        )

    candidates.sort(key=lambda w: (w.source_path, w.start_s))
    random.shuffle(candidates)
    return candidates[: min(count, len(candidates))]
