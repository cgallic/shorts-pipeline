"""Cut a window from a long take, burn the text overlay, mix music.

Three ffmpeg invocations:
  1. cut_window     — extract [start_s, end_s] from a long-take source
  2. drawtext_overlay — burn multi-line overlay text onto the clip
  3. mix_music      — loop-mix a music bed under the overlayed clip

The overlay uses ffmpeg's drawtext filter directly so the pipeline has no
external Python rendering dependency. Font path is configurable via the
OVERLAY_FONT_PATH env var; default falls back to DejaVu Sans on Linux.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def _default_font() -> str:
    if env := os.environ.get("OVERLAY_FONT_PATH"):
        return env
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    raise RuntimeError(
        "No overlay font found. Set OVERLAY_FONT_PATH in .env to a .ttf path "
        "(e.g. /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf)."
    )


def _escape_drawtext(s: str) -> str:
    """Escape ffmpeg drawtext-special characters.

    drawtext uses : to separate args, \\ to escape, % for placeholders, and '
    for quoting. Stripping/escaping these keeps multi-line lowercase tactical
    overlays safe.
    """
    return (
        s.replace("\\", "\\\\")
         .replace(":", r"\:")
         .replace("'", r"\'")
         .replace("%", r"\%")
    )


def cut_window(source_path: str | Path, start_s: float, end_s: float, out_path: str | Path) -> Path:
    duration = end_s - start_s
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-i", str(source_path),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg cut failed (exit {proc.returncode}). "
            f"stderr tail: {(proc.stderr or '<empty>')[-800:]}"
        )
    return out


def drawtext_overlay(
    in_path: str | Path,
    out_path: str | Path,
    overlay_text: str,
    fontsize: int = 52,
    fontcolor: str = "white",
    boxcolor: str = "black@0.55",
    border_w: int = 24,
    line_spacing: int = 10,
) -> Path:
    """Burn multi-line overlay text vertically centered onto the video.

    `overlay_text` is split on \\n; each line becomes a separate drawtext
    filter so the block centers cleanly regardless of line count. A subtle
    semi-transparent background box improves legibility against busy footage.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    font = _default_font()
    lines = [line for line in overlay_text.split("\n") if line.strip()]
    if not lines:
        # Nothing to overlay — just copy the source
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(in_path), "-c", "copy", str(out)],
            check=True, capture_output=True,
        )
        return out

    n = len(lines)
    line_h = fontsize + line_spacing
    block_h = n * line_h
    filters = []
    for i, line in enumerate(lines):
        # y offset places the i-th line so the whole block is vertically centered
        y_expr = f"(h-{block_h})/2+{i*line_h}"
        filters.append(
            f"drawtext=fontfile='{font}'"
            f":text='{_escape_drawtext(line)}'"
            f":fontcolor={fontcolor}"
            f":fontsize={fontsize}"
            f":box=1:boxcolor={boxcolor}:boxborderw={border_w}"
            f":x=(w-text_w)/2:y={y_expr}"
        )
    vf = ",".join(filters)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg drawtext failed (exit {proc.returncode}). "
            f"stderr tail: {(proc.stderr or '<empty>')[-800:]}"
        )
    return out


def _video_has_audio(path: str | Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return "audio" in (proc.stdout or "")


def mix_music(video: str | Path, music: str | Path, output: str | Path, volume: float = 0.15) -> Path:
    """Mix `music` under `video`'s audio at `volume` (0-1). Looped to video duration.

    If the video has no audio stream, `music` is used as the sole audio at
    `volume` (no mixing). Output keeps the source video stream unchanged.
    """
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if _video_has_audio(video):
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-stream_loop", "-1", "-i", str(music),
            "-filter_complex",
            f"[1:a]volume={volume}[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0",
            "-c:v", "copy",
            "-shortest",
            str(out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", f"[1:a]volume={volume}[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-shortest",
            str(out),
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mix failed (exit {proc.returncode}). "
            f"stderr tail: {(proc.stderr or '<empty>')[-800:]}"
        )
    return out


def render_short(
    source_path: str | Path,
    start_s: float,
    end_s: float,
    overlay_text: str,
    music_track: str | Path,
    final_output: str | Path,
    overlay_style: str = "dense",  # noqa: ARG001 — reserved for v0.2 (lower-third style)
    fontsize: int = 52,
) -> Path:
    """End-to-end: cut window -> text overlay -> music mix -> final mp4."""
    _ = overlay_style  # reserved for v0.2 (lower-third); v0.1 only does "dense"
    final_output = Path(final_output)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cut_path = tmp / "cut.mp4"
        overlay_path = tmp / "overlay.mp4"

        cut_window(source_path, start_s, end_s, cut_path)
        drawtext_overlay(cut_path, overlay_path, overlay_text, fontsize=fontsize)
        mix_music(overlay_path, music_track, final_output, volume=0.15)

    return final_output
