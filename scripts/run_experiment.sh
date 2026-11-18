#!/usr/bin/env bash
# run_experiment.sh — Experiment 1: run identical bursty traffic under each
# routing policy and save the results.
#
# Usage:
#   scripts/run_experiment.sh                         # all three policies
#   scripts/run_experiment.sh queue-aware round-robin # a subset, in this order
#   LOADGEN_ARGS="--phases 6:30,25:20,6:30" scripts/run_experiment.sh
#
# For each policy it:
#   1. restarts the simulated replicas, so every policy starts with empty
#      caches and zeroed counters (otherwise a later policy would benefit
#      from caches warmed by an earlier one);
#   2. switches the EPP to the policy (scripts/set-policy.sh restarts the
#      EPP, which also clears its prefix-cache index);
#   3. waits until a request goes through the gateway;
#   4. runs load/loadgen.py with the same seed, so traffic is identical;
#   5. saves each replica's metrics (requests served, prefix-cache hits).
#
# Output: results/<timestamp>/<policy>.csv, metrics/<policy>/<pod>.txt,
# meta.txt. Then run: python3 analysis/analyze.py results/<timestamp>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$SCRIPT_DIR/.."
export NAMESPACE="${NAMESPACE:-inferroute}"
export CONTEXT="${CONTEXT:-kind-inferroute}"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:30080}"
LOADGEN_ARGS="${LOADGEN_ARGS:-}"
SETTLE="${SETTLE:-10}"
OUT="${OUT:-$REPO/results/$(date +%Y%m%d-%H%M%S)}"

if [[ $# -gt 0 ]]; then
  POLICIES="$*"
else
  POLICIES="round-robin queue-aware queue-cache-aware"
fi

kc() { kubectl --context "$CONTEXT" -n "$NAMESPACE" "$@"; }

wait_for_gateway() {
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null -X POST "$GATEWAY_URL/v1/chat/completions" \
         -H 'Content-Type: application/json' \
         -d '{"model":"meta-llama/Llama-3.1-8B-Instruct","messages":[{"role":"user","content":"warmup"}],"max_tokens":1}'; then
      return 0
    fi
    sleep 2
  done
  echo "error: gateway not answering after 120s" >&2
  return 1
}

mkdir -p "$OUT"
{
  echo "date: $(date)"
  echo "git: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo n/a)"
  echo "policies: $POLICIES"
  echo "loadgen args: $LOADGEN_ARGS"
  echo "replicas: $(kc get deploy vllm-sim -o jsonpath='{.spec.replicas}')"
} > "$OUT/meta.txt"

for policy in $POLICIES; do
  echo
  echo "################ $policy ################"

  echo "==> restarting replicas (fresh caches and counters)"
  kc rollout restart deploy/vllm-sim >/dev/null
  kc rollout status deploy/vllm-sim --timeout=180s

  "$SCRIPT_DIR/set-policy.sh" "$policy"

  echo "==> waiting for the gateway"
  sleep "$SETTLE"              # let the EPP discover pods and scrape metrics
  wait_for_gateway

  # shellcheck disable=SC2086  # LOADGEN_ARGS is intentionally word-split
  python3 "$REPO/load/loadgen.py" --target "$GATEWAY_URL" --policy "$policy" \
    --out "$OUT/$policy.csv" $LOADGEN_ARGS

  echo "==> saving replica metrics"
  mkdir -p "$OUT/metrics/$policy"
  for pod in $(kc get pods -l app=vllm-sim -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'); do
    kubectl --context "$CONTEXT" get --raw \
      "/api/v1/namespaces/$NAMESPACE/pods/$pod:8000/proxy/metrics" \
      > "$OUT/metrics/$policy/$pod.txt" || echo "warning: no metrics from $pod" >&2
  done
done

echo
echo "==> done. Results in $OUT"
echo "    next: python3 analysis/analyze.py $OUT"
