"""Sidecar .txt writer — caption sidecar for folder-watching posters.

Matches the convention "<stem>.mp4 + <stem>.txt" used by Repurpose.io and most
other folder-watching posting services. Single-line caption + space-separated
hashtags (the watcher word-wraps).
"""
from __future__ import annotations

import re
from pathlib import Path

_STRIP_CHARS = re.compile(r"[\\/:\"\?<>\|\*]")
_APOS = re.compile(r"['']")
_DOTS = re.compile(r"\.")
_WS = re.compile(r"\s+")
_INLINE_HASHTAGS = re.compile(r"#\w+")


def sanitize_title(title: str) -> str:
    cleaned = _STRIP_CHARS.sub("", title)
    cleaned = _APOS.sub("", cleaned)
    cleaned = _DOTS.sub(" ", cleaned)
    cleaned = _WS.sub(" ", cleaned).strip()
    return cleaned[:80]


def write_sidecar(
    out_dir: str | Path,
    title: str,
    caption: str,
    hashtags: list[str],
) -> Path:
    """Write a sidecar .txt next to the rendered .mp4.

    Format: `{caption} {hashtag1 hashtag2 ...}` — single line (the watcher
    word-wraps). The caption is expected to already contain a CTA from the
    channel's CTA pool. Inline hashtags inside the caption body are scrubbed
    so the hashtag bag stays consistent per brand.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = sanitize_title(title)
    path = out_dir / f"{safe}.txt"
    cleaned_caption = _INLINE_HASHTAGS.sub("", caption)
    cleaned_caption = _WS.sub(" ", cleaned_caption).strip()
    body = f"{cleaned_caption} {' '.join(hashtags)}\n"
    path.write_text(body, encoding="utf-8")
    return path
