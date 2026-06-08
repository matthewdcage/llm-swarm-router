#!/usr/bin/env bash
# Compare netllm agent metrics before/after a connector-style batch workload.
# Usage: ./scripts/benchmark-parity.sh [agent_url] [num_requests]

set -euo pipefail

AGENT_URL="${1:-http://127.0.0.1:11400}"
N="${2:-10}"
MODEL="${3:-test-model}"

echo "Agent: $AGENT_URL"
echo "Requests: $N"

before=$(curl -sf "${AGENT_URL}/metrics" | grep '^netllm_requests_total' | tail -1 || echo "netllm_requests_total 0")

start=$(date +%s.%N)
for i in $(seq 1 "$N"); do
  curl -sf "${AGENT_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "X-Netllm-Batch-Id: bench" \
    -H "X-Netllm-Shard-Index: ${i}" \
    -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping ${i}\"}],\"max_tokens\":1}" \
    >/dev/null || echo "  request $i failed (no backend?)"
done
end=$(date +%s.%N)

elapsed=$(echo "$end - $start" | bc)
after=$(curl -sf "${AGENT_URL}/metrics" | grep '^netllm_requests_total' | tail -1 || echo "")

echo ""
echo "Elapsed: ${elapsed}s ($(echo "scale=2; $N / $elapsed" | bc) req/s)"
echo "Before: $before"
echo "After:  $after"
echo ""
echo "Compare with Honcho connector logs ([llm-pool] lines) using the same N and model."
