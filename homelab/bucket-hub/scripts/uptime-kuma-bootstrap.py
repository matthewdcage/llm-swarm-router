#!/usr/bin/env python3
"""One-shot Uptime Kuma bootstrap: admin account + LAN service monitors."""
from __future__ import annotations

import json
import secrets
import string
from pathlib import Path

from uptime_kuma_api import MonitorType, UptimeKumaApi

KUMA_URL = "http://127.0.0.1:3001"
DATA_DIR = Path.home() / "homelab" / "scripts" / "uptime-kuma-setup"
CREDS_FILE = DATA_DIR / "admin-credentials.txt"

MONITORS: list[dict[str, object]] = [
    {"name": "Hub (Glance)", "url": "http://192.168.50.138:3000/"},
    {"name": "Hub form", "url": "http://192.168.50.138:3020/"},
    {"name": "AriaNg", "url": "http://192.168.50.65:8088/"},
    {"name": "MeTube", "url": "http://192.168.50.65:8089/"},
    {"name": "Jellyfin", "url": "http://192.168.50.65:8096/"},
    {"name": "Jackett", "url": "http://192.168.50.65:9117/"},
    {"name": "SearXNG", "url": "http://192.168.50.65:8888/"},
    {"name": "TNAS", "url": "http://192.168.50.186:8181/"},
    {"name": "netllm Ubuntu", "url": "http://192.168.50.65:11400/ui/"},
    {"name": "netllm Mac", "url": "http://192.168.50.19:11400/ui/"},
]


def _gen_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with UptimeKumaApi(KUMA_URL) as api:
        if api.need_setup():
            username = "bucket"
            password = _gen_password()
            result = api.setup(username, password)
            if not result.get("ok", True):
                raise SystemExit(f"setup failed: {result}")
            CREDS_FILE.write_text(f"username={username}\npassword={password}\n", encoding="utf-8")
            CREDS_FILE.chmod(0o600)
            api.login(username, password)
            print(json.dumps({"setup": "created", "creds_file": str(CREDS_FILE)}))
        else:
            lines = CREDS_FILE.read_text(encoding="utf-8").splitlines() if CREDS_FILE.exists() else []
            creds = dict(line.split("=", 1) for line in lines if "=" in line)
            if not creds.get("username") or not creds.get("password"):
                raise SystemExit("already setup but no creds file — log in via UI or reset kuma.db")
            api.login(creds["username"], creds["password"])
            print(json.dumps({"setup": "existing"}))

        existing = {m.get("name") for m in api.get_monitors()}
        added: list[str] = []
        skipped: list[str] = []
        for spec in MONITORS:
            name = str(spec["name"])
            if name in existing:
                skipped.append(name)
                continue
            res = api.add_monitor(
                type=MonitorType.HTTP,
                name=name,
                url=str(spec["url"]),
                interval=60,
                retryInterval=60,
                maxretries=2,
            )
            if not res.get("monitorId") and res.get("msg") != "Added Successfully.":
                raise SystemExit(f"add_monitor failed for {name}: {res}")
            added.append(name)

        print(json.dumps({"added": added, "skipped": skipped, "total": len(api.get_monitors())}))


if __name__ == "__main__":
    main()
