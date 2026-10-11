## Handoff to PM
**Packet:** AP-1
**Files changed:**
- `homelab/bucket-hub/job_kinds.py` — shared `is_aria2_job` / `dispatch_route` (worker first; kindless is not aria2)
- `homelab/bucket-hub/download_destinations.py` — `aria2_dir_for_job`, `aria2_opts_for_job`, `metadata_retry_scan_dir`, `host_dirs_to_ensure`
- `homelab/bucket-hub/index_writer.py` — schema v1, `secret_scan`, locked JSONL append, torrent + fallback builders
- `homelab/bucket-hub/app.py` — PATCH `download_dir`/`index_entry`; append index on first `done`/`error`; serve `hub-escape.js`; use `is_aria2_job`
- `homelab/bucket-hub/hub-worker.py` — flat inbox dirs/opts; `dispatch_route`; torrent completion PATCH with `index_entry`
- `homelab/bucket-hub/form.html` — TV dest is `torrents` only; Fill does not POST folders; escaping + download_dir copy
- `homelab/bucket-hub/static/hub-escape.js` — `escHtml`, `escAttr`, `safeHref`, `redactSecrets`
- `homelab/bucket-hub/scripts/deploy-manifest.txt` — Pi and worker file lists (includes `job_kinds.py` and `index_writer.py`)
- `homelab/bucket-hub/scripts/verify-deploy-parity.sh` — sha256 vs `stackrise-pi` / `ai-server-ts`
- `homelab/bucket-hub/scripts/cleanup-empty-tv-folders.py` — dry-run empty `Season *` dirs
- docs: `HUB-DOWNLOAD-DESTINATIONS.md`, `05-OPERATIONS-SSH-DEPLOY.md`, `08-TV-AND-MEDIA-PIPELINE.md`, `PROJECT-STATE.md`, `docs/testing/AP-1-test-report.md`
- tests: `conftest.py` plus all §3.1 files listed below

**Tests added:**
- `tests/test_download_destinations.py::test_movies_usb_root_inbox` — pass (L1: `/mnt/torrents`, not `/downloads`)
- `tests/test_download_destinations.py::test_aria2_dir_for_job_*` — pass
- `tests/test_aria2_dir_consistency.py::*` — pass
- `tests/test_job_kinds.py::*` — pass
- `tests/test_job_pause.py::test_pause_rejects_non_aria2` — pass
- `tests/test_job_concurrency.py::test_claim_bucket_for_kindless_job` — pass
- `tests/test_form_escaping.py::*` — pass (Node present)
- `tests/test_cleanup_empty_tv_folders.py::*` — pass
- `tests/test_deploy_manifest.py::*` — pass
- `tests/test_index_writer.py::*` — pass
- `tests/test_index_on_job_done.py::*` — pass
- Suite **before:** 54 passed, 2 failed. **After:** 110 passed in 2.08s (`PYTHONPATH=. uv run --with pytest pytest tests/ -q` on 2026-10-12)

**Decisions made:**
- `aria2_dir_for` still maps nested `torrents/Show/Season` to `/mnt/torrents/Show/Season` for the old helper; **all live begin/resume/re-add paths use `aria2_dir_for_job`** which is always `/mnt/torrents` under the inbox.
- Traversal out of the inbox raises `ValueError`.
- `secret_scan` ignores already-redacted `apikey=***` so stored index lines after redaction are valid.
- Index append on PATCH only when status newly becomes `done`/`error`. Missing `index_entry` uses `entry_from_job`. Error forces `status: failed`.
- Deploy/SSH not run from this session (packet: Finn runs).

**Open questions:**
- Confirm live worker byte size vs git via `verify-deploy-parity.sh worker` before first rsync.
- AP-2 not started; waiting for PM go.

```bash
# Finn deploy (after review)
cd homelab/bucket-hub
./scripts/verify-deploy-parity.sh worker   # record mismatches
# rsync every [pi] path in scripts/deploy-manifest.txt → stackrise-pi:~/homelab/hub-form/
# include static/hub-escape.js
ssh stackrise-pi 'sudo systemctl restart hub-form hub-gateway glance'
# bump glance.yml ?v=
# rsync every [worker] path → ai-server-ts:~/homelab/scripts/
ssh ai-server-ts 'systemctl --user restart hub-worker'
./scripts/verify-deploy-parity.sh pi
./scripts/verify-deploy-parity.sh worker
python3 scripts/cleanup-empty-tv-folders.py --jobs-dir ~/homelab/data/hub-jobs --library-only
# on ai-server, disk-only dry run, then --apply only after review
```
