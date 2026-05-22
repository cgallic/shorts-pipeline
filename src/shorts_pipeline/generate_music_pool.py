"""Generate ElevenLabs Music tracks into a channel's music pool.

One-off rollout helper. Run once per channel when you set it up; refill the
pool every few months as you want fresh beds.

Usage:
  python -m shorts_pipeline.generate_music_pool \\
    --out-dir $SHORTS_PIPELINE_ROOT/music-pools/myfirstchannel \\
    --count 10 \\
    --prompt "warm lo-fi piano, 90 bpm, no vocals, 30 seconds"

Or with a prompts file (one ElevenLabs prompt per line, sampled randomly):
  python -m shorts_pipeline.generate_music_pool \\
    --out-dir $SHORTS_PIPELINE_ROOT/music-pools/myfirstchannel \\
    --count 10 \\
    --prompts-file /path/to/my-brand-music-prompts.txt

Requires ELEVENLABS_API_KEY in environment (or in .env loaded by python-dotenv).
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


ELEVENLABS_MUSIC_ENDPOINT = "https://api.elevenlabs.io/v1/music"


def generate_track(prompt: str, out_path: Path, length_ms: int = 30000) -> Path:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set (export it or put it in .env)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.post(
        ELEVENLABS_MUSIC_ENDPOINT,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "music_length_ms": length_ms,
            "force_instrumental": True,
            "model_id": "music_v1",
        },
        timeout=300,
    )
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return out_path


def _load_prompts(args: argparse.Namespace) -> list[str]:
    if args.prompts_file:
        lines = Path(args.prompts_file).read_text(encoding="utf-8").splitlines()
        prompts = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
        if not prompts:
            raise SystemExit(f"No prompts found in {args.prompts_file}")
        return prompts
    if args.prompt:
        return [args.prompt]
    raise SystemExit("Provide --prompt or --prompts-file")


def main() -> int:
    p = argparse.ArgumentParser(prog="generate_music_pool")
    p.add_argument("--out-dir", required=True, help="Music pool dir for channel")
    p.add_argument("--count", type=int, default=10, help="Tracks to generate (default 10)")
    p.add_argument("--length-ms", type=int, default=30000, help="Track length in ms (default 30s)")
    p.add_argument("--start-index", type=int, default=1, help="Starting track number (for resuming)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt", help="Single ElevenLabs Music prompt for all tracks")
    src.add_argument("--prompts-file", help="Text file with one prompt per line; sampled randomly")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    prompts = _load_prompts(args)

    for i in range(args.count):
        idx = args.start_index + i
        prompt = random.choice(prompts)
        out = out_dir / f"track-{idx:02d}.mp3"
        if out.exists():
            print(f"[skip] {out.name} already exists")
            continue
        print(f"[gen] {out.name}: {prompt}")
        try:
            generate_track(prompt, out, args.length_ms)
            print(f"[ok]  {out.name} written ({out.stat().st_size} bytes)")
        except Exception as e:
            print(f"[err] {out.name}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
