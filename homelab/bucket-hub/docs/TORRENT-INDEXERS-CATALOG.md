# Torrent indexers catalog

The Hub maintains a **tracking catalog** of public Jackett indexers aligned with [r/Piracy megathreads](https://www.reddit.com/r/Piracy/wiki/index/). It does **not** enable search by itself.

| Concept | Meaning |
|---------|---------|
| **Catalog** | `torrent-indexers-catalog.json` on the Pi — labels, categories, megathread pointers, `reference_urls` for mirror hints |
| **Configured in Jackett** | Indexer added, tested, and saved on **ai-server** (`~/homelab/data/jackett/Jackett/Indexers/*.json`) |
| **Hub torrent search** | Uses only **configured + healthy** Jackett indexers behind Gluetun |

## Megathread sources

| Megathread | URL | Catalog field |
|------------|-----|---------------|
| Movies & TV | https://www.reddit.com/r/Piracy/wiki/megathread/movies_and_tv/ | `megathread_url` |
| Anime | https://www.reddit.com/r/Piracy/wiki/megathread/anime/ | `megathread_anime_url` |
| Games | https://www.reddit.com/r/Piracy/wiki/megathread/games/ | `megathread_games_url` |

Hub does not scrape Reddit. Maintainers copy mirror URLs from the live wiki into `reference_urls`, then operators **Import hints** and **Check mirrors** on VPN (see [MANUAL-STEPS.md §6b-ii](MANUAL-STEPS.md#6b-ii-indexer-mirror-urls-hub-indexers-tab)).

## Verifying `jackett_id`

Each catalog row `jackett_id` must match a Jackett definition stem (filename without `.yml`):

```bash
# On ai-server (Jackett container)
docker exec jackett ls /app/Jackett/Definitions/<jackett_id>.yml

# Or configured instances only (smaller set)
ls ~/homelab/data/jackett/Jackett/Indexers/*.json | xargs -n1 basename -s .json | sort
```

Helper script (repo):

```bash
./scripts/verify-jackett-catalog.sh
# Optional: JACKETT_DEFINITIONS_DIR=/path/to/Definitions ./scripts/verify-jackett-catalog.sh
```

On ai-server, defaults can point at the container:

```bash
JACKETT_DEFINITIONS_DIR=/var/lib/docker/volumes/...  # or docker exec path
```

## Quarterly refresh checklist

1. Open the three megathread URLs above; note domain changes for sites you use.
2. Bump `catalog_version` and `last_verified_note` in `torrent-indexers-catalog.json`.
3. Update `reference_urls` / `notes` for rotated mirrors (especially 1337x, EZTV, LimeTorrents).
4. Re-run `verify-jackett-catalog.sh` after Jackett image upgrades (new/removed definitions).
5. Deploy catalog to Pi; restart `hub-form`.
6. Glance **Indexers** → **Import all hints** → **Check mirrors** (VPN on).
7. On ai-server Jackett UI: enable high-value indexers from the checklist below; **Test** → **Save**.
8. **Refresh scan** on Indexers; confirm Hub torrent search shows more tracker names.

## Jackett enable checklist (by Hub category)

Start with indexers already working on your LAN, then add from megathread “GOAT” lists.

### Movies / TV / general

- thepiratebay, 1337x, eztv, yts, limetorrents, torrentgalaxyclone, therarbg
- torrentdownload, torrentdownloads, extratorrent-st, torrentproject2, magnetdownload, uindex
- kickasstorrents-ws, ilcorsaroblu, bitmagnet (confirm indexer-specific settings)
- internetarchive (archival), showrss (TV RSS)

### Anime (Hub TV chip includes anime indexers)

- nyaasi, tokyotosho, animetosho-xyz, acgrip, mikan, shanaproject, dmhy

### Games

- gamestorrents, skidrowrepack, plugintorrent, pctorrent, linuxtracker

**Excluded by policy:** explicit adult indexers (e.g. sukebei variants) unless you opt in locally.

## Hub search routing

Hub torrent chips send two filters to Jackett on ai-server:

| Chip | Indexers queried (`categories` in catalog) | Torznab `Category[]` (result types) |
|------|---------------------------------------------|-------------------------------------|
| **All** | Every configured Jackett indexer | none |
| **TV** | `tv`, `anime`, or `general` | TV Torznab IDs |
| **Movies** | `movies` or `general` | Movie Torznab IDs |
| **Games** | `games` only (not `general`) | Games Torznab IDs |

Anime indexers are included on the **TV** chip (no separate Anime chip). Indexers configured in Jackett but missing from the catalog are treated as `general` (Movies + TV only).

Worker reads the same catalog JSON as the Pi (`HUB_TORRENT_INDEXERS_CATALOG`, default `~/homelab/hub-form/torrent-indexers-catalog.json` on ai-server) and calls `/api/v2.0/indexers/{id}/results` per matching indexer instead of always using `all`.

## Catalog vs UI

- Overview widget: `configured_in_catalog / catalog_total` (e.g. `8/30`).
- `/indexers`: full table, mirror tracking, import/check workflow.
- Removing a row from the catalog does not remove Jackett configuration; extra Jackett indexers still appear as “not in catalog” in summaries.

## Related docs

- [MANUAL-STEPS.md §6b](MANUAL-STEPS.md#6b-jackett-indexers-for-hub-torrent-search) — fix empty torrent search
- [MANUAL-STEPS.md §6b-ii](MANUAL-STEPS.md#6b-ii-indexer-mirror-urls-hub-indexers-tab) — mirrors workflow
