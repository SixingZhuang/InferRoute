#!/usr/bin/env bash
# spread.sh — send N requests through the gateway, then show how many each
# replica completed. A quick check that routing works and of how the active
# policy spreads load.
#
# Usage: scripts/spread.sh [N]   (default 30)
#
# Per-replica counts come from each pod's vllm:request_success_total metric,
# read through the Kubernetes API server proxy (no port-forward needed).
# Counts are cumulative since the pod started, so the script prints the
# difference before vs. after the run.
set -euo pipefail

N="${1:-30}"
NAMESPACE="${NAMESPACE:-inferroute}"
CONTEXT="${CONTEXT:-}"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:30080}"
MODEL="${MODEL:-meta-llama/Llama-3.1-8B-Instruct}"

kc() {
  if [[ -n "$CONTEXT" ]]; then kubectl --context "$CONTEXT" "$@"; else kubectl "$@"; fi
}

pods() {
  kc -n "$NAMESPACE" get pods -l app=vllm-sim \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
}

served() {  # $1 = pod name -> total successful requests so far
  kc get --raw "/api/v1/namespaces/$NAMESPACE/pods/$1:8000/proxy/metrics" 2>/dev/null \
    | awk '/^vllm:request_success_total/ {s += $NF} END {printf "%d\n", s}'
}

# Plain files instead of associative arrays: macOS ships bash 3.2.
snap="$(mktemp)"; trap 'rm -f "$snap"' EXIT
for p in $(pods); do echo "$p $(served "$p")" >> "$snap"; done

echo "==> sending $N requests to $GATEWAY_URL"
ok=0; fail=0
for i in $(seq 1 "$N"); do
  if curl -sf -o /dev/null -X POST "$GATEWAY_URL/v1/chat/completions" \
       -H 'Content-Type: application/json' \
       -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"request $i\"}]}"; then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
  fi
done
echo "    ok=$ok failed=$fail"

echo "==> requests served per replica during this run"
for p in $(pods); do
  after=$(served "$p")
  before=$(awk -v p="$p" '$1 == p {print $2}' "$snap")
  printf "  %-40s %d\n" "$p" $(( after - ${before:-0} ))
done
