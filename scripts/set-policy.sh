#!/usr/bin/env bash
# set-policy.sh — switch the EPP routing policy.
#
# Usage:
#   scripts/set-policy.sh list              # show available policies
#   scripts/set-policy.sh current           # show the active policy
#   scripts/set-policy.sh <policy>          # e.g. round-robin | queue-aware | queue-cache-aware
#
# How it works: the EPP reads its EndpointPickerConfig from the ConfigMap
# $CONFIGMAP (mounted at /etc/epp/config.yaml). This script replaces that
# ConfigMap with policies/<policy>.yaml, restarts the EPP so it reloads the
# file, waits for it to be ready, and records the policy name as an
# annotation on the Deployment so `current` can report it.
#
# Override defaults with environment variables, e.g.
#   NAMESPACE=inferroute CONTEXT=kind-inferroute scripts/set-policy.sh queue-aware
set -euo pipefail

NAMESPACE="${NAMESPACE:-inferroute}"
CONFIGMAP="${CONFIGMAP:-epp-config}"
DEPLOYMENT="${DEPLOYMENT:-epp}"
CONTEXT="${CONTEXT:-}"                 # empty = current kubectl context
TIMEOUT="${TIMEOUT:-120s}"
ANNOTATION="inferroute/policy"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY_DIR="${POLICY_DIR:-$SCRIPT_DIR/../policies}"

kc() {
  if [[ -n "$CONTEXT" ]]; then
    kubectl --context "$CONTEXT" -n "$NAMESPACE" "$@"
  else
    kubectl -n "$NAMESPACE" "$@"
  fi
}

list_policies() {
  for f in "$POLICY_DIR"/*.yaml; do
    basename "$f" .yaml
  done
}

usage() {
  echo "usage: $0 list | current | <policy>" >&2
  echo "policies:" >&2
  list_policies | sed 's/^/  /' >&2
  exit 1
}

[[ $# -eq 1 ]] || usage

case "$1" in
  list)
    list_policies
    ;;
  current)
    policy="$(kc get deploy "$DEPLOYMENT" \
      -o jsonpath="{.metadata.annotations.inferroute/policy}" 2>/dev/null || true)"
    echo "${policy:-unknown (not set by this script)}"
    ;;
  -h|--help)
    usage
    ;;
  *)
    policy="$1"
    file="$POLICY_DIR/$policy.yaml"
    if [[ ! -f "$file" ]]; then
      echo "error: no policy file $file" >&2
      usage
    fi

    echo "==> switching EPP to policy: $policy"
    kc create configmap "$CONFIGMAP" --from-file=config.yaml="$file" \
      --dry-run=client -o yaml | kc apply -f -

    kc annotate deploy "$DEPLOYMENT" "$ANNOTATION=$policy" --overwrite >/dev/null
    kc rollout restart deploy "$DEPLOYMENT" >/dev/null
    echo "==> waiting for EPP rollout (timeout $TIMEOUT)"
    if ! kc rollout status deploy "$DEPLOYMENT" --timeout="$TIMEOUT"; then
      echo "error: EPP did not become ready; recent logs:" >&2
      kc logs deploy/"$DEPLOYMENT" -c epp --tail=30 >&2 || true
      exit 1
    fi
    echo "==> active policy: $policy"
    ;;
esac
