# Extraction status — what's in v0.1, what's coming

This repo is a clean extraction of a working pipeline. v0.1 ships the daily-cron heart; later phases bring in the mascot animation, real-call shorts, and tooling that the upstream uses but that's not strictly required to run the pipeline.

## v0.1 — included ✅

Everything below is in this repo and runnable today.

| Upstream file | This repo | Sanitization |
| --- | --- | --- |
| `scripts/shorts_pipeline/auto_shorts_channels.py` | `src/shorts_pipeline/auto_shorts_channels.py` | Hardcoded `CHANNELS` dict → auto-discovery of `*.yaml` in `SHORTS_CHANNELS_DIR`. Hardcoded `/srv/shorts-pipeline/...` paths → env-var-driven. Inline `brand_guard` import → optional `SHORTS_CAPTION_VALIDATOR` hook. Mascot loop guarded behind `format_mix.mascot > 0` (always 0 in v0.1 examples). |
| `scripts/shorts_pipeline/config.py` | `src/shorts_pipeline/config.py` | Verbatim. |
| `scripts/shorts_pipeline/cut_picker.py` | `src/shorts_pipeline/cut_picker.py` | Verbatim. |
| `scripts/shorts_pipeline/history.py` | `src/shorts_pipeline/history.py` | Verbatim. (Mascot table + columns still present so a future v0.3 mascot port works against the same DB.) |
| `scripts/shorts_pipeline/qwen_client.py` | `src/shorts_pipeline/qwen_client.py` | Default `ollama_url` reads `OLLAMA_URL` env (was hardcoded `http://agent:11434`). Default `model` reads `OLLAMA_MODEL` env. Mascot prompt functions kept (so v0.3 just needs to wire the renderer back). |
| `scripts/shorts_pipeline/render.py` | `src/shorts_pipeline/render.py` | `from ingester.shorts import make_short` dependency removed; replaced with a self-contained `drawtext_overlay` using `ffmpeg drawtext` directly. Font path configurable via `OVERLAY_FONT_PATH`. |
| `scripts/shorts_pipeline/sidecar.py` | `src/shorts_pipeline/sidecar.py` | Verbatim. |
| `scripts/shorts_pipeline/systemd/*` | `deploy/systemd/*` | Working dir and `EnvironmentFile` paths parameterized; comments added. |
| `clients/_shared/shorts-pipeline/channels/<brand>.yaml` | `examples/channels/example.yaml` | Brand-specific data swapped for generic placeholders. |
| `clients/_shared/shorts-pipeline/prompts/<brand>-voice.md` | `examples/prompts/example-voice.md` | Brand voice swapped for generic template structure. |
| `clients/_shared/shorts-pipeline/prompts/<brand>-topics.txt` | `examples/prompts/example-topics.txt` | Brand-specific seeds swapped for generic placeholders. |

## v0.2 — planned

Smaller additions that round out the production setup.

| File / feature | Upstream | Notes |
| --- | --- | --- |
| `generate_music_pool.py` | `scripts/shorts_pipeline/generate_music_pool.py` | Wraps the ElevenLabs Music API. Needs `ELEVENLABS_API_KEY`. Vanilla one-shot script, easy to drop in. |
| Windows pull script | external — `scout-media-dev/scripts/shorts-pipeline-pull/pull-from-agent.ps1` | scp from agent → Drive every 30 min, with source-side orphan quarantine. Reference impl; users with different sync setups (rclone, Syncthing, just-leave-it-local) won't need it. |
| Tests | `scripts/shorts_pipeline/tests/` | 13 unit tests in upstream covering config/history/cut_picker/sidecar/qwen_client. Need path-fixtures to run outside the upstream layout. |
| `pyproject.toml` extras (`[project.optional-dependencies]`) | new | `[dev]` for pytest, `[music]` for ElevenLabs SDK, `[mascot]` for v0.3. |

## v0.3 — mascot / stop-motion path

The animated-character render path. Significantly more code (~25 modules) and more dependencies (Rhubarb lipsync, PIL, numpy, optional ComfyUI). Held back so v0.1 stays small.

| Upstream file | Purpose |
| --- | --- |
| `mascot_render.py` | Stop-motion compositor: TTS → Rhubarb visemes → 4-mouth-shape frames → music mix |
| `cartoonimator.py` | Frame-sequence + shot-timeline assembler (opening pose, talking segment, mid pose, blink) |
| `voice.py` | ElevenLabs TTS wrapper, per-character voice IDs |
| `lipsync.py` | Rhubarb Preston Blair → 4-mouth-shape timeline + smoothing |
| `face_overlay.py` | Load mouth/eye anchors from character JSON, render onto base PNG |
| `character_resolver.py` | Resolve slug → mascot library path |
| `composite.py` | PIL/numpy compositor for mascot + background + overlay |
| `build_rig_poses.py`, `library_build.py` | Build pose libraries from layered parts |
| `rig_animate.py`, `rig_segment.py`, `rig_split.py`, `rig_layer_builder.py`, `rig_color_segment.py`, `rig_underlap_painter.py` | The character rig system (IK/FK pose interp, region segmentation, layer assembly, joint underlap fill) |
| `postfx/harness.py`, `postfx/frame_timing.py`, `postfx/color_matcher.py`, `postfx/line_boil.py`, `postfx/video_io.py` | Cel-look effects: variable frame holds, palette quantization, procedural line boil |
| `gpt_image_client.py` | GPT-4V image gen wrapper for base mascot PNGs |
| `anchor_tagger_server.py`, `region_tagger_server.py` | Interactive HTTP tools for marking up mouth/eye anchors and limb regions |
| `clients/_shared/shorts-pipeline/mascots/<character>/` | Example mascot character library (poses, parts, colormaps, anchors) |

If you want this path now, copy those files into `src/shorts_pipeline/`, install Rhubarb (`apt install rhubarb-lip-sync` or build from source), add `pillow numpy elevenlabs` to deps, and set `format_mix:` in your channel YAML to allocate a mascot slot.

## Deferred — not planned for extraction

- **Real-call shorts** (`CALLS_PIPELINE.md`, `calls_align.py`, `calls_render.py`) — clips from consented Vapi calls with J/L padding rules. Highly specific to a Vapi+demo-line workflow; lives better as a separate repo.
- **Legacy Wan-i2v path** (`mascot_render_wan.py`, `wan_client.py`) — rejected as "too AI-uncanny" in production; kept in the upstream for reference only.
- **Spike / demo files** (`spike_one_scene.py`, `rig_animate_demo.py`, `rig_talking_demo.py`) — not in the daily cron, kept upstream as scratch.

## How upstream patches flow into this repo

Until v0.2 wraps, treat this as a one-way mirror: upstream is the source of truth for the maintainer, this is the public-facing extraction. After v0.2, the plan is to flip it — develop in this repo, vendor back into upstream only if a feature stays upstream-specific.
