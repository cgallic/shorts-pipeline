# Adding a channel

Three files, no code changes.

## 1. Channel YAML

`$SHORTS_CHANNELS_DIR/myfirstchannel.yaml`:

```yaml
channel: myfirstchannel
brand: My First Channel
output_dir: /srv/shorts-pipeline/output/myfirstchannel
forehead_pool: /srv/shorts-pipeline/forehead-source
music_pool: /srv/shorts-pipeline/music-pools/myfirstchannel
history_db: /srv/shorts-pipeline/history/myfirstchannel.db
shorts_per_day: 6
voice_prompt_file: /srv/shorts-pipeline/prompts/myfirstchannel-voice.md
topic_universe_file: /srv/shorts-pipeline/prompts/myfirstchannel-topics.txt
forbidden_phrases:
  - "leverage"
  - "solution"
cta_pool:
  - 'comment "demo" for the walkthrough'
hashtag_set:
  - "#mybrand"
stats_to_cite:
  - "3x lift in conversion last quarter"
overlay_style: dense
clip_duration_s: 6
```

Start by copying `examples/channels/example.yaml`.

## 2. Voice prompt

`$SHORTS_PIPELINE_ROOT/prompts/myfirstchannel-voice.md`:

This is the system prompt for Qwen. Spend real time on this — it's the single biggest knob on output quality. Cover:

- Who the brand is, in one paragraph
- The target persona (one specific reader, not a segment)
- The voice rules (POV, register, sentence length, what they would and wouldn't say)
- 2-3 few-shot examples of overlays that nail the voice
- A "what bad output looks like" section calling out the AI tells you've actually seen

Start by copying `examples/prompts/example-voice.md`.

## 3. Topic universe

`$SHORTS_PIPELINE_ROOT/prompts/myfirstchannel-topics.txt`:

20+ topic seeds, one per line. Each is a *scenario*, not a *headline*. Examples that work:

```
The moment a customer realized our competitor's "lowest price guarantee" excluded their entire product line
What happens when a 4-person sales ops team is replaced by one config file
The 11pm phone call that turned a stalled deal into a contract by morning
```

Examples that don't work:
- "Why our product is great" (too abstract)
- "Top 5 ways to..." (Qwen will write a listicle every time)

Start by copying `examples/prompts/example-topics.txt`.

## 4. Music pool

Drop 5-10 instrumental .mp3 tracks into `$SHORTS_PIPELINE_ROOT/music-pools/myfirstchannel/`. Each track should be ≥30s; the pipeline loops shorter tracks to fill the 6-second clip.

The orchestrator picks one at random per render. If you want a specific track for a specific channel, that's just "only put one track in the pool."

For brand-aligned music with no licensing headaches, use ElevenLabs Music (≈$0.30/track). A wrapper is planned for v0.2.

## 5. Forehead source

Drop long-take .mp4 footage into `$SHORTS_PIPELINE_ROOT/forehead-source/`. This is **shared across channels** — one filming session feeds three brands. The cross-channel registry guarantees no window goes to two channels.

How much do you need? Each long take produces `(duration * 0.6) / clip_duration_s` windows. A 10-minute take with `clip_duration_s: 6` → `(600 * 0.6) / 6 = 60` windows. Three channels at 6/day = 18/day = ~3 days of inventory per 10-minute take. Refill weekly.

## 6. Smoke test

```bash
# Dry run — writes to dry-run/, doesn't touch real output_dir, doesn't write history
shorts-pipeline --channel myfirstchannel --dry-run --count 1

# Inspect dry-run output
ls -la /srv/shorts-pipeline/dry-run/myfirstchannel/
# Should see <stem>.mp4 and <stem>.txt — open the mp4, read the txt
```

If the dry run looks right, run it for real:

```bash
shorts-pipeline --channel myfirstchannel --count 1
ls -la /srv/shorts-pipeline/output/myfirstchannel/
```

Then let the daily timer take over.

## Dual-ICP channels (marketplaces, two-sided products)

Replace `topic_universe_file:` with:

```yaml
topic_universe:
  buyer:  /srv/shorts-pipeline/prompts/mymarketplace-buyer-topics.txt
  seller: /srv/shorts-pipeline/prompts/mymarketplace-seller-topics.txt
icp_split:
  buyer: 3
  seller: 3   # must sum to shorts_per_day
```

The voice prompt is shared across both ICPs. If you want truly different voices per ICP, define two separate channels.
