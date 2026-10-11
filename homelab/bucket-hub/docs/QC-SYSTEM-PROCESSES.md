# Hub system processes check

Run these checks from your Mac on the local network.

## 1. Pi services (192.168.50.138)

```bash
ssh bucket@192.168.50.138
systemctl --user status hub-form.service     # Hub backend on :3020
docker ps --format "table {{.Names}}\t{{.Status}}"  # Glance, Caddy, Uptime, WebDAV, Dozzle
systemctl --user list-timers                  # overnight-run.timer
```

Check:
- [ ] hub-form.service: active (running)
- [ ] hub-gateway (Caddy): Up
- [ ] glance: Up
- [ ] overnight-run.timer: enabled, next trigger shows tonight 22:00

## 2. Ubuntu worker (192.168.50.65)

```bash
ssh bucket@192.168.50.65
systemctl --user status hub-worker.service    # Download worker
systemctl --user list-timers                  # mount-watchdog.timer
docker ps --format "table {{.Names}}\t{{.Status}}"  # Gluetun, FlareSolverr, MeTube, etc.
```

Check:
- [ ] hub-worker.service: active (running)
- [ ] mount-watchdog.timer: enabled, fires every 60s
- [ ] gluetun: Up (healthy)
- [ ] flaresolverr: Up
- [ ] aria2: Up
- [ ] metube: Up

## 3. Job queue health

```bash
# From Mac
curl -s http://192.168.50.138:3020/api/status | python3 -m json.tool
```

Check:
- [ ] worker field present with fresh heartbeat (age < 30s during active work)
- [ ] vpn_ok: true
- [ ] jobs counts match expected (done/error/running/review)
- [ ] No stale_mounts reported

## 4. API endpoints

| Endpoint | Method | Expected |
|----------|--------|----------|
| `/api/status` | GET | Worker heartbeat, VPN, mounts, `max_concurrent_aria2` (claim cap, default 2) |
| `/api/jobs/claim` | POST | Worker claims next eligible job (respects `HUB_MAX_CONCURRENT_ARIA2`, `HUB_SLOT_VIDEO`, `HUB_SLOT_ARCHIVE` on Pi) |
| `/api/jobs` | GET | Job list with statuses |
| `/api/jobs/{id}/cancel` | POST | Cancel running/queued/paused job |
| `/api/jobs/{id}/pause` | POST | Pause running aria2 job (torrent/HTTP) |
| `/api/jobs/{id}/resume` | POST | Resume paused aria2 job (high-priority re-queue if another download is active) |
| `/api/jobs/{id}/retry` | POST | Retry error/cancelled job |
| `/api/jobs/queue/reorder` | POST | Reorder all `queued` jobs (`ordered_ids` = full list); sets `queue_rank` |
| `/api/devops/log` | GET | Worker warning log (may be empty) |
| `/api/overnight/last-night` | GET | Prior night run summary |
| `/api/overnight/schedule` | GET | Current overnight schedule |
| `/api/overnight/status` | GET | Current/last run state |
| `/api/drives` | GET | Detected drives and mount state |
| `/api/worker/aria2-destinations` | GET | Allowed torrent/HTTP download host prefixes + compose notes |
| `/api/llm/status` | GET | vLLM on/off/starting state |

All endpoints at `http://192.168.50.138:3020/api/...`

### Torrent / aria2 destination alignment

See **[HUB-DOWNLOAD-DESTINATIONS.md](HUB-DOWNLOAD-DESTINATIONS.md)** for the full contract.

- [ ] `GET /api/worker/aria2-destinations` lists `movies_usb` with `/mnt/external/movies`
- [ ] ai-server aria2 volume includes `/mnt/external/movies/torrents:/mnt/torrents` and `aria2.conf` `dir=/mnt/torrents`
- [ ] Queuing a magnet to an unsupported path returns **HTTP 400** (not a worker `error` job)
- [ ] Test magnet lands under `/mnt/external/movies/torrents/` when Movies drive is selected

## 5. Overnight automation

```bash
# Check timer is armed
ssh bucket@192.168.50.138 'systemctl --user status overnight-run.timer'
# Check schedule
curl -s http://192.168.50.138:3020/api/overnight/schedule | python3 -m json.tool
# Check last run
curl -s http://192.168.50.138:3020/api/overnight/last-night | python3 -m json.tool
```

Check:
- [ ] Timer triggers at 22:00 AEST
- [ ] Schedule has wake + mount-check + drain + sleep steps
- [ ] Last run data returns (even if failed/idle)

## 6. Auto-wake on high priority

Test: queue a job with "High priority" checked. If ai-server heartbeat is >2min stale, Wake-on-LAN should fire automatically.

```bash
# Check the auto-wake logic
curl -s http://192.168.50.138:3020/api/status | python3 -c "
import sys,json; d=json.load(sys.stdin)
age = d.get('heartbeat_age_s', 0)
print(f'Heartbeat age: {age:.0f}s')
print('Auto-wake would fire' if age > 120 else 'Worker is awake, auto-wake skipped')
"
```

## 7. Notification system

Status: **Telegram not configured** — `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are not in `.env`.

The code is ready (`notify.py` on Pi). To activate:
```bash
ssh bucket@192.168.50.138
echo 'TELEGRAM_BOT_TOKEN=your_token' >> ~/homelab/hub-form/.env
echo 'TELEGRAM_CHAT_ID=your_chat_id' >> ~/homelab/hub-form/.env
systemctl --user restart hub-form.service
```

Check:
- [ ] Decide: configure Telegram bot, or defer?
- [ ] If configured: queue a test job, verify notification on complete/error

## 8. Website archive (soyjakwiki)

Current crawl: `cbb807bb01ed` — FlareSolverr Chrome crawl via Gluetun VPN.

```bash
# Check FlareSolverr is actively processing
ssh bucket@192.168.50.65 'docker logs --tail 5 flaresolverr 2>&1'
# Check progress
curl -s http://192.168.50.138:3020/api/jobs | python3 -c "
import sys,json
jobs = json.load(sys.stdin)
jobs = jobs if isinstance(jobs, list) else jobs.get('jobs',[])
for j in jobs:
    if 'soyjak' in (j.get('title','') + j.get('url','')).lower() and j['status'] == 'running':
        p = j.get('progress',{})
        print(f'Pages: {p.get(\"pages\",\"?\")} | Queued: {p.get(\"queued\",\"?\")} | Visited: {p.get(\"visited\",\"?\")}')
"
```

Expected: pages count slowly increasing (~2/min). Each page goes through FlareSolverr Chrome + Gluetun VPN, so it's inherently slow.

## 9. Torrent → Jellyfin E2E

Automated LAN checks (no download unless you opt in):

```bash
chmod +x homelab/bucket-hub/scripts/hub-torrent-jellyfin-smoke.sh
./homelab/bucket-hub/scripts/hub-torrent-jellyfin-smoke.sh
# Optional tiny magnet (your URL, legal content only):
# SMOKE_MAGNET=1 SMOKE_MAGNET_URL='magnet:?xt=...' ./homelab/bucket-hub/scripts/hub-torrent-jellyfin-smoke.sh
```

Check:
- [ ] Expansion HDD mounted at `/mnt/external/movies` on ai-server
- [ ] aria2 `stat` on `/mnt/torrents` matches host exFAT device id; `docker exec -u p3terx aria2 touch /mnt/torrents/.qc_write_test`
- [ ] Jellyfin `:8096` health OK
- [ ] Hub `GET /api/worker/aria2-destinations` lists `movies_usb`
- [ ] Sonarr root `/data/tv`, Radarr `/data/movies`; aria2 client + remote path `/mnt/torrents` → host torrents bind
- [ ] Jellyfin libraries: **Shows** → `/tv`, **Movies** → `/movies` (not only `media-fallback`)
- [ ] Hub torrent queue merges search tags into `job.tags`; worker writes `.hub-job.json` under completed torrent folder
- [ ] After Sonarr/Radarr import + library scan, Jellyfin item **Tags** include Hub slugs (see `docs/MEDIA-JELLYFIN-PIPELINE.md`)

Known issues:
- Checkpoint file is lost on worker restart (saves to /tmp, should persist to data dir)
- Heartbeat goes stale during long synchronous crawl sessions (cosmetic — crawl continues)
- URL queue grows large (junk filter catches most, but ~6k URLs still pass through)
