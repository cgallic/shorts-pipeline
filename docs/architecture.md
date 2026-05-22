# Architecture

This goes a level deeper than the README. Read the README first.

## Design principles

1. **State lives outside the repo.** The repo is code + templates. All channel configs, prompts, music, footage, output, and history live under a configurable `SHORTS_PIPELINE_ROOT` so the repo can be reinstalled / re-pulled without touching state.
2. **One config per channel, no code edits to add channels.** The orchestrator discovers `*.yaml` under `SHORTS_CHANNELS_DIR`. Nothing about a new channel requires a Python change.
3. **Cooldowns, not bans.** Three layers (topic seed 3d, window 14d, hook forever) so the system degrades gracefully as footage and ideas run thin. The default behavior when something is exhausted is to skip a slot, not to crash the run.
4. **Atomic external boundary.** Posting services that watch a folder are an external surface and we have no control over their robustness. The pipeline takes responsibility for never giving them a partial pair.
5. **The LLM is a slot machine, not a workflow.** Each Qwen call is independent, stateless, and either produces a valid `{overlay, caption, title}` or is discarded. No multi-turn, no retries-with-feedback. This is intentional — it makes failures localized and the pipeline as a whole much more debuggable.

## Module-by-module

### `auto_shorts_channels.py` — orchestrator

The CLI entry point (`shorts-pipeline`). Responsibilities:

- Parse args (`--channel`, `--dry-run`, `--count`).
- Open the cross-channel `CutsRegistry` once at startup.
- For each channel: open its `ChannelHistory`, pick topic seeds, pick cuts, generate copy, render, write sidecar, optionally validate, atomic-promote, record.

Failures are scoped: one short failing skips the slot; one channel failing skips the channel; nothing failing stops the whole run.

### `config.py` — channel YAML loader

`ChannelConfig` is a frozen-ish dataclass that mirrors the YAML 1:1. The loader enforces required fields, validates `format_mix` sums to `shorts_per_day`, and resolves the `topic_universe` vs `topic_universe_file` shapes.

### `cut_picker.py` — ffprobe + window enumeration

- `ffprobe_duration(path)` shells out to ffprobe for the take's duration.
- `enumerate_windows(duration, clip_duration_s)` enumerates non-overlapping windows in the middle 60% of the take.
- `pick_cuts(...)` reads every take in `forehead_pool`, builds all candidate windows, filters by `already_used`, shuffles, returns up to N.

The middle-60% rule exists because long takes typically have framing-adjust at the head and wind-down at the tail; the middle is where the content lives. Configurable per-channel via `clip_duration_s`.

### `history.py` — SQLite

Two classes:

- `ChannelHistory` — per-channel state. Tables: `used_cuts` (every short ever shipped on this channel — window, hook, title, topic seed, music, output path). Indexes on source path, topic seed, window tuple, and hook.
- `CutsRegistry` — global. Table: `channel_cuts` keyed on `(source_path, start_s, end_s)`. Insert-or-ignore. Used to answer "has any channel claimed this window?"

Cooldown queries are written as `created_at > datetime('now', '-N days')` for human-readable windowing.

### `qwen_client.py` — Ollama HTTP

POSTs to `{OLLAMA_URL}/api/generate` with `format: "json"` and `think: false`. Parses the response strictly: the three required keys must exist, anything else is rejected. Markdown code-fences are stripped if Qwen wraps the JSON anyway.

The prompt template injects:
- The channel-specific voice system prompt (loaded from `voice_prompt_file`)
- A list of forbidden phrases
- A list of stats the model may cite verbatim (and a hard rule against inventing numbers)
- The CTA pool to pick from
- The topic seed for this slot

### `render.py` — ffmpeg

Three steps, each a separate ffmpeg invocation:

1. `cut_window` — seek + cut to a temp .mp4
2. `drawtext_overlay` — burn the multi-line overlay text onto the clip
3. `mix_music` — loop the music track under the video audio at 15% volume, or as the sole audio if the video had no audio

The drawtext overlay uses a styling function that splits the overlay text on newlines, vertically centers the block, and renders each line with a configurable font. Default font is DejaVu Sans on Linux; override via `OVERLAY_FONT_PATH`.

### `sidecar.py` — caption writer

`sanitize_title(title)` is the single source of truth for filename safety. The same sanitized title is used for the `.mp4` and `.txt`, so they pair up by stem.

`write_sidecar(out_dir, title, caption, hashtags)` writes a single-line `caption hashtag1 hashtag2 ...` file. Inline hashtags inside the caption body are scrubbed — the hashtag set comes from channel config only, so a brand's hashtag bag is consistent across every short.

## Atomic-promote in detail

```python
# in orchestrator, after sidecar + optional validator pass:
staged_mp4 = staging / f"{safe_title}.mp4"
staged_txt = staging / f"{safe_title}.txt"
final_mp4  = output_dir / f"{safe_title}.mp4"
final_txt  = output_dir / f"{safe_title}.txt"

# both exist in staging — promote
staged_txt.replace(final_txt)   # txt first
staged_mp4.replace(final_mp4)   # mp4 last
```

`Path.replace()` is `os.replace()` under the hood, which on POSIX is `rename(2)` and on Windows (NTFS) is `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING`. Both are atomic within the same filesystem.

The order is load-bearing. An external watcher that does `glob *.mp4` and then opens `<stem>.txt` will see the txt already exists by the time it notices the mp4.

## Caption validation hook

A common need is to plug in brand-voice linting — banned phrases, AI-tell detection, etc. The hook is opt-in via `SHORTS_CAPTION_VALIDATOR` in `.env`:

```
SHORTS_CAPTION_VALIDATOR=mypkg.brandguard:check
```

The dotted path resolves to a callable `check(text: str) -> CheckResult` where `CheckResult` has `passed: bool` and `violations: list`. On `passed=False`, the staged pair is moved to `.staging/.lint-rejects/<safe_title>.{mp4,txt,violations.json}` and the short is dropped from the run. The reject artifacts persist for review.

If the env var is empty, no validator runs (the default).

## Cross-channel dedup logic

When channel B runs:

```python
used_windows = set()
for take in list_takes(forehead_pool):
    # block windows claimed by A or C
    for (s, e) in registry.used_windows_for_source(take, exclude_channel="B"):
        used_windows.add((take, s, e))
    # block windows B used recently (within 14d)
    for (s, e) in registry.used_windows_for_source(take):  # all channels incl B
        if (take, s, e) in used_windows: continue
        if channel_history.is_window_in_cooldown(take, s, e, days=14):
            used_windows.add((take, s, e))
```

The asymmetry — other channels' usage is permanent, your own usage decays — exists because:

- Cross-channel duplicate windows are *brand* leakage (a viewer who follows two channels sees the same clip with different copy → looks scammy).
- Same-channel re-use is fine after enough time has passed; you'll write different overlay text and the audience won't recognize the source clip.

## What's not here in v0.1

The full upstream system has a second render path — animated mascot stop-motion. That path is significantly more complex (character pose libraries, anchor-point markup, Rhubarb lipsync, frame-timing post-fx) and is being held back for v0.3 to keep v0.1 small and inspectable.

If you want the mascot path now, see [extraction-status.md](extraction-status.md) for which files in the upstream to copy in.
