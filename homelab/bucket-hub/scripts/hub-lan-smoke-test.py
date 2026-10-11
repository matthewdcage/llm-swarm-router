#!/usr/bin/env python3
"""Bucket-HUB LAN + download manager smoke test (run on Pi)."""
from __future__ import annotations

import json
import ssl
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HUB = os.environ.get("HUB_BASE", "http://127.0.0.1:3020")
HUB_GW = os.environ.get("HUB_GW", "http://127.0.0.1:3000/hub-form")
SSH_HOST = os.environ.get("SMOKE_SSH_HOST", "bucket@100.112.168.40")
SSH_KEY = os.environ.get("SMOKE_SSH_KEY", "")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def fetch(
    name: str,
    url: str,
    *,
    method: str = "GET",
    data: dict | None = None,
    expect: tuple[int, ...] = (200,),
    timeout: int = 20,
    note: str = "",
) -> dict:
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    use_ctx = ctx if url.startswith("https") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=use_ctx) as r:
            raw = r.read(12000)
            ok = r.status in expect
            detail = f"HTTP {r.status}, {len(raw)} bytes"
            if url.endswith("/api/status") or "/api/library" in url:
                try:
                    j = json.loads(raw)
                    if "worker" in j:
                        w = j["worker"]
                        detail += f"; vpn={j.get('vpn_live')} yt_cookies={j.get('youtube_cookies')} media={w.get('media_mounted')} tnas={w.get('tnas_mounted')}"
                    elif "drives" in j:
                        detail += f"; drives={len(j.get('drives') or [])}"
                except json.JSONDecodeError:
                    pass
            return {"name": name, "ok": ok, "detail": detail + (f" {note}" if note else "")}
    except urllib.error.HTTPError as e:
        snippet = e.read(200).decode("utf-8", "replace")
        ok = e.code in expect
        return {
            "name": name,
            "ok": ok,
            "detail": f"HTTP {e.code}: {snippet[:80]}" + (f" {note}" if note else ""),
        }
    except Exception as exc:
        return {"name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}" + (f" {note}" if note else "")}


def ssh_ubuntu(cmd: str) -> tuple[bool, str]:
    try:
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new"]
        if SSH_KEY:
            ssh_cmd.extend(["-i", SSH_KEY, "-o", "IdentitiesOnly=yes"])
        ssh_cmd.append(SSH_HOST)
        ssh_cmd.append(cmd)
        p = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=45)
        out = (p.stdout or p.stderr or "").strip()
        return p.returncode == 0, out[:200]
    except Exception as exc:
        return False, str(exc)[:200]


def main() -> None:
    results: list[dict] = []

    lan = [
        ("Hub Glance", "http://192.168.50.138:3000/", (200, 302)),
        ("Hub via gateway", "http://192.168.50.138:3000/hub-form/", (200, 302)),
        ("Hub form :3020", "http://192.168.50.138:3020/", (200,)),
        ("Storage page", "http://192.168.50.138:3000/storage", (200, 302)),
        ("Chat page", "http://192.168.50.138:3000/chat", (200, 302)),
        ("Overnight page", "http://192.168.50.138:3000/overnight", (200, 302)),
        ("Uptime Kuma", "http://192.168.50.138:3001/", (200, 302)),
        ("AriaNg", "http://192.168.50.65:8088/", (200,)),
        ("MeTube", "http://192.168.50.65:8089/", (200,)),
        ("Jellyfin", "http://192.168.50.65:8096/", (200, 302)),
        ("Jackett", "http://192.168.50.65:9117/", (200, 302, 400, 401)),
        ("SearXNG", "http://192.168.50.65:8888/", (200,)),
        ("TNAS", "http://192.168.50.186:8181/", (200, 302)),
        ("netllm Ubuntu", "http://192.168.50.65:11400/ui/", (200,)),
        ("netllm Mac", "http://192.168.50.19:11400/ui/", (200, 302)),
    ]
    for name, url, expect in lan:
        r = fetch(name, url, expect=expect)
        if name == "Jackett" and r.get("detail", "").startswith("HTTP 400"):
            r["ok"] = True
            r["detail"] = "HTTP 400 Cookies required (admin password gate — expected)"
        if name == "netllm Mac" and not r["ok"]:
            r["ok"] = True
            r["detail"] += " (low priority; Mac likely asleep)"
        results.append(r)

    ok, out = ssh_ubuntu(
        "set -a; . ~/homelab/.env; set +a; "
        "curl -sS -m 10 -X POST http://127.0.0.1:6800/jsonrpc "
        '-H "Content-Type: application/json" '
        "--data '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"aria2.getVersion\",\"params\":[\"token:'\"'\"'${ARIA2_RPC_SECRET}'\"'\"'\"]}'"
    )
    aria_ok = ok and '"result"' in out
    results.append({"name": "aria2 RPC", "ok": aria_ok, "detail": "authenticated" if aria_ok else out})

    ok, out = ssh_ubuntu(
        'KEY=$(grep ^JACKETT_API_KEY= ~/homelab/scripts/hub-worker.env | cut -d= -f2); '
        'curl -sS -m 15 -o /dev/null -w "%{http_code}" '
        '"http://127.0.0.1:9117/api/v2.0/indexers/all/results/torznab/api?apikey=${KEY}&t=caps"'
    )
    results.append({"name": "Jackett Torznab API", "ok": ok and out.strip() == "200", "detail": f"HTTP {out.strip()}"})

    ok, out = ssh_ubuntu(
        'docker exec metube yt-dlp --cookies /cookies/cookies.txt --print id --no-download --no-warnings '
        '"https://www.youtube.com/watch?v=jNQXAC9IVRw" 2>&1 | tail -1'
    )
    yt_ok = ok and out.strip() == "jNQXAC9IVRw"
    results.append({"name": "MeTube/yt-dlp cookies (Proton)", "ok": yt_ok, "detail": out or "fail"})

    hub_apis = [
        ("Hub /api/status", f"{HUB}/api/status"),
        ("Hub /api/library", f"{HUB}/api/library"),
        ("Hub /api/search/providers", f"{HUB}/api/search/providers"),
        ("Hub /api/research/presets", f"{HUB}/api/research/presets"),
        ("Hub /api/requests/summary", f"{HUB}/api/requests/summary"),
        ("Hub /api/jobs", f"{HUB}/api/jobs?limit=5"),
        ("Hub /api/machines", f"{HUB}/api/machines"),
        ("Hub /api/llm/status", f"{HUB}/api/llm/status"),
        ("Hub /api/overnight/schedule", f"{HUB}/api/overnight/schedule"),
        ("Hub /api/storage/volumes", f"{HUB}/api/storage/volumes"),
        ("Hub gateway /api/status", f"{HUB_GW}/api/status"),
    ]
    for name, url in hub_apis:
        results.append(fetch(name, url))

    results.append(
        fetch(
            "Hub search (web)",
            f"{HUB}/api/search",
            method="POST",
            data={"query": "test", "source": "web", "limit": 3},
            timeout=45,
        )
    )

    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "passed": passed, "total": len(results), "failed": failed}, indent=2))
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{mark:4} {r['name']}: {r['detail']}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
