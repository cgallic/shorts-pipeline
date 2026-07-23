# shorts-pipeline

> A daily cron that ships YouTube Shorts. Slices 6-second windows out of long-take video, writes overlay copy with a local LLM, mixes brand-specific music underneath, and delivers atomic `.mp4 + .txt` pairs into a folder a posting service (Repurpose.io, Buffer, manual upload, etc.) watches. Multi-channel out of the box — one config file per brand.

![status: v0.1](https://img.shields.io/badge/status-v0.1-blue) ![license: MIT](https://img.shields.io/badge/license-MIT-green) ![python](https://img.shields.io/badge/python-3.10%2B-blue)

---

## Table of contents

- [What this is, in one breath](#what-this-is-in-one-breath)
- [Why it's interesting](#why-its-interesting)
- [At a glance](#at-a-glance)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Daily run lifecycle](#daily-run-lifecycle)
- [Channel configuration](#channel-configuration)
- [Operations](#operations)
- [The three delivery guarantees](#the-three-delivery-guarantees)
- [Project layout](#project-layout)
- [Status & roadmap](#status--roadmap)
- [Origin](#origin)

---

## What this is, in one breath

You film a few hours of long-take "talking head" video once. Every morning, this pipeline picks unused 6-second slices out of that footage, asks a local LLM to write a short tactical-reveal overlay in your brand voice, burns the text onto the clip, mixes a 15%-volume brand-specific music bed underneath, and drops the finished MP4 next to its caption sidecar into an output folder. Whatever you have watching that folder — Repurpose.io, a Zapier flow, a manual upload pass — ships them.

Three channels with three voices and three music pools, all from the same long-take pool, can run from the same cron — they just won't ever ship the same 6-second slice.

## Why it's interesting

The interesting parts aren't the obvious ones.

- **Atomic pair delivery.** Posting services that watch a Drive folder will sometimes catch a `.mp4` mid-write and post it with the bare filename as the caption. This pipeline stages every render into a hidden `.staging/` subdir and only promotes the pair into the watched folder once both files exist, with `.txt` renamed before `.mp4`. Three layers of guards on top of that.
- **Cross-channel deduplication.** A single 6-second window from your forehead-source pool can only ever be claimed by **one** channel, ever. A separate registry SQLite outside the per-channel history enforces that. Channels can still re-use their own old windows after a cooldown — the overlay text is different each time.
- **Strictly-JSON LLM output.** The Qwen client uses Ollama's `format: "json"` plus a structured prompt to make Qwen return a well-formed `{overlay, caption, title}` triple every time. The pipeline parses on the boundary; bad JSON is logged and skipped, not crashed on.
- **Caption sanitization that survives Repurpose.** Filenames get `[\\/:"?<>|*]` stripped, apostrophes removed, dots → spaces, whitespace collapsed, truncated to 80 chars. Inline `#hashtags` Qwen leaves in the caption body are scrubbed — hashtags only come from the channel config so the bag is consistent per brand.
- **No cloud dependency for the LLM.** Default points at a local Ollama. Bring your own model. (Pipeline was developed on Qwen 3.6 quantized to fit on a single 24 GB GPU.)
- **Cooldowns at three levels.** Topic seeds (3 days, prevents narrative repetition), windows (14 days, allows old footage re-use), and hooks (forever — if the exact overlay first line was ever shipped, skip).

## At a glance

```
                                  ┌────────────────────────────────────┐
forehead-source/                  │            Daily cron              │
  long-take-001.mp4 ─┐            │   (systemd timer, 09:00 UTC)       │
  long-take-002.mp4 ─┼──► picker ─┤                                    │
  long-take-003.mp4 ─┘            │   for each channel:                │
                                  │     1. pick fresh 6s windows       │
channels/                         │     2. ask Qwen for overlay copy   │
  brand-a.yaml ──────────────────►│     3. cut + drawtext + music mix  │
  brand-b.yaml ──────────────────►│     4. write sidecar .txt          │
  brand-c.yaml ──────────────────►│     5. atomic promote into output  │
                                  │                                    │
prompts/                          └──────┬──────────┬──────────┬───────┘
  brand-a-voice.md                       │          │          │
  brand-a-topics.txt                     ▼          ▼          ▼
                                   output/    output/    output/
music-pools/                       brand-a/   brand-b/   brand-c/
  brand-a/*.mp3                    *.mp4      *.mp4      *.mp4
  brand-b/*.mp3                    *.txt      *.txt      *.txt
  brand-c/*.mp3                          │          │          │
                                          ▼          ▼          ▼
                                              folder-watching service
                                          (Repurpose.io / Buffer / scp)
```

Order-of-magnitude numbers from one production deployment (3 channels × 6 shorts/day):

| Cost / step           | Per short                  |
| --------------------- | -------------------------- |
| Qwen overlay copy     | ~free (local LLM, ~5 s GPU) |
| Music bed             | one-off, ~$0.30 per 30-s track via ElevenLabs Music, 10 tracks per channel = ~$3/channel/lifetime |
| Compute               | one CPU + GPU on a single box, ~90 s end-to-end per short |
| Delivery              | folder watcher (Repurpose.io etc.) |

## Quick start

```bash
# 1. Clone + install
git clone https://github.com/cgallic/shorts-pipeline.git
cd shorts-pipeline
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

# 2. Environment
cp .env.example .env
# edit .env — at minimum point OLLAMA_URL at a running Ollama and pick a model

# 3. Working tree (NOT inside the repo — see .env SHORTS_PIPELINE_ROOT)
export ROOT=/srv/shorts-pipeline                    # or wherever
mkdir -p $ROOT/{channels,prompts,output,forehead-source,music-pools,history,logs}

# 4. Copy one of the examples to start
cp examples/channels/example.yaml         $ROOT/channels/myfirstchannel.yaml
cp examples/prompts/example-voice.md      $ROOT/prompts/myfirstchannel-voice.md
cp examples/prompts/example-topics.txt    $ROOT/prompts/myfirstchannel-topics.txt
# edit the YAML — fix paths, brand, CTAs, hashtags

# 5. Drop a long take into forehead-source/, drop a music track into music-pools/myfirstchannel/

# 6. Dry run (writes into $ROOT/dry-run/<channel>, doesn't touch real output_dir)
shorts-pipeline --channel myfirstchannel --dry-run

# 7. Real run
shorts-pipeline --channel myfirstchannel

# 8. (production) Schedule via systemd — see deploy/systemd/
```

## Architecture

The pipeline is six small modules and an orchestrator. The orchestrator is the only thing that knows about "channels" — every other module operates on plain primitives.

```
auto_shorts_channels.py     orchestrator + CLI entry point
├── config.py               YAML → ChannelConfig dataclass
├── cut_picker.py           ffprobe + window enumeration + dedup against already-used
├── history.py              per-channel SQLite + cross-channel cuts registry
├── qwen_client.py          Ollama HTTP client, strict-JSON parsing
├── render.py               cut window via ffmpeg, drawtext overlay, music mix
└── sidecar.py              .mp4-adjacent .txt caption + filename sanitization
```

### Data flow for one short

```
[topic_seed]    ──► qwen_client ──► (overlay, caption, title)
                                          │
[forehead pool] ──► cut_picker ──► Window(source, start_s, end_s)
                                          │
                                          ▼
                  render.py:  ffmpeg cut → drawtext overlay → music mix
                                          │
                                          ▼
                  sidecar.py: write `<sanitized title>.txt` alongside .mp4
                                          │
                                          ▼
                  orchestrator: atomic promote staging → output_dir
                                  (txt rename FIRST, mp4 rename LAST)
                                          │
                                          ▼
                  history.record(...) + registry.register(...)
```

### Caption validation hook

The orchestrator calls an optional `caption_validator(text) -> CheckResult` between sidecar write and atomic promote. If `SHORTS_CAPTION_VALIDATOR` is set in `.env` to a dotted path (`mypkg.brandguard:check`), the validator is loaded and called per render. Failures move the staged pair to `.staging/.lint-rejects/` instead of promoting. Leave the env var blank to disable.

This is the seam where you plug in brand-voice linting (banned phrases, AI-tell detection, etc.) without baking it into the pipeline.

## Daily run lifecycle

When the systemd timer fires at 09:00 UTC:

1. **Open the cross-channel cuts registry** (`history/cuts-registry.db`). This is the global "who used what" ledger.
2. **For each `channels/*.yaml` config**:
   1. Load the channel config + open the per-channel history DB.
   2. **Pick topic seeds.** Round-robin through `<channel>-topics.txt`, excluding any seed used within 3 days. For dual-ICP channels, split per `icp_split:` (e.g. `{buyer: 3, seller: 3}`).
   3. **Gather already-used windows.** Cross-channel: any window claimed by any other channel is permanently blocked. Same-channel: any window this channel used within 14 days is blocked.
   4. **Pick cuts.** ffprobe each `forehead-source/*.mp4`, enumerate non-overlapping 6-second windows in the middle 60% of each take (skip first/last 20% — usually setup/wind-down), filter out blocked windows, shuffle, take N.
   5. **For each (cut, seed)**:
      - Ask Qwen for `{overlay, caption, title}`. One retry on transport error. On JSON error, log + skip slot.
      - Check `history.has_hook(overlay)` — if Qwen produced the exact same first line ever before, skip.
      - `sanitize_title(title) → safe_title`
      - Render into `<output_dir>/.staging/<safe_title>.mp4`
      - Write sidecar `<output_dir>/.staging/<safe_title>.txt`
      - Optional caption-validator. Quarantine to `.lint-rejects/` on block.
      - **Atomic promote**: `.txt` rename first, `.mp4` rename last. Either both files appear in `output_dir` or neither does.
      - `history.record(...)` + `registry.register(...)`
3. **Write status file** (`logs/shorts-pipeline-status.txt`) with `success=N target=M`.

If any single short fails, the channel keeps going. If a channel fails entirely, the next channel still runs.

## Channel configuration

A channel YAML is a single brand's complete delivery contract.

```yaml
# Identity ------------------------------------------------------------------
channel: myfirstchannel        # slug used internally
brand: My First Channel        # display name

# Filesystem layout ---------------------------------------------------------
output_dir: /srv/shorts-pipeline/output/myfirstchannel
forehead_pool: /srv/shorts-pipeline/forehead-source     # SHARED across channels
music_pool: /srv/shorts-pipeline/music-pools/myfirstchannel   # per-channel
history_db: /srv/shorts-pipeline/history/myfirstchannel.db    # per-channel

# Cadence -------------------------------------------------------------------
shorts_per_day: 6
clip_duration_s: 6

# Copy generation -----------------------------------------------------------
voice_prompt_file: /srv/shorts-pipeline/prompts/myfirstchannel-voice.md
topic_universe_file: /srv/shorts-pipeline/prompts/myfirstchannel-topics.txt

# Voice guardrails ----------------------------------------------------------
forbidden_phrases:
  - "leverage"
  - "solution"
  - "transform your business"

cta_pool:
  - 'comment "demo" for the walkthrough'
  - 'comment "demo" for the pricing sheet'

hashtag_set:
  - "#mybrand"
  - "#mybrandcategory"

# Stats the LLM may cite verbatim (NEVER invent numbers)
stats_to_cite:
  - "We saw a 3x lift in conversion"
  - "Replaced a 4-person team"

# Render style --------------------------------------------------------------
overlay_style: dense           # "dense" (full-bleed) or "lower-third" (planned)
```

For dual-ICP channels (e.g. a marketplace that needs to talk to both sides), replace `topic_universe_file:` with:

```yaml
topic_universe:
  buyer:  /srv/shorts-pipeline/prompts/myfirstchannel-buyer-topics.txt
  seller: /srv/shorts-pipeline/prompts/myfirstchannel-seller-topics.txt
icp_split:
  buyer: 3
  seller: 3
```

See [docs/adding-a-channel.md](docs/adding-a-channel.md) for the full step-by-step.

## Operations

### Where state lives

Nothing about runtime state lives in the repo. The repo is code + examples. State lives under `SHORTS_PIPELINE_ROOT`:

```
$SHORTS_PIPELINE_ROOT/
├── channels/                 # one .yaml per channel
├── prompts/                  # voice + topics files referenced by YAMLs
├── forehead-source/          # long-take .mp4 source (shared across channels)
├── music-pools/<channel>/    # per-channel music tracks
├── output/<channel>/         # final deliverables — watched externally
│   └── .staging/             # transient — atomic-promote workspace
├── history/                  # per-channel SQLite + cross-channel registry
├── logs/                     # cron output, status file
└── dry-run/<channel>/        # --dry-run target
```

### systemd

The two units in `deploy/systemd/` run the pipeline daily and capture logs.

```bash
sudo cp deploy/systemd/shorts-pipeline.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shorts-pipeline.timer
systemctl list-timers shorts-pipeline.timer   # confirm next fire
journalctl -u shorts-pipeline.service -n 200  # last run logs
```

The service is a `Type=oneshot` that runs `shorts-pipeline` and exits. The timer fires it daily.

### Adding a channel

See [docs/adding-a-channel.md](docs/adding-a-channel.md). One YAML, one voice prompt, one topics file. No code edits — the orchestrator auto-discovers every `*.yaml` in `SHORTS_CHANNELS_DIR`.

### Music pools

The bundled helper `generate_music_pool.py` wraps the ElevenLabs Music API. Pre-generate ~10 instrumental tracks per channel, drop them in `music-pools/<channel>/`, and the orchestrator picks one at random per short. Any 30-second-or-longer .mp3 works; you don't have to use ElevenLabs.

```bash
python -m shorts_pipeline.generate_music_pool \
  --out-dir $SHORTS_PIPELINE_ROOT/music-pools/myfirstchannel \
  --count 10 \
  --prompt "warm lo-fi piano, 90 bpm, no vocals, 30 seconds"

# Or sample from a prompts file (one prompt per line; pool gets variety):
python -m shorts_pipeline.generate_music_pool \
  --out-dir $SHORTS_PIPELINE_ROOT/music-pools/myfirstchannel \
  --count 10 \
  --prompts-file /srv/shorts-pipeline/prompts/myfirstchannel-music-prompts.txt
```

Requires `ELEVENLABS_API_KEY` in your `.env`. Each track costs ~$0.30 on ElevenLabs Music; 10 tracks per channel is a one-time ~$3 spend.

### Folder-watching delivery

The pipeline writes to a local folder. How that gets to a posting service is up to you.

- **Repurpose.io / Buffer / Hootsuite**: point the watcher at the local folder, or sync the folder to Drive/Dropbox and point the watcher there.
- **Manual**: review the folder once a day, upload what you like.
- **The author's setup**: scp from a Linux agent box to a Windows machine every 30 minutes via a Scheduled Task; Repurpose.io watches the Windows-side Drive folder.

A reference Windows pull script is in [deploy/windows-pull/](deploy/windows-pull/) (planned for v0.2).

### Failure modes & recovery

| Symptom | What to check |
| --- | --- |
| Channel produced 0 shorts | `forehead-source/` empty? `music-pools/<channel>/` empty? Qwen unreachable at `$OLLAMA_URL`? |
| All shorts skipped with "Overlay duplicate" | The hook table has saturated. Either expand `<channel>-topics.txt` or accept lower throughput. |
| "Forehead pool fully exhausted" | Every middle-60% window of every long take has been claimed (across all channels). Film more long takes. |
| Orphan `.mp4` in `output/<channel>/` without sibling `.txt` | Should never happen due to atomic-promote. If it does, see [The three delivery guarantees](#the-three-delivery-guarantees). |
| ffmpeg fails with "drawtext font not found" | drawtext needs a font file. Set `OVERLAY_FONT_PATH` in `.env` or install DejaVu Sans (`apt install fonts-dejavu`). |

## The three delivery guarantees

The single most important property of this pipeline is that **a posting service watching the output folder will never see a `.mp4` without its sibling `.txt`**. Repurpose.io will happily post a video with the raw filename as the caption if you let it. We don't let it.

Three layers, in order of where they fire:

1. **Atomic promote (in the orchestrator).** Render and sidecar write into `output_dir/.staging/`. Only once both files exist on disk does the code do two `Path.replace()` calls in a specific order: `.txt` first (which a watcher may not even notice), then `.mp4` (which the watcher does notice — but by the time it does, the `.txt` is already at its final name). `Path.replace()` is atomic on Linux and on Windows (NTFS) within the same filesystem.

2. **Channel-side glob doesn't see staging.** The hidden `.staging/` subdir doesn't match `*.mp4` so any external `scp <output_dir>/*.mp4` or fs-watcher pattern naturally ignores in-flight files.

3. **(If you sync to a remote folder) Source-side quarantine.** A reference rsync/scp wrapper (planned for v0.2) checks for orphan mp4s on the source side BEFORE shipping and moves them to a quarantine dir. If something somehow lands in `output_dir/` without a sibling, the wrapper catches it before it goes to the watcher.

Bug history: every one of these layers exists because of a real Repurpose.io incident where a bare-filename caption shipped publicly. The architecture is paranoid by design.

## Project layout

```
shorts-pipeline/
├── README.md                                # this file
├── LICENSE
├── pyproject.toml                           # installable; `shorts-pipeline` CLI
├── requirements.txt
├── .env.example
├── .gitignore
│
├── src/shorts_pipeline/
│   ├── __init__.py
│   ├── auto_shorts_channels.py              # ENTRY POINT (CLI: shorts-pipeline)
│   ├── config.py                            # ChannelConfig + YAML loader
│   ├── cut_picker.py                        # ffprobe + window enumeration
│   ├── history.py                           # per-channel + cross-channel SQLite
│   ├── qwen_client.py                       # Ollama HTTP, strict JSON
│   ├── render.py                            # ffmpeg cut + drawtext + music mix
│   └── sidecar.py                           # .txt writer + filename sanitization
│
├── examples/
│   ├── channels/example.yaml                # template channel config
│   └── prompts/
│       ├── example-voice.md                 # template Qwen system prompt
│       └── example-topics.txt               # template 20 topic seeds
│
├── deploy/
│   └── systemd/
│       ├── shorts-pipeline.service          # one-shot unit
│       └── shorts-pipeline.timer            # daily fire
│
├── docs/
│   ├── architecture.md                      # deeper than the README
│   ├── operations.md                        # ops cookbook
│   ├── adding-a-channel.md                  # step-by-step
│   └── extraction-status.md                 # what's in v0.1, what's coming
│
└── tests/                                   # unit tests (v0.2)
```

## Status & roadmap

This repo is v0.1: a clean, focused extraction of the **forehead-overlay daily pipeline** — the daily-cron heart of the system. Everything below is shippable today, and is what's running in production for the author.

What's deliberately not in v0.1, and where to find it on the upstream:

| Feature | Status | Upstream location |
| --- | --- | --- |
| Forehead-overlay daily pipeline | **✅ v0.1** | this repo |
| Strict-JSON Qwen overlay copy | **✅ v0.1** | this repo |
| Cross-channel cut dedup | **✅ v0.1** | this repo |
| Atomic-pair delivery | **✅ v0.1** | this repo |
| `generate_music_pool.py` (ElevenLabs Music wrapper) | v0.2 | `scripts/shorts_pipeline/generate_music_pool.py` |
| Windows pull / scp wrapper (folder-watcher source-side guard) | v0.2 | `scripts/shorts-pipeline-pull/` (lives outside the upstream repo) |
| Mascot / stop-motion path (Phase 2 — animated character per-frame compositor) | v0.3 | `scripts/shorts_pipeline/mascot_render.py`, `cartoonimator.py`, `lipsync.py`, `face_overlay.py`, `character_resolver.py`, `rig_*.py`, `postfx/` |
| Real-call shorts (Vapi consent gate + J/L cut padding) | v0.3 | `clients/_shared/shorts-pipeline/CALLS_PIPELINE.md`, `calls_align.py`, `calls_render.py` |
| Anchor/region tagger HTTP servers | future | `anchor_tagger_server.py`, `region_tagger_server.py` |

See [docs/extraction-status.md](docs/extraction-status.md) for a per-file accounting.

## Where this fits with other OSS video tools

This repo is intentionally narrow — it owns the **orchestration pattern** (multi-channel + LLM-driven copy + cross-channel dedup + atomic delivery), not generic video editing. For the surrounding work, point at existing tools at each seam:

### Upstream — preparing your `forehead-source/`

- **[Auto-Editor](https://github.com/WyattBlue/auto-editor)** — silence-strips long-take footage by volume threshold or via a paired transcript. Recommended pre-step for talking-head sources before they hit `forehead-source/`. `pip install auto-editor`, then:
  ```
  auto-editor my-long-take.mp4 --margin 0.2sec --output-file my-long-take.cleaned.mp4
  ```
  (Bonus: Auto-Editor uses ffmpeg under the hood, same as this pipeline — no new system deps.)

- **[OBS Studio](https://obsproject.com/)** — for capturing the long takes in the first place. No magic here, just the standard way to record clean 1080p+ talking-head.

### Sidestream — alternative input modes (call recordings, podcasts, etc.)

- **[WhisperX](https://github.com/m-bain/whisperX)** — Whisper + forced word-level alignment + speaker diarization in one CLI. Use it to slice a podcast or sales call into speaker-aligned segments that you then feed into this pipeline's render step. (Diarization requires a Hugging Face token + accepting pyannote's model terms.)

- **[pyannote-audio](https://github.com/pyannote/pyannote-audio)** — the underlying speaker diarization library if you want finer control than WhisperX gives.

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — CTranslate2-backed Whisper, much faster on GPU. Useful if you're transcribing your own forehead takes to use Auto-Editor's transcript mode.

### Downstream — branded promo / explainer videos (different shape, not this repo's job)

- **[Remotion](https://www.remotion.dev/)** — programmatic React video. The right tool when you need *deterministic, brand-controlled* video (sales explainers, feature trailers, ad variants) rather than overlay-on-footage. See the official templates: https://www.remotion.dev/templates
- **[Manim](https://www.manim.community/)** — for math/animation explainers à la 3Blue1Brown.
- **[p5.js](https://p5js.org/) / [Three.js](https://threejs.org/) / [D3](https://d3js.org/)** — for one-off animated explainers / illustrative scenes that you screen-record into an MP4.

### Posting — the watcher side

This pipeline writes `.mp4 + .txt` pairs into a folder. What watches that folder is on you:

- **[Repurpose.io](https://repurpose.io/)** — the watcher this pipeline was originally built against. Polls a Drive / Dropbox folder, posts to TikTok/Reels/Shorts.
- **[Buffer](https://buffer.com/) / [Hootsuite](https://hootsuite.com/)** — similar, with manual review queues.
- Manual: open the folder once a day and upload what you like.

## Origin

Originally built as `scripts/shorts_pipeline/` inside a larger marketing-knowledge-base monorepo for a single brand (KaiCalls — an AI phone secretary). When two more channels joined, the pipeline was rewritten to be config-driven; this extraction is that rewrite, with brand-specific data swapped for templates so other people can use it.

The production deployment runs three channels off one shared forehead-source pool — slicing one filming session into ~18 shorts a day across three separate brand identities.

Pull requests welcome. Open an issue first for anything bigger than a one-file change.


---

*Built and maintained by [Connor Gallic](https://pr.linkedin.com/in/cgallic) — connect on LinkedIn.*
