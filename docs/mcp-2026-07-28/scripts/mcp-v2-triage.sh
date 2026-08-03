#!/usr/bin/env bash
# mcp-v2-triage.sh
#
# Classify a repository against the MCP 2026-07-28 specification and the v2 SDKs,
# and print an impact row plus the runbook that applies.
#
# This exists because a remote agent session cannot always read every repo in an
# org. Run this locally across your checkouts to produce the same inventory a
# reviewer would produce by hand.
#
# Usage:
#   ./mcp-v2-triage.sh                      # triage the current directory
#   ./mcp-v2-triage.sh /path/to/repo        # triage one repo
#   ./mcp-v2-triage.sh --all ~/code         # triage every git repo under a root
#   ./mcp-v2-triage.sh --all ~/code --tsv   # machine-readable output
#
# Requires: ripgrep (rg). Falls back to grep -r if rg is absent.
#
# Exit codes: 0 = triaged, 2 = bad usage, 3 = missing dependency.

set -uo pipefail

TSV=0
ALL=0
ROOT=""
TARGETS=()

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --all)  ALL=1; ROOT="${2:-}"; [ -n "$ROOT" ] || { echo "--all needs a directory" >&2; exit 2; }; shift 2 ;;
    --tsv)  TSV=1; shift ;;
    -h|--help) usage 0 ;;
    -*)     echo "unknown flag: $1" >&2; exit 2 ;;
    *)      TARGETS+=("$1"); shift ;;
  esac
done

if command -v rg >/dev/null 2>&1; then
  SEARCH() { rg --no-messages -l "$1" "$2" 2>/dev/null; }
  SEARCHC() { rg --no-messages -c "$1" "$2" 2>/dev/null | awk -F: '{s+=$NF} END{print s+0}'; }
else
  echo "note: ripgrep not found, falling back to grep (slower)" >&2
  SEARCH() { grep -rlE "$1" "$2" 2>/dev/null; }
  SEARCHC() { grep -rhoE "$1" "$2" 2>/dev/null | wc -l | tr -d ' '; }
fi

# Directories that produce false positives (vendored SDK copies, caches, backups).
PRUNE='node_modules|\.venv|venv|\.git|dist|build|__pycache__|site-packages|\.next|vendor'

count() { # pattern dir -> integer, excluding vendored paths
  local n
  n=$(SEARCHC "$1" "$2")
  echo "${n:-0}"
}

hits() { # pattern dir -> newline list of files, vendored paths pruned
  SEARCH "$1" "$2" | grep -Ev "/($PRUNE)/" || true
}

triage_one() {
  local dir="$1"
  local name; name="$(basename "$dir")"

  # ---------- SDK detection -------------------------------------------------
  local py_pin="" ts_pin="" lang="none" sdk="none"

  # Python pin: mcp / fastmcp in pyproject.toml, requirements*.txt, setup.py
  py_pin=$(grep -rhoE '(^|[^a-z-])(mcp|fastmcp)[[:space:]]*(\[[a-z,]+\])?[[:space:]]*(==|>=|~=|>|<)[[:space:]]*"?[0-9][0-9a-z.*]*' \
            --include='pyproject.toml' --include='requirements*.txt' --include='setup.py' --include='setup.cfg' \
            "$dir" 2>/dev/null | grep -Ev "/($PRUNE)/" | head -3 | tr -d '"' | sed 's/^[^a-z]*//' | paste -sd';' -)

  # TypeScript pin: any @modelcontextprotocol/* dependency in package.json
  ts_pin=$(grep -rhoE '"@modelcontextprotocol/[a-z-]+"[[:space:]]*:[[:space:]]*"[^"]+"' \
            --include='package.json' "$dir" 2>/dev/null | grep -Ev "/($PRUNE)/" \
            | sed 's/"//g; s/[[:space:]]//g' | sort -u | head -3 | paste -sd';' -)

  [ -n "$py_pin" ] && { lang="python"; sdk="$py_pin"; }
  if [ -n "$ts_pin" ]; then
    if [ "$lang" = "python" ]; then lang="python+ts"; sdk="$sdk | $ts_pin"; else lang="typescript"; sdk="$ts_pin"; fi
  fi

  # Not an MCP repo at all: skip quietly.
  if [ "$lang" = "none" ]; then
    local anymcp; anymcp=$(hits 'from mcp|import mcp|@modelcontextprotocol' "$dir" | head -1)
    [ -z "$anymcp" ] && return 1
    lang="unpinned"; sdk="(code imports MCP but no manifest pin found)"
  fi

  # ---------- breaking-change signals --------------------------------------
  local n_init n_session n_fastmcp n_toolcall n_sse n_sampling n_roots n_logging n_stdio n_http n_zod
  n_init=$(count 'await [a-zA-Z_.]*\.initialize\(\)|\.initialize\(\)' "$dir")
  n_session=$(count 'Mcp-Session-Id|mcp[-_]session[-_]id|sessionId' "$dir")
  n_fastmcp=$(count 'FastMCP|fastmcp' "$dir")
  n_toolcall=$(count '@mcp\.tool|\.tool\(|registerTool|@server\.(list_tools|call_tool)' "$dir")
  n_sse=$(count 'SSEServerTransport|sse_app|SseServerTransport|/sse' "$dir")
  n_sampling=$(count 'createMessage|sampling/createMessage|create_message' "$dir")
  n_roots=$(count 'listRoots|roots/list|list_roots' "$dir")
  n_logging=$(count 'notifications/message|send_log_message|loggingCapability|setLevel' "$dir")
  n_stdio=$(count 'stdio_server|StdioServerTransport|stdio_client|StdioServerParameters' "$dir")
  n_http=$(count 'streamablehttp|StreamableHTTP|streamable_http' "$dir")
  n_zod=$(count 'from .zod.|require\(.zod.\)' "$dir")

  # ---------- archetype -----------------------------------------------------
  local archetype runbook
  local is_client is_server
  is_client=$(count 'ClientSession|from mcp.client|@modelcontextprotocol/client' "$dir")
  is_server=$(count 'FastMCP|MCPServer|@modelcontextprotocol/server|mcp\.server' "$dir")

  if [ "$is_client" -gt 0 ] && [ "$is_server" -gt 0 ]; then
    archetype="gateway/proxy (server AND client)"; runbook="04-code-execution-sampling-and-skill-factories.md (gateway section)"
  elif [ "$n_sampling" -gt 0 ] || [ "$n_roots" -gt 0 ]; then
    archetype="sampling/roots dependent"; runbook="04-code-execution-sampling-and-skill-factories.md (part b)"
  elif [ "$(count 'registerResource|@mcp\.resource|resources/list|list_resources' "$dir")" -gt 0 ]; then
    archetype="resources/prompts bearing"; runbook="03-resources-prompts-and-apps.md"
  elif [ "$n_http" -gt 0 ] || [ "$n_sse" -gt 0 ] || [ "$n_session" -gt 0 ]; then
    archetype="remote HTTP server"; runbook="02-remote-http-oauth.md"
  elif [ "$n_stdio" -gt 0 ] || [ "$n_toolcall" -gt 0 ]; then
    archetype="tools-only stdio"; runbook="01-tools-only-stdio.md"
  else
    archetype="mcp consumer/config only"; runbook="GUIDELINES.md"
  fi

  # ---------- risk ----------------------------------------------------------
  # Unbounded lower-bound pins are the single most dangerous state: a fresh
  # install silently resolves to 2.0.0 and the server stops working.
  local unbounded=0
  echo "$py_pin" | grep -qE '(mcp|fastmcp)[[:space:]]*>=' && unbounded=1
  echo "$ts_pin" | grep -qE ':\^?[0-9]' && echo "$ts_pin" | grep -qE ':\^' && unbounded=$((unbounded))

  local risk=0
  [ "$unbounded" -eq 1 ]     && risk=$((risk+4))
  [ "$n_init" -gt 0 ]        && risk=$((risk+3))
  [ "$n_session" -gt 0 ]     && risk=$((risk+3))
  [ "$n_sse" -gt 0 ]         && risk=$((risk+2))
  [ "$n_sampling" -gt 0 ]    && risk=$((risk+2))
  [ "$n_roots" -gt 0 ]       && risk=$((risk+2))
  [ "$n_fastmcp" -gt 0 ]     && risk=$((risk+1))
  [ "$is_client" -gt 0 ] && [ "$is_server" -gt 0 ] && risk=$((risk+3))

  local band
  if   [ "$risk" -ge 9 ]; then band="P0-critical"
  elif [ "$risk" -ge 5 ]; then band="P1-high"
  elif [ "$risk" -ge 2 ]; then band="P2-moderate"
  else band="P3-low"; fi

  if [ "$TSV" -eq 1 ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$name" "$band" "$archetype" "$lang" "${sdk:-none}" "$risk" "$runbook"
    return 0
  fi

  printf '\n\033[1m%s\033[0m  [%s]  risk=%d\n' "$name" "$band" "$risk"
  printf '  archetype : %s\n' "$archetype"
  printf '  language  : %s\n' "$lang"
  printf '  sdk pin   : %s\n' "${sdk:-none}"
  printf '  runbook   : %s\n' "$runbook"
  printf '  signals   :'
  [ "$unbounded" -eq 1 ]  && printf '\n    ! UNBOUNDED PIN, a fresh install now resolves to mcp 2.0.0 and will break'
  [ "$n_init" -gt 0 ]     && printf '\n    ! %s call(s) to .initialize(), the handshake is removed in 2026-07-28' "$n_init"
  [ "$n_session" -gt 0 ]  && printf '\n    ! %s session-id reference(s), Mcp-Session-Id is removed' "$n_session"
  [ "$n_sse" -gt 0 ]      && printf '\n    - %s legacy HTTP+SSE reference(s), deprecated with a 12 month offramp' "$n_sse"
  [ "$n_sampling" -gt 0 ] && printf '\n    - %s sampling reference(s), sampling is deprecated' "$n_sampling"
  [ "$n_roots" -gt 0 ]    && printf '\n    - %s roots reference(s), roots is deprecated' "$n_roots"
  [ "$n_logging" -gt 0 ]  && printf '\n    - %s logging reference(s), logging is deprecated' "$n_logging"
  [ "$n_fastmcp" -gt 0 ]  && printf '\n    - %s FastMCP reference(s), renamed to MCPServer in the official python SDK' "$n_fastmcp"
  [ "$n_toolcall" -gt 0 ] && printf '\n    - %s tool registration site(s) to review' "$n_toolcall"
  [ "$n_zod" -gt 0 ]      && printf '\n    - %s zod import(s), v2 requires zod ^4.2.0' "$n_zod"
  printf '\n'
  return 0
}

main() {
  if [ "$TSV" -eq 1 ]; then
    printf 'repo\tband\tarchetype\tlanguage\tsdk_pin\trisk\trunbook\n'
  else
    cat <<'BANNER'
MCP 2026-07-28 migration triage
Bands: P0-critical (fix now) · P1-high · P2-moderate · P3-low
BANNER
  fi

  local dirs=()
  if [ "$ALL" -eq 1 ]; then
    while IFS= read -r g; do dirs+=("$(dirname "$g")"); done \
      < <(find "$ROOT" -maxdepth 4 -type d -name .git 2>/dev/null | sort)
  elif [ "${#TARGETS[@]}" -gt 0 ]; then
    dirs=("${TARGETS[@]}")
  else
    dirs=(".")
  fi

  local n=0 skipped=0
  for d in "${dirs[@]}"; do
    if triage_one "$d"; then n=$((n+1)); else skipped=$((skipped+1)); fi
  done

  if [ "$TSV" -eq 0 ]; then
    printf '\n%d repo(s) triaged, %d skipped (no MCP usage detected).\n' "$n" "$skipped"
  fi
}

main
