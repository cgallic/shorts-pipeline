"""Channel YAML config loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import yaml


@dataclass
class ChannelConfig:
    channel: str
    brand: str
    output_dir: str
    forehead_pool: str
    music_pool: str
    history_db: str
    shorts_per_day: int
    voice_prompt_file: str
    forbidden_phrases: list[str]
    cta_pool: list[str]
    hashtag_set: list[str]
    overlay_style: str
    clip_duration_s: int
    topic_universe_file: Optional[str] = None
    topic_universe_file_buyer: Optional[str] = None
    topic_universe_file_seller: Optional[str] = None
    icp_split: Optional[dict[str, int]] = None
    # Reserved for the v0.3 mascot path; safe to leave empty in v0.1.
    format_mix: dict[str, int] = field(default_factory=dict)
    mascot_chars: Union[list[str], dict[str, str], None] = None
    mascot_voice_prompt_file: Optional[str] = None
    postfx: Optional[dict] = None


REQUIRED_FIELDS = (
    "channel",
    "brand",
    "output_dir",
    "forehead_pool",
    "music_pool",
    "history_db",
    "shorts_per_day",
    "voice_prompt_file",
    "forbidden_phrases",
    "cta_pool",
    "hashtag_set",
    "overlay_style",
    "clip_duration_s",
)


def load_channel_config(path: str | Path) -> ChannelConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    for key in REQUIRED_FIELDS:
        if key not in raw:
            raise KeyError(f"Missing required field {key!r} in {path}")

    topic_universe = raw.get("topic_universe")
    topic_universe_file = raw.get("topic_universe_file")
    topic_universe_file_buyer = None
    topic_universe_file_seller = None

    if isinstance(topic_universe, dict):
        topic_universe_file_buyer = topic_universe.get("buyer")
        topic_universe_file_seller = topic_universe.get("seller")
    elif topic_universe_file is None:
        raise KeyError(
            f"Either topic_universe_file or topic_universe.{{buyer,seller}} is required in {path}"
        )

    shorts_per_day = int(raw["shorts_per_day"])

    fm = raw.get("format_mix")
    if fm is None:
        format_mix = {"forehead": shorts_per_day, "mascot": 0}
    else:
        format_mix = {
            "forehead": int(fm.get("forehead", 0)),
            "mascot": int(fm.get("mascot", 0)),
        }
        if format_mix["forehead"] + format_mix["mascot"] != shorts_per_day:
            raise ValueError(
                f"format_mix sum ({format_mix['forehead']} + {format_mix['mascot']}) "
                f"must equal shorts_per_day ({shorts_per_day}) in {path}"
            )

    mascot_chars_raw = raw.get("mascot_chars")
    if isinstance(mascot_chars_raw, list):
        mascot_chars = [str(c) for c in mascot_chars_raw]
    elif isinstance(mascot_chars_raw, dict):
        mascot_chars = {str(k): str(v) for k, v in mascot_chars_raw.items()}
    else:
        mascot_chars = None

    return ChannelConfig(
        channel=raw["channel"],
        brand=raw["brand"],
        output_dir=raw["output_dir"],
        forehead_pool=raw["forehead_pool"],
        music_pool=raw["music_pool"],
        history_db=raw["history_db"],
        shorts_per_day=shorts_per_day,
        voice_prompt_file=raw["voice_prompt_file"],
        forbidden_phrases=list(raw["forbidden_phrases"]),
        cta_pool=list(raw["cta_pool"]),
        hashtag_set=list(raw["hashtag_set"]),
        overlay_style=raw["overlay_style"],
        clip_duration_s=int(raw["clip_duration_s"]),
        topic_universe_file=topic_universe_file,
        topic_universe_file_buyer=topic_universe_file_buyer,
        topic_universe_file_seller=topic_universe_file_seller,
        icp_split=raw.get("icp_split"),
        format_mix=format_mix,
        mascot_chars=mascot_chars,
        mascot_voice_prompt_file=raw.get("mascot_voice_prompt_file"),
        postfx=raw.get("postfx"),
    )
