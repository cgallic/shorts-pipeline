# Operations cookbook

## Bootstrapping a new host

```bash
# System deps
sudo apt install -y ffmpeg fonts-dejavu python3-venv

# Pull the repo (anywhere — repo location ≠ runtime location)
sudo mkdir -p /srv/shorts-pipeline && sudo chown $USER /srv/shorts-pipeline
cd /srv/shorts-pipeline
git clone https://github.com/cgallic/shorts-pipeline.git repo
cd repo
python3 -m venv ../.venv
../.venv/bin/pip install -e .

# Runtime state tree
cd /srv/shorts-pipeline
mkdir -p channels prompts forehead-source music-pools history logs output dry-run

# Configure
cp repo/.env.example /srv/shorts-pipeline/.env
# edit .env — set SHORTS_PIPELINE_ROOT=/srv/shorts-pipeline, OLLAMA_URL, etc.
```

## Adding a channel

See [adding-a-channel.md](adding-a-channel.md).

## Running manually

```bash
# One channel, dry run (writes to /srv/shorts-pipeline/dry-run/<channel>/)
shorts-pipeline --channel myfirstchannel --dry-run

# One channel, real
shorts-pipeline --channel myfirstchannel

# All channels
shorts-pipeline

# Limit count for testing
shorts-pipeline --channel myfirstchannel --count 1
```

## systemd

```bash
# Install the units (one-time)
sudo cp deploy/systemd/shorts-pipeline.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shorts-pipeline.timer

# Verify
systemctl list-timers shorts-pipeline.timer
systemctl status shorts-pipeline.service

# Tail logs
journalctl -u shorts-pipeline.service -f
tail -f /srv/shorts-pipeline/logs/shorts-pipeline.log

# Force a run now (timer-style — triggers the service immediately)
sudo systemctl start shorts-pipeline.service

# Pause everything
sudo systemctl stop shorts-pipeline.timer

# Pause one channel
mv /srv/shorts-pipeline/channels/myfirstchannel.yaml{,.paused}
```

## Recovery

### "Channel produced 0 shorts" in the status file

Check the run log: `journalctl -u shorts-pipeline.service -n 200` or `tail -200 /srv/shorts-pipeline/logs/shorts-pipeline.log`.

| Log line | Cause | Fix |
| --- | --- | --- |
| `No .mp4 takes found in <forehead_pool>` | Empty pool | Drop a long take into `forehead-source/` |
| `Forehead pool fully exhausted` | Every window has been used | Film more, or shrink cooldown windows |
| `No music tracks in <music_pool>` | Empty per-channel music pool | Drop ≥1 .mp3 in `music-pools/<channel>/` |
| `requests.exceptions.ConnectionError` to OLLAMA_URL | Ollama unreachable | `curl $OLLAMA_URL/api/version`; restart the model server |
| `QwenError: Invalid JSON from Qwen` | Model returned malformed JSON despite `format: "json"` | Try a different model, increase `num_predict`, narrow the prompt |
| `Overlay duplicate` (info) | History hook table saturated | Expand topics file, or accept lower throughput |
| `brand-guard BLOCK` (if validator wired) | Caption hit a banned-phrase rule | Inspect `.staging/.lint-rejects/`, adjust the validator or topic |

### Orphan `.mp4` without sibling `.txt` in output

This shouldn't happen due to atomic-promote. If it does, something bypassed the pipeline (manual file drop, restored backup, etc.). Either:

- Delete the orphan: `rm output/<channel>/<orphan-stem>.mp4`
- Or write the sibling .txt by hand if you know what the caption should be.

A reference rsync wrapper that quarantines orphans before shipping is planned for v0.2.

### Reset a channel's history

```bash
# Nuclear option — channel will be allowed to re-use everything
mv /srv/shorts-pipeline/history/<channel>.db{,.backup-$(date +%F)}
```

### Reset the cross-channel registry

```bash
# Only do this if you're certain — every channel can claim every window again
mv /srv/shorts-pipeline/history/cuts-registry.db{,.backup-$(date +%F)}
```

## Health monitoring

The pipeline writes `/srv/shorts-pipeline/logs/shorts-pipeline-status.txt` at the end of every run:

```
2026-05-22T09:01:14.512347 success=15 target=18
```

A simple watch script:

```bash
#!/usr/bin/env bash
STATUS=$(cat /srv/shorts-pipeline/logs/shorts-pipeline-status.txt 2>/dev/null)
SUCCESS=$(echo "$STATUS" | sed -n 's/.*success=\([0-9]*\).*/\1/p')
TARGET=$(echo "$STATUS"  | sed -n 's/.*target=\([0-9]*\).*/\1/p')

if [ -z "$SUCCESS" ] || [ "$SUCCESS" -lt "$TARGET" ]; then
    # alert — pipeline missed its target
    echo "shorts-pipeline degraded: $SUCCESS/$TARGET" | mail -s "shorts-pipeline" you@example.com
fi
```
