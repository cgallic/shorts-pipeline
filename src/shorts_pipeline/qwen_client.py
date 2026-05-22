"""Qwen via Ollama HTTP client for overlay copy generation.

Returns a QwenCopy with:
- overlay: 8-12 line lowercase tactical reveal (goes on screen)
- caption: single-paragraph lowercase explainer ending with comment-keyword CTA
- title: filename-safe short title

Connects to {OLLAMA_URL}/api/generate. Override per-call via the `ollama_url`
and `model` kwargs, or globally via the OLLAMA_URL and OLLAMA_MODEL env vars.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import requests


class QwenError(RuntimeError):
    pass


@dataclass
class QwenCopy:
    overlay: str
    caption: str
    title: str


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _default_ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


def _default_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "qwen3:14b")


def parse_qwen_response(raw: str) -> QwenCopy:
    cleaned = _FENCE.sub("", raw).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise QwenError(f"Invalid JSON from Qwen: {raw!r}") from e

    for key in ("overlay", "caption", "title"):
        if key not in data:
            raise QwenError(f"Missing field {key!r} in Qwen response: {data}")

    return QwenCopy(
        overlay=str(data["overlay"]).strip(),
        caption=str(data["caption"]).strip(),
        title=str(data["title"]).strip(),
    )


def build_prompt(
    system_prompt: str,
    topic_seed: str,
    cta_pool: list[str],
    forbidden_phrases: list[str],
    stats: list[str],
) -> str:
    """The system_prompt already contains the full style spec + few-shot examples.
    We only inject the topic seed, the channel-specific CTA pool, and forbidden phrases.
    """
    return (
        f"{system_prompt}\n\n"
        f"FORBIDDEN PHRASES (never write these): {', '.join(forbidden_phrases)}\n\n"
        f"STATS YOU MAY CITE (verbatim only — do not invent numbers):\n"
        + "\n".join(f"  - {s}" for s in stats) + "\n\n"
        f"PICK A CTA FROM THIS POOL (use the exact wording, lowercase):\n"
        + "\n".join(f'  - {c}' for c in cta_pool) + "\n\n"
        f"TOPIC SEED FOR THIS POST: {topic_seed}\n\n"
        "Write ONE original tactical reveal in the brand voice based on this scenario. "
        "Make it specific. Tell a real story. Name the hidden mechanism. Give the exact counter-tactic. "
        "Hit emotionally. No repeats of phrasing you've used before.\n\n"
        "Output STRICT JSON only — no markdown fences, no preamble, no commentary:\n"
        '{"overlay": "<8-12 line lowercase tactical reveal, separated by \\n>", '
        '"caption": "<single lowercase paragraph 40-60 words ending with a comment-keyword CTA>", '
        '"title": "<filename-safe short title 40-70 chars, lowercase>"}\n'
    )


def generate_copy(
    system_prompt: str,
    topic_seed: str,
    cta_pool: list[str],
    forbidden_phrases: list[str],
    stats: list[str],
    ollama_url: str | None = None,
    model: str | None = None,
    timeout: float = 180.0,
) -> QwenCopy:
    prompt = build_prompt(system_prompt, topic_seed, cta_pool, forbidden_phrases, stats)
    url = ollama_url or _default_ollama_url()
    mdl = model or _default_model()
    resp = requests.post(
        f"{url}/api/generate",
        json={
            "model": mdl,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.85, "num_predict": 1024},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    raw = resp.json().get("response", "")
    return parse_qwen_response(raw)
