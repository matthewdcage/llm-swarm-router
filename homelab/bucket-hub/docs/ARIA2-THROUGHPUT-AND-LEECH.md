# aria2 throughput tuning and leech-only policy

Operational guide for **ai-server** (`~/homelab/aria2/aria2.conf`, Gluetun + aria2 compose). Complements `HUB-DOWNLOAD-DESTINATIONS.md`.

## Apply changes (non-destructive)

1. Copy/sync `scripts/apply-aria2-hub-tuning.sh` to `~/homelab/scripts/`.
2. Run (creates a timestamped `.bak.hub-tuning.*` backup first):

```bash
chmod +x ~/homelab/scripts/apply-aria2-hub-tuning.sh
~/homelab/scripts/apply-aria2-hub-tuning.sh --recreate
```

`--recreate` restarts only the **aria2** container; the `/config` volume (session + partial data) is kept. Active torrents usually resume from session.

Dry review without restart:

```bash
~/homelab/scripts/apply-aria2-hub-tuning.sh
# then recreate when convenient
```

## Recommendations (2026-10-10 review)

| Finding | Action |
|---------|--------|
| Hub allows 16 concurrent torrents; aria2 had `max-concurrent-downloads=5` | Set to **16** (script) |
| Aggregate download can exceed **900 Mbps** when swarms are good | Set `HUB_ARIA2_TARGET_MBPS` in `hub-worker.env` to ~85–95% of real WAN |
| Expansion HDD is **exFAT** (`/mnt/external/movies`) | Keep `file-allocation=none`; expect USB to cap sustained writes before gigabit |
| Slow torrent (e.g. Office) with 1–2 seeders | Swarm-limited — not fixed by more slots |
| Worker `loop error: timed out` under many gids | aria2 RPC slow when busy → stale Hub heartbeat/progress; downloads still run |
| `seed-ratio=1.0` in aria2 | **Post-complete seeding** until 1:1 ratio — set **`seed-ratio=0.0`** for leech-only |

### Hub worker env (ai-server `~/homelab/scripts/hub-worker.env`)

| Variable | Suggested | Role |
|----------|-----------|------|
| `HUB_ARIA2_MAX_ACTIVE` | `16` | Hard cap on simultaneous Hub-tracked torrents |
| `HUB_ARIA2_TARGET_MBPS` | `450` (500 Mbps NBN) or `900` (gigabit) | Stop *starting* new torrents when aggregate speed nears cap |
| `HUB_ARIA2_BOOTSTRAP_SLOTS` | `4` | Allow slots while magnets connect |
| `HUB_MAX_CONCURRENT_ARIA2` | `16` on Pi | Claim guard — match worker max |

### Pi

After deploy, `GET /api/status` should show `max_concurrent_aria2: 16` and (when worker heartbeats) `aria2_target_mbps` / `aria2_total_download_bps`.

## Leech-only (no seeding)

**Goal:** download files for Sonarr/Jellyfin; **do not** upload after complete.

| Layer | Behaviour |
|-------|-----------|
| **aria2.conf** | `seed-ratio=0.0`, `seed-time=0`, `bt-seed-unverified=false`, `max-overall-upload-limit=128K` (small uplink during download only) |
| **Per-job RPC opts** | Hub worker sets `seed-ratio=0`, `seed-time=0` on every magnet/HTTP torrent |
| **On complete** | Worker calls `aria2.remove` (keeps files on disk, stops client seeding) after post-download hooks |

**During download:** BitTorrent still uploads a small amount to peers (handshakes, rare pieces). That is not “seeding the library”; it is protocol overhead. Capping upload at **128K** limits exposure while downloading.

**Files on disk** under `torrents/` remain for Sonarr/Radarr import; they are not offered to the swarm once aria2 drops the job.

### Verify (ai-server)

```bash
grep -E '^seed-ratio=|^max-concurrent-downloads=|^max-overall-upload-limit=' ~/homelab/aria2/aria2.conf
docker exec aria2 aria2c --conf-path=/config/aria2.conf -S 2>/dev/null | head -3 || true
# After a torrent completes, gid should disappear from tellActive:
python3 -c "..."  # see scripts/monitor-active-download.sh
```

Sonarr/Radarr do not re-seed through aria2 unless a completed download is left in aria2’s list with a positive seed ratio — the Hub worker removes it on done.

## Rollback

```bash
cp -a ~/homelab/aria2/aria2.conf.bak.hub-tuning.<stamp> ~/homelab/aria2/aria2.conf
docker compose up -d --force-recreate aria2
```

Restore `max-concurrent-downloads=5` and `seed-ratio=1.0` only if you intentionally want old behaviour.
