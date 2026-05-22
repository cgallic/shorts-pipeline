"""Multi-channel daily shorts orchestrator.

Daily cron entry point. For each channel YAML found in SHORTS_CHANNELS_DIR:
pick fresh windows, generate overlay copy via Qwen, render (cut + drawtext +
music mix), atomic-promote into output_dir, record history.

Environment variables (see .env.example):
  SHORTS_PIPELINE_ROOT    state root (history db, registry, dry-run, logs)
  SHORTS_CHANNELS_DIR     directory of channel YAMLs (default: $ROOT/channels)
  OLLAMA_URL              Ollama base URL for Qwen copy generation
  OLLAMA_MODEL            Ollama model tag
  SHORTS_CAPTION_VALIDATOR  optional dotted path `mod.sub:fn(text)->CheckResult`
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import logging
import os
import random
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .config import ChannelConfig, load_channel_config
from .cut_picker import list_takes, pick_cuts
from .history import ChannelHistory, CutsRegistry, UsedCut
from .qwen_client import QwenError, generate_copy
from .render import render_short
from .sidecar import sanitize_title, write_sidecar


log = logging.getLogger("shorts-pipeline")


# ─── Environment helpers ─────────────────────────────────────────────────────


def _root() -> Path:
    return Path(os.environ.get("SHORTS_PIPELINE_ROOT", "/srv/shorts-pipeline"))


def _channels_dir() -> Path:
    if env := os.environ.get("SHORTS_CHANNELS_DIR"):
        return Path(env)
    return _root() / "channels"


def _registry_path() -> Path:
    return _root() / "history" / "cuts-registry.db"


def _status_path() -> Path:
    return _root() / "logs" / "shorts-pipeline-status.txt"


def discover_channels() -> dict[str, Path]:
    """Map channel slug → YAML config path. Slug = filename stem."""
    d = _channels_dir()
    if not d.exists():
        return {}
    return {p.stem: p for p in sorted(d.glob("*.yaml")) if p.is_file()}


# ─── Caption-validator hook ──────────────────────────────────────────────────


def _load_validator() -> Optional[Callable]:
    """Resolve SHORTS_CAPTION_VALIDATOR (`pkg.mod:fn`) → callable, or None."""
    spec = os.environ.get("SHORTS_CAPTION_VALIDATOR", "").strip()
    if not spec:
        return None
    if ":" not in spec:
        log.warning("SHORTS_CAPTION_VALIDATOR=%r is not `module:callable` form — ignoring", spec)
        return None
    mod_name, attr = spec.split(":", 1)
    try:
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    except Exception as e:
        log.warning("Could not load caption validator %r: %s", spec, e)
        return None


# ─── Atomic pair emission ────────────────────────────────────────────────────
# Posters that watch a folder (Repurpose.io etc.) will pick up a .mp4 the
# instant it appears. If the sibling .txt isn't there yet, the poster ships
# the bare filename as the caption. To prevent that, every render targets a
# `.staging/` subdir; both files only land in the watched output_dir via two
# renames done in this order: txt first, mp4 last. Path.replace() is atomic
# within the same filesystem on POSIX and on Windows NTFS.


def _staging_dir(output_dir: Path) -> Path:
    d = output_dir / ".staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _promote_pair(staging: Path, output_dir: Path, safe_title: str) -> None:
    staged_mp4 = staging / f"{safe_title}.mp4"
    staged_txt = staging / f"{safe_title}.txt"
    if not staged_mp4.exists():
        raise RuntimeError(f"staged mp4 missing: {staged_mp4}")
    if not staged_txt.exists():
        raise RuntimeError(f"staged txt missing: {staged_txt}")
    final_mp4 = output_dir / f"{safe_title}.mp4"
    final_txt = output_dir / f"{safe_title}.txt"
    staged_txt.replace(final_txt)
    staged_mp4.replace(final_mp4)


def _cleanup_staged(staging: Path, safe_title: str) -> None:
    """Best-effort cleanup if a render or sidecar step failed mid-flight."""
    for suffix in (".mp4", ".txt"):
        p = staging / f"{safe_title}{suffix}"
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def _quarantine_pair(staging: Path, safe_title: str, result) -> Path:
    """Move a validator-rejected pair into .staging/.lint-rejects/."""
    quarantine = staging / ".lint-rejects"
    quarantine.mkdir(parents=True, exist_ok=True)
    moved_mp4 = quarantine / f"{safe_title}.mp4"
    moved_txt = quarantine / f"{safe_title}.txt"
    verdict_path = quarantine / f"{safe_title}.violations.json"
    for suffix, target in ((".mp4", moved_mp4), (".txt", moved_txt)):
        p = staging / f"{safe_title}{suffix}"
        if p.exists():
            p.replace(target)
    verdict_path.write_text(
        json.dumps(_serialize_result(result), indent=2),
        encoding="utf-8",
    )
    return quarantine


def _serialize_result(result: Any) -> dict:
    """Best-effort serialization for a validator result of unknown shape."""
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    if isinstance(result, dict):
        return result
    as_dict_fn = getattr(result, "as_dict", None)
    if callable(as_dict_fn):
        try:
            v = as_dict_fn()
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    return {"repr": repr(result), "passed": bool(getattr(result, "passed", False))}


def _validate_caption(
    validator: Optional[Callable],
    staging: Path,
    safe_title: str,
    caption_text: str,
    channel: str,
) -> bool:
    """Run the optional validator. Returns True if pair may be promoted."""
    if validator is None:
        return True
    try:
        result = validator(caption_text)
    except Exception as e:
        log.warning("Caption validator raised on %s/%s — promoting anyway: %s",
                    channel, safe_title, e)
        return True
    if getattr(result, "passed", True):
        return True
    _quarantine_pair(staging, safe_title, result)
    log.error("Caption rejected for %s/%s — quarantined", channel, safe_title)
    return False


# ─── Topic-seed picking ──────────────────────────────────────────────────────


def load_topic_universe(cfg: ChannelConfig) -> dict[str, list[str]]:
    """Returns {'main': [...]} or {'buyer': [...], 'seller': [...]}."""
    if cfg.topic_universe_file:
        topics = Path(cfg.topic_universe_file).read_text(encoding="utf-8").splitlines()
        return {"main": [t.strip() for t in topics if t.strip()]}
    out: dict[str, list[str]] = {}
    if cfg.topic_universe_file_buyer:
        topics = Path(cfg.topic_universe_file_buyer).read_text(encoding="utf-8").splitlines()
        out["buyer"] = [t.strip() for t in topics if t.strip()]
    if cfg.topic_universe_file_seller:
        topics = Path(cfg.topic_universe_file_seller).read_text(encoding="utf-8").splitlines()
        out["seller"] = [t.strip() for t in topics if t.strip()]
    return out


def pick_topic_seeds(cfg: ChannelConfig, history: ChannelHistory) -> list[tuple[str, str]]:
    """Returns list of (topic_seed, icp_label) tuples. icp_label='main' for single-ICP."""
    universe = load_topic_universe(cfg)

    if "main" in universe:
        topics = [(t, "main") for t in universe["main"]]
        topics = [t for t in topics if not history.is_topic_seed_in_cooldown(t[0], days=3)]
        random.shuffle(topics)
        if len(topics) < cfg.shorts_per_day:
            raise RuntimeError(
                f"Not enough fresh topics for {cfg.channel}: {len(topics)} of {cfg.shorts_per_day}"
            )
        return topics[: cfg.shorts_per_day]

    icp_split = cfg.icp_split or {}
    seeds: list[tuple[str, str]] = []
    for icp, count in icp_split.items():
        candidates = [(t, icp) for t in universe.get(icp, [])]
        candidates = [t for t in candidates if not history.is_topic_seed_in_cooldown(t[0], days=3)]
        random.shuffle(candidates)
        if len(candidates) < count:
            raise RuntimeError(
                f"Not enough fresh {icp} topics for {cfg.channel}: {len(candidates)} of {count}"
            )
        seeds.extend(candidates[:count])
    return seeds


# ─── Cross-channel + same-channel cooldown ───────────────────────────────────


def gather_used_windows(
    forehead_dir: str,
    registry: CutsRegistry,
    history: ChannelHistory,
    channel: str,
    cooldown_days: int = 14,
) -> set[tuple[str, float, float]]:
    """Windows to exclude when picking cuts for `channel`.

    Rules:
      1. Cross-channel: a window claimed by ANY other channel is permanently blocked.
      2. Same-channel: a window this channel used within `cooldown_days` is blocked.
         Older same-channel windows can be re-used; the overlay text is different.
    """
    used: set[tuple[str, float, float]] = set()
    for take in list_takes(Path(forehead_dir)):
        for s, e in registry.used_windows_for_source(str(take), exclude_channel=channel):
            used.add((str(take), s, e))
        for s, e in registry.used_windows_for_source(str(take)):
            if (str(take), s, e) in used:
                continue
            if history.is_window_in_cooldown(str(take), s, e, days=cooldown_days):
                used.add((str(take), s, e))
    return used


# ─── Per-channel pipeline ────────────────────────────────────────────────────


def run_channel(
    channel_slug: str,
    cfg_path: str,
    registry: CutsRegistry,
    dry_run: bool,
    validator: Optional[Callable] = None,
    count_override: Optional[int] = None,
) -> int:
    """Run pipeline for one channel. Returns count of shorts produced."""
    cfg = load_channel_config(cfg_path)

    # v0.1 ships only the forehead-overlay path. Mascot allocation requires v0.3.
    if cfg.format_mix.get("mascot", 0) > 0:
        log.warning(
            "Channel %s has format_mix.mascot=%d but v0.1 does not include the "
            "mascot render path — those slots will not be produced. See docs/extraction-status.md.",
            channel_slug, cfg.format_mix.get("mascot", 0),
        )

    history = ChannelHistory(cfg.history_db, channel_slug)
    history.init()

    topic_seeds = pick_topic_seeds(cfg, history)
    fh_count = cfg.format_mix.get("forehead", cfg.shorts_per_day)
    if count_override is not None:
        fh_count = min(fh_count, count_override)
    forehead_seeds = topic_seeds[:fh_count]

    if dry_run:
        output_dir = _root() / "dry-run" / channel_slug
    else:
        output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    voice_prompt = Path(cfg.voice_prompt_file).read_text(encoding="utf-8")

    music_pool = sorted(Path(cfg.music_pool).glob("*.mp3"))
    if not music_pool:
        log.error("No music tracks in %s", cfg.music_pool)
        return 0

    import yaml as _yaml
    raw = _yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
    stats = raw.get("stats_to_cite", [])

    if fh_count == 0:
        return 0
    if not list_takes(Path(cfg.forehead_pool)):
        log.error("No forehead takes in %s", cfg.forehead_pool)
        return 0

    used_windows = gather_used_windows(cfg.forehead_pool, registry, history, channel_slug)
    cuts = pick_cuts(
        forehead_dir=cfg.forehead_pool,
        count=fh_count,
        clip_duration_s=cfg.clip_duration_s,
        already_used=used_windows,
    )

    success = 0
    for cut, (topic_seed, _) in zip(cuts, forehead_seeds):
        try:
            copy = generate_copy(
                system_prompt=voice_prompt,
                topic_seed=topic_seed,
                cta_pool=cfg.cta_pool,
                forbidden_phrases=cfg.forbidden_phrases,
                stats=stats,
            )
        except QwenError as e:
            log.warning("Qwen parse error for seed %r in %s: %s — skipping",
                        topic_seed, channel_slug, e)
            continue
        except Exception as e:
            log.warning("Qwen error in %s for %r: %s — retrying",
                        channel_slug, topic_seed, e)
            try:
                copy = generate_copy(
                    system_prompt=voice_prompt,
                    topic_seed=topic_seed,
                    cta_pool=cfg.cta_pool,
                    forbidden_phrases=cfg.forbidden_phrases,
                    stats=stats,
                )
            except Exception as e2:
                log.error("Qwen retry failed for %s/%r: %s",
                          channel_slug, topic_seed, e2)
                continue

        if history.has_hook(copy.overlay):
            log.info("Overlay duplicate (skipping): %r", copy.overlay[:60])
            continue

        safe_title = sanitize_title(copy.title)
        music = random.choice(music_pool)
        staging = _staging_dir(output_dir)
        staged_mp4 = staging / f"{safe_title}.mp4"
        final_path = output_dir / f"{safe_title}.mp4"

        try:
            render_short(
                source_path=cut.source_path,
                start_s=cut.start_s,
                end_s=cut.end_s,
                overlay_text=copy.overlay,
                music_track=music,
                final_output=staged_mp4,
                overlay_style=cfg.overlay_style,
            )
        except Exception as e:
            log.error("Render failed %s/%s: %s", channel_slug, safe_title, e)
            _cleanup_staged(staging, safe_title)
            continue

        try:
            write_sidecar(out_dir=staging, title=copy.title,
                          caption=copy.caption, hashtags=cfg.hashtag_set)
        except Exception as e:
            log.error("Sidecar failed %s/%s: %s — discarding", channel_slug, safe_title, e)
            _cleanup_staged(staging, safe_title)
            continue

        if not _validate_caption(validator, staging, safe_title, copy.caption, channel_slug):
            continue  # pair has been quarantined

        try:
            _promote_pair(staging, output_dir, safe_title)
        except Exception as e:
            log.error("Promote failed %s/%s: %s — discarding", channel_slug, safe_title, e)
            _cleanup_staged(staging, safe_title)
            continue

        if not dry_run:
            history.record(UsedCut(
                source_path=cut.source_path,
                start_s=cut.start_s, end_s=cut.end_s,
                hook=copy.overlay, title=copy.title,
                topic_seed=topic_seed, music_track=str(music),
                output_path=str(final_path),
            ))
            registry.register(cut.source_path, cut.start_s, cut.end_s, channel_slug)
        success += 1

    log.info("Channel %s: %d/%d shorts produced",
             channel_slug, success, cfg.shorts_per_day)
    return success


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(prog="shorts-pipeline")
    p.add_argument("--channel", help="Run only this channel slug (default: all discovered)")
    p.add_argument("--dry-run", action="store_true",
                   help=f"Write to {_root()}/dry-run/<channel>/ instead of output_dir; "
                        "do not record history.")
    p.add_argument("--count", type=int,
                   help="Override shorts_per_day for this run (testing)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    channels = discover_channels()
    if not channels:
        log.error("No channels found in %s — add at least one *.yaml.", _channels_dir())
        return 1

    if args.channel and args.channel not in channels:
        log.error("Channel %r not found. Available: %s",
                  args.channel, ", ".join(sorted(channels)))
        return 1

    registry = CutsRegistry(_registry_path())
    registry.init()

    validator = _load_validator()
    if validator:
        log.info("Caption validator loaded from SHORTS_CAPTION_VALIDATOR")

    targets = [args.channel] if args.channel else sorted(channels.keys())
    total_success = 0
    total_target = 0
    for ch in targets:
        cfg_path = str(channels[ch])
        cfg = load_channel_config(cfg_path)
        target = args.count or cfg.shorts_per_day
        total_target += target
        try:
            total_success += run_channel(
                ch, cfg_path, registry,
                dry_run=args.dry_run,
                validator=validator,
                count_override=args.count,
            )
        except Exception as e:
            log.error("Channel %s failed: %s", ch, e)

    status_path = _status_path()
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        f"{dt.datetime.now().isoformat()} success={total_success} target={total_target}\n",
        encoding="utf-8",
    )

    return 0 if total_success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
