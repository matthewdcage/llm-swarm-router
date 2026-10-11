#!/usr/bin/env bash
# Poll Hub + aria2 for active downloads (read-only). Usage: ./monitor-active-download.sh [interval_s] [rounds]
set -euo pipefail
HUB_API="${HUB_API:-http://127.0.0.1:3020}"
INTERVAL="${1:-30}"
ROUNDS="${2:-0}"

poll_once() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
  curl -sf "${HUB_API}/api/jobs?limit=15" | python3 -c "
import json,sys
for j in json.load(sys.stdin).get('jobs',[]):
    if j.get('status') not in ('running','claimed','paused','queued'):
        continue
    p=j.get('progress') or {}
    print(f\"hub {j.get('id','')[:10]} {j.get('status')} phase={p.get('phase')} {p.get('completed')}/{p.get('total')} seed={p.get('seeders')} {(j.get('title') or '')[:50]}\")
"
  python3 <<'PY'
import json, urllib.request
RPC="http://127.0.0.1:6800/jsonrpc"
S="token:homelab-aria2"

def call(m, p):
    body = {"jsonrpc": "2.0", "id": "1", "method": m, "params": [S] + p}
    req = urllib.request.Request(RPC, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["result"]

for row in call("aria2.tellActive", []) or []:
    gid = row["gid"]
    st = call("aria2.tellStatus", [gid, ["status", "completedLength", "totalLength", "downloadSpeed", "numSeeders", "connections"]])
    print(f"aria2 {gid[:12]} {st.get('status')} {st.get('completedLength')}/{st.get('totalLength')} spd={st.get('downloadSpeed')} seed={st.get('numSeeders')}")
PY
}

if [[ "$ROUNDS" == "0" ]]; then
  while true; do
    echo "---"
    poll_once
    sleep "$INTERVAL"
  done
else
  for ((i=1; i<=ROUNDS; i++)); do
    echo "--- round $i/$ROUNDS ---"
    poll_once
    [[ "$i" -lt "$ROUNDS" ]] && sleep "$INTERVAL"
  done
fi
