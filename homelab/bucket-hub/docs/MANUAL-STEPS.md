# Bucket-HUB — manual steps (you)

Procedures that need your Mac, Cloudflare dashboard, browser login, or password manager.
Agents link open items here from [`TODO.md`](../TODO.md); do not duplicate long procedures in TODO.

---

## DeepSeek Harness roles

| Surface | When | How |
|---------|------|-----|
| **Interactive chat** | Daytime | Hub `/chat` → LLM on/off → iframe → `chat.bucket-hub.stackrise.com.au` → **dsh web** on Ubuntu `:3080` |
| **Nightly automation** | 22:00 Brisbane timer | Pi `overnight-run.py` → SSH Ubuntu → **`dsh --profile headless`** via `overnight-dsh-task.sh` |

**Inference path (both):** `dsh` → netllm `:11400` → vLLM Qwen3.8-27B AWQ (GPU on-demand via `ai-server-llm.sh`).

Interactive chat must stay on a **subdomain root** (dsh breaks under `/chat` subpaths). Nightly uses headless CLI, not the web UI.

---

## 1. Cloudflare tunnel — Hub down (Error 1033)

**Symptom:** After Access login, **Cloudflare Tunnel error** / **Error 1033** on `bucket-hub.stackrise.com.au` (any path, including `/jellyfin`).

**Cause:** `cloudflared` on the Pi is not connected to Cloudflare, or the origin `http://127.0.0.1:3000` is not responding.

**Fix (SSH to Pi — Tailscale `bucket@100.85.42.0` or LAN `bucket@192.168.50.138`):**

```bash
# From repo root (Mac on Tailscale/LAN) — full E2E deploy:
./homelab/bucket-hub/scripts/deploy-torrent-jellyfin-e2e-home.sh

# Pi + tunnel only:
PI_HOST=bucket@100.85.42.0 ./homelab/bucket-hub/scripts/deploy-hub-jellyfin-proxy.sh
```

Or manually on the Pi:

```bash
curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/   # expect 200
docker ps --format '{{.Names}} {{.Status}}' | grep -E 'hub-gateway|glance'
docker restart hub-gateway glance
sudo systemctl restart cloudflared    # or: systemctl --user restart cloudflared
```

If `cloudflared` is a container, `docker restart <cloudflared-container>`. Confirm in Cloudflare Zero Trust → Tunnels that the connector shows **Healthy**.

**Remote workaround while tunnel is down:** Hub on Tailscale `http://100.85.42.0:3000` (no Cloudflare). Jellyfin direct: `http://100.112.168.40:8096` (ai-server awake).

---

## 2. Cloudflare tunnel — Chat subdomain

Add a public hostname (same Access allow-list as `bucket-hub.stackrise.com.au`):

| Field | Value |
|-------|--------|
| Hostname | `chat.bucket-hub.stackrise.com.au` |
| Service | `http://127.0.0.1:3080` on **Ubuntu** (`ai-server`), or tunnel route that reaches Ubuntu Tailscale/LAN `:3080` |

Without this, Hub `/chat` iframe fails remotely. Confirm Access email allow-list covers the same users.

---

## 3. Qwen3.8 weights pull + smoke

On Ubuntu (`ai-server`):

```bash
~/homelab/scripts/ai-server-llm.sh pull    # ~18 GB; watch ~/homelab/data/logs/qwen38-pull.log
~/homelab/scripts/ai-server-llm.sh start
~/homelab/scripts/ai-server-llm.sh status
```

Then on Hub: open **Chat** tab → toggle LLM on → confirm iframe loads and a short prompt works → toggle off (free GPU).

Headless smoke (LLM up):

```bash
bash ~/homelab/scripts/overnight-dsh-task.sh research 120 ~/homelab/data/logs/test-research.md
```

---

## 4. Deploy overnight planner v2 (Pi + Ubuntu)

From your Mac (LAN or Tailscale), with SSH keys that work for `bucket@…`:

```bash
SRC="/path/to/llm-swarm-router/.tmp-hub-ui"
PI=bucket@192.168.50.138          # or Tailscale Pi
UB=bucket@100.112.168.40          # ai-server Tailscale

scp "$SRC"/{app.py,overnight.html,hub-overnight.js,overnight-schedule.default.json} \
  "$PI:~/homelab/hub-form/"
scp "$SRC/scripts/overnight-run.py" "$PI:~/homelab/scripts/"
scp "$SRC/scripts/overnight-dsh-task.sh" "$UB:~/homelab/scripts/"
ssh "$UB" 'chmod +x ~/homelab/scripts/overnight-dsh-task.sh'
ssh "$PI" 'systemctl --user restart hub-form.service'
```

Hard-refresh `/overnight`, then **Save** once to migrate any old `duration_min` schedule.

---

## 5. MeTube YouTube cookies (re-export)

When Hub/MeTube yt-dlp fails with bot/sign-in through Proton:

1. On a browser with **Proton VPN on**, open youtube.com.
2. Export Netscape cookies for **Current Site** only (not ALL sites).
3. Copy to Ubuntu: `~/homelab/data/ytdlp/cookies.txt`
4. Restart MeTube; re-run a known-good URL smoke via Proton path.

Hub status chip only checks that the file exists — a stale file still shows “ok”.

---

## 6. Password manager — one-shot cred files

After first setup, save then delete server-side plaintext:

| Service | File (Ubuntu/Pi) |
|---------|------------------|
| Jackett admin | `~/homelab/data/jackett/admin-password.txt` |
| Uptime Kuma admin | under `~/homelab/scripts/uptime-kuma-setup/` / `admin-credentials.txt` |

---

## 6b. Jackett indexers for Hub torrent search

**Catalog vs search:** Glance **Indexers** and `torrent-indexers-catalog.json` list ~30 public Jackett IDs for tracking and mirror hints. **Torrent search only uses indexers you add and pass Test in Jackett on ai-server.** Hub torrent chips (**TV**, **Movies**, **Games**, **All**) also pick which configured indexers are queried based on each row’s `categories` tags (see [TORRENT-INDEXERS-CATALOG.md — Hub search routing](TORRENT-INDEXERS-CATALOG.md#hub-search-routing)); Torznab category filters still apply per chip. **Games** does not include `general` sites (1337x, TPB) — add games-tagged indexers in Jackett for game search.

See [TORRENT-INDEXERS-CATALOG.md](TORRENT-INDEXERS-CATALOG.md) for megathread links, `jackett_id` verification, and a category checklist after each catalog bump.

**Symptom:** Hub Search → **torrent** shows `jackett: no results`, only 1–2 dead rows, or only **The Pirate Bay** / **LimeTorrents** in tracker names.

**Cause:** Jackett has **too few working indexers**, indexers fail their test, or the query is too terse (`breaking bad s1` — Hub expands to `season 1` but **1337x / EZTV / TorrentGalaxy**-style indexers still need to be added in Jackett). Search uses Jackett on **ai-server** behind Gluetun (`hub-worker` → `http://127.0.0.1:9117`).

**Fix (on ai-server, LAN `http://192.168.50.65:9117` or via SSH tunnel):**

1. Jackett UI → **Indexers** → **Add indexer** → pick public indexers you trust (examples: 1337x, EZTV, Nyaa for anime).
2. For each indexer → **Test** → **Save** (failed tests will not return results).
3. Confirm API key matches worker env:
   ```bash
   grep JACKETT_API_KEY ~/homelab/scripts/hub-worker.env
   curl -s "http://127.0.0.1:9117/api/v2.0/indexers?apikey=YOUR_KEY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'indexers')"
   ```
   Expect **≥ 1** configured indexer.
4. Re-test from Hub: Search → **torrent** → TV chip → query `better call saul complete`.

**Deploy note:** `JACKETT_API_KEY` lives in `~/homelab/scripts/hub-worker.env` on Ubuntu (not in git). After changing Jackett admin password or API key, update env and `systemctl --user restart hub-worker.service`.

**Search while downloading:** `hub-worker` polls for torrent/GitHub search every few seconds even during long aria2 jobs (Office-sized downloads no longer block Jackett search). TV searches can still take up to ~2 minutes on the Pi relay timeout.

### 6b-ii. Indexer mirror URLs (Hub `/indexers` tab)

Track alternate site URLs (megathread mirrors, forum tips) **without** Hub writing to Jackett.

1. Glance → **Indexers** → **Import all hints** or per-row **Import hints** (copies catalog `reference_urls`, including multiple 1337x/EZTV mirrors) into your tracked list; or **Add mirror** / **bulk paste** URLs from the [Movies & TV](https://www.reddit.com/r/Piracy/wiki/megathread/movies_and_tv/), [Anime](https://www.reddit.com/r/Piracy/wiki/megathread/anime/), or [Games](https://www.reddit.com/r/Piracy/wiki/megathread/games/) megathreads.
2. Click **Check mirrors** (optionally include megathread hints without importing) — ai-server worker curls each URL via Gluetun. Status: reachable, matches Jackett Site Link, or unreachable.
3. Catalog `reference_urls` are maintainer hints (verify on the live wiki); Hub does not scrape Reddit. Each new catalog row must still be **added in Jackett** on ai-server (`jackett_id` must match Jackett’s definition name — run `homelab/bucket-hub/scripts/verify-jackett-catalog.sh` on ai-server after deploy).
4. When a mirror looks good on VPN, open Jackett on ai-server → indexer → update **Site Link** (must end with `/`) → **Test** → **Save**.
5. Click **Refresh scan** on the Indexers page to re-run Jackett search probes.

State files on the Pi (`JOBS_DIR`, typically `/data/hub-jobs/`): `torrent-indexers.candidates.json`, `torrent-indexers.candidates.status.json`.

---

## 7. netllm Mac (low priority)

`192.168.50.19:11400` refused when Mac asleep. Start netllm with LAN listen when you need Mac peer / Kuma green.

---

## 8. Deploy Hub UI usability pass

After agents update `.tmp-hub-ui/` Glance + form assets:

```bash
SRC="/path/to/llm-swarm-router/.tmp-hub-ui"
PI=bucket@192.168.50.138

scp "$SRC"/glance.yml "$PI:~/homelab/glance/"
scp "$SRC"/{glance-hub.css,hub-vpn.js,hub-requests.js,hub-embed.css} "$PI:~/homelab/glance/assets/" 2>/dev/null \
  || scp "$SRC"/{glance-hub.css,hub-vpn.js,hub-requests.js,hub-embed.css} "$PI:~/homelab/glance/"
scp "$SRC"/form.html "$PI:~/homelab/hub-form/"
scp "$SRC"/gen-status-pages.py "$PI:~/homelab/scripts/" 2>/dev/null || true
ssh "$PI" 'python3 ~/homelab/scripts/gen-status-pages.py; docker restart glance hub-gateway 2>/dev/null; systemctl --user restart hub-form.service'
```

Adjust asset paths to match the live Pi layout (`~/homelab/glance/` vs mounted `/app/assets`).

## 9. Torrent → Jellyfin one-time (ai-server)

After `scripts/deploy-media-stack-ai-server.sh` (or manual merge of `docker-compose-media.yml`):

1. **Jellyfin** `http://192.168.50.65:8096` → Dashboard → Libraries: Shows `/tv`, Movies `/movies` (optional read-only `/torrents` for staging).
2. **Sonarr** `http://192.168.50.65:8989` → root `/data/tv`, **Importing → Hardlink**, aria2 `127.0.0.1:6800`, remote path **`/mnt/torrents`** → local **`/data/torrents`**.
3. **Radarr** `http://192.168.50.65:7878` → root `/data/movies`, hardlink import, same aria2 client and remote path mapping.
4. **`~/homelab/scripts/hub-worker.env`** (not in git): `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `SONARR_*`, `RADARR_*` — then `systemctl --user restart hub-worker.service`.
5. **Pi Hub**: deploy `app.py` + `form.html` from `.tmp-hub-ui/` (section 7).

LAN smoke: `./homelab/bucket-hub/scripts/hub-torrent-jellyfin-smoke.sh`

### ai-server disk: shrink `/models` to 300G, grow `/` (LVM)

One NVMe (`nvme0n1`): **ubuntu--lv** → `/` (OS, Docker, Jellyfin transcode cache under `~/homelab/data/jellyfin/cache`), **models** LV → `/models` (LLM weights only). Default install over-allocated **~780G** to `/models`; streaming needs headroom on `/`.

From your Mac (copies script, you run with sudo on ai-server):

```bash
AI=bucket@192.168.50.65   # or Tailscale IP
scp homelab/bucket-hub/scripts/resize-ai-server-lvm-models-300g.sh "$AI:~/"
ssh -t "$AI" 'bash ~/resize-ai-server-lvm-models-300g.sh'
```

Expect **~5 min downtime** for `vllm-coder` while `/models` is unmounted. After: `/` ~**580G**, `/models` **300G** (still plenty for AWQ weights + HF cache). Optional reclaim on `/`: `docker image prune` (unused images).

### Hub VPN shows “stale” during a large torrent

`GET /api/status` marks VPN **not live** when the worker heartbeat is older than **25s** (`vpn_stale`), even if Gluetun is healthy. Long `wait_aria2` loops used to block heartbeats; worker now pings every **12s** while a download runs.

**Verify Gluetun (ai-server):**

```bash
docker ps --filter name=gluetun --format '{{.Status}}'
docker exec gluetun wget -qO- --timeout=8 https://api.ipify.org   # expect Proton egress IP
```

If Gluetun is up but the Hub badge is red, hard-refresh or wait for the next in-download heartbeat. Manual refresh:

```bash
# on ai-server — posts heartbeat with current VPN IP (needs HUB_WORKER_TOKEN in hub-worker.env)
systemctl --user restart hub-worker   # or wait for wait_aria2 heartbeat
```

Do **not** assume torrent egress stopped: aria2 uses `network_mode: service:gluetun`.

### Clear Active / Failed queue in the Hub UI

`POST /api/jobs/dismiss-queue` (no body) dismisses all jobs in `review`, `error`, or `queued`. Single job: `POST /api/jobs/{id}/dismiss` (same statuses). Dismissed jobs are hidden from default lists but kept on disk for audit.

```bash
curl -s -X POST http://127.0.0.1:3020/api/jobs/dismiss-queue
```

### Jellyfin tab infinite load (remote / Tailscale)

Glance must **not** iframe `http://192.168.50.65:8096` — the phone or laptop loads that URL directly and it hangs off-LAN. Use Caddy `/jellyfin` proxy + Glance `source: /jellyfin/`, then in Jellyfin **Dashboard → Networking → Base URL** set exactly `/jellyfin` (**no trailing space** — a space breaks redirects to `/jellyfin%20/web/` and 404). Restart Jellyfin after changing. Direct admin UI on LAN: http://192.168.50.65:8096/web/ (Base URL does not apply to direct port access the same way). Wake ai-server if the proxy times out.

Direct (Tailscale only, no Hub iframe): `http://100.112.168.40:8096`

**Smoke checklist** (post-deploy 2026-08-26)

- [x] Hub overview: Overnight + Reachability left; Processes/LAN on Dev
- [x] Download iframe: no jobs/last-night bleed; mount chips only (VPN in header)
- [x] Header VPN badge + Requests bell; Queue shows progress
- [x] Nav order: Hub → Storage → Chat → Overnight → Dev
- [ ] Off-LAN / Access: confirm page loads without hanging (LAN monitors only on Dev)
- [x] Chat / Overnight / Storage tabs open (200)
