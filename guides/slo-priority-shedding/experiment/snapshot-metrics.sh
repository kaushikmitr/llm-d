#!/usr/bin/env bash
# Snapshot the EPP metrics relevant to SLO admission / shedding, for per-phase diffing.
# Usage: ./snapshot-metrics.sh <label>   # writes results/metrics_<label>.txt
set -euo pipefail
LABEL="${1:?label, e.g. baseline-pre}"
NAMESPACE="${NAMESPACE:-llm-d-predicted-latency}"
RELEASE="${RELEASE:-predicted-latency-routing}"
OUT="$(dirname "$0")/results"; mkdir -p "$OUT"
DEST="$OUT/metrics_${LABEL}.txt"

# EPP /metrics is unauthenticated (auth=false). Scrape from a throwaway curl pod.
kubectl run snap-${LABEL//[^a-z0-9-]/-} --rm -i --restart=Never -n "${NAMESPACE}" \
  --image=curlimages/curl:8.10.1 --command -- \
  sh -c "curl -sS http://${RELEASE}-epp:9090/metrics" 2>/dev/null \
  | grep -iE "objective|slo|admit|shed|drop|prediction|predicted|queue|inference_pool" \
  | grep -E "^[a-z]" > "$DEST" || true

echo "[snapshot] wrote $(wc -l < "$DEST") metric lines -> $DEST"
echo "--- counters of interest ---"
grep -iE "slo_violation|admit|shed|drop|request_total" "$DEST" | head -30 || echo "(none yet — appear after SLO traffic)"
