# AP-1 test report

## 1. Scope

Covers audit findings **C1, C2, C3, H1, L1, L2, L3, M1** and **amendment D** (shared `index_writer.py`, torrent completion + Pi `done`/`error` fallback).

- C1/C2: TV torrents use a flat `torrents/` inbox; Hub no longer invents Show/Season folders.
- C3/M1: deploy manifest + `verify-deploy-parity.sh`.
- H1: HTML escaping (`escHtml` / `escAttr` / `safeHref` / `redactSecrets`).
- L1: `aria2_dir_for` inbox is `/mnt/torrents`, not `/downloads`.
- L2: `is_aria2_job` — worker first; kindless jobs are not torrents.
- L3: begin/resume/re-add share `aria2_opts_for_job`.
- D: every finished job appends `$JOBS_DIR/download-index.jsonl`.

## 2. Automated tests

| File | Test | Proves | Finding |
|------|------|--------|---------|
| `test_download_destinations.py` | `test_movies_usb_root_inbox` | Inbox maps to `ARIA2_TORRENTS_CONTAINER_DIR` | L1 |
| | `test_aria2_dir_for_job_flat_inbox` | Nested TV dests still `/mnt/torrents` | C1 |
| | `test_aria2_dir_for_job_media_hdd` | Media HDD unchanged | C1 |
| | `test_aria2_dir_for_job_tnas_staging` | TNAS staging path | C1 |
| | `test_aria2_dir_for_job_rejects_traversal` | `ValueError` on `../` | C1 |
| `test_aria2_dir_consistency.py` | `test_begin_resume_readd_same_dir` | Same `dir` | L3 |
| | `test_legacy_nested_job_uses_flat_dir` | Legacy Season dest | C2 |
| | `test_meta_retry_scans_release_folder` | Scan release folder | C1 |
| | `test_worker_does_not_mkdir_nested_inbox` | Only inbox mkdir | C1 |
| `test_job_kinds.py` | `test_no_kind_metube_worker_is_not_aria2` | Worker wins | L2 |
| | `test_no_kind_no_worker_is_not_aria2` | Warning + false | L2 |
| | `test_magnet_without_worker_is_aria2` | Magnet default | L2 |
| | `test_http_without_worker_is_aria2` | HTTP default | L2 |
| | `test_repo_worker_never_aria2` | Worker beats kind | L2 |
| | `test_future_repo_kinds_never_aria2` | Repo kinds | L2 |
| | `test_kindless_dispatch_never_aria2` | `dispatch_route` | L2 |
| `test_job_pause.py` | `test_pause_rejects_non_aria2` | API pause | L2 |
| `test_job_concurrency.py` | `test_claim_bucket_for_kindless_job` | Metube not an aria2 slot | L2 |
| `test_form_escaping.py` | `test_innerhtml_interpolations_escaped` | Static guard | H1 |
| | `test_jackett_key_redacted_for_display` | Display redaction | H1 |
| | `test_node_escape_helpers` | Node `escHtml`/`escAttr`/`safeHref` | H1 |
| `test_cleanup_empty_tv_folders.py` | dry-run / disk-only / library-only / refuse root | Cleanup contract | C2 |
| `test_deploy_manifest.py` | imports covered, paths exist, `bash -n` | Deploy parity | C3/M1 |
| `test_index_writer.py` | core fields, torrent entry, secrets, append, concurrent, fallback | Schema v1 | D |
| `test_index_on_job_done.py` | PATCH done/error/running | Index on finish | D |

## 3. Run log

Command (from `homelab/bucket-hub`):

```bash
PYTHONPATH=. uv run --with pytest pytest tests/ -q
```

Date: 2026-10-12.

| When | Result |
|------|--------|
| **Before** (audit baseline) | **54 passed, 2 failed** (`test_movies_usb_root_inbox` L1, `test_pause_rejects_non_aria2` L2) |
| **After** | **110 passed** in 2.08s (`uv run --with pytest`) |

## 4. Manual smoke (Finn)

| Step | Command / action | Result |
|------|------------------|--------|
| 1 | `cd homelab/bucket-hub && ./scripts/verify-deploy-parity.sh worker` **before** deploy. Record exit code and mismatches (answers whether the 122,279-byte worker was real). | |
| 2 | Rsync every `[pi]` path in `scripts/deploy-manifest.txt` to `~/homelab/hub-form/` on `stackrise-pi`; restart `hub-form`; bump Glance `?v=`. Rsync every `[worker]` path to `~/homelab/scripts/` on `ai-server-ts`; `systemctl --user restart hub-worker`. Then `./scripts/verify-deploy-parity.sh pi` and `… worker` (both exit 0). | |
| 3 | TV chip: search "Breaking Bad season 2", pick a season pack, **Fill** only. `library.json` must gain **no** new folder. | |
| 4 | Queue it. On ai-server: aria2 `dir=/mnt/torrents`. `ls /mnt/external/movies/torrents/` shows a release folder, no new `Breaking Bad/` folder. | |
| 5 | Pause then Resume; same `dir`, progress not reset to 0 %. | |
| 6 | Job card shows "Downloaded to torrents/…"; Sonarr files to `tv/Breaking Bad/Season 2`. | |
| 7 | Request note `<img src=x onerror=alert(1)>` renders as text; no alert. | |
| 8 | `python3 scripts/cleanup-empty-tv-folders.py --jobs-dir ~/homelab/data/hub-jobs` (Pi `--library-only`; ai-server `--disk-only`). Paste dry-run list; `--apply` only after review. | |
| 9 | `tail -n 1 ~/homelab/data/hub-jobs/download-index.jsonl \| jq .` — torrent entry with `release_name`, `download_dir`, `media_pipeline`, no API key. | |

## 5. Not tested and why

- Live aria2 / Jackett / Sonarr / SSH deploy (packet: Finn runs; tests must not touch network, Docker, SSH, or a real mount).
- `verify-deploy-parity.sh` against live hosts (needs Finn's SSH). `bash -n` only.
- Node was available here; `tests/js/hub-escape.test.mjs` ran via pytest.
