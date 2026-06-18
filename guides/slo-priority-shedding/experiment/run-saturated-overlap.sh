#!/usr/bin/env bash
# Saturated phase with GUARANTEED overlap: high-priority is the measured stream (fixed
# NREQ); the low-priority flood runs CONTINUOUSLY (relaunched whenever it drains) until high
# finishes, then is stopped. This avoids the count-matching trap — a heavily-shed flood
# (instant 429s) drains far faster than a slow long-context stream, so a fixed-count flood
# can't reliably outlast high. Each low iteration uses a fresh seed (cache-miss).
#
# Usage: ./run-saturated-overlap.sh <tag> <high_seed> <low_seed_base>
#   e.g. ./run-saturated-overlap.sh sat96c 5001 5100
set -uo pipefail
cd "$(dirname "$0")"
NS="${NAMESPACE:-llm-d-predicted-latency}"
TAG="${1:?tag}"; HSEED="${2:?high seed}"; LSEEDBASE="${3:?low seed base}"
export SLO_TTFT_MS=15000 SLO_TPOT_MS=40

./snapshot-metrics.sh "${TAG}-pre" >/dev/null 2>&1

# Measured high-priority stream (light, sustainable conc).
CONC_HIGH=8 NREQ_HIGH=144 SEED_HIGH=$HSEED NUM_CONVERSATIONS=32 \
  ./run-phase.sh configs/high-priority.yaml "exp-${TAG}-high" >/dev/null 2>&1
echo "launched high (exp-${TAG}-high, seed $HSEED)"

# Continuous low flood until high completes.
i=0
while :; do
  HACT=$(kubectl get job "exp-${TAG}-high" -n "$NS" -o jsonpath='{.status.active}' 2>/dev/null)
  [ -z "$HACT" ] && { echo "high finished"; break; }
  LJOB="exp-${TAG}-low-${i}"
  LACT=$(kubectl get job "$LJOB" -n "$NS" -o jsonpath='{.status.active}' 2>/dev/null)
  if [ -z "$LACT" ]; then
    i=$((i+1)); LJOB="exp-${TAG}-low-${i}"
    CONC_LOW=96 NREQ_LOW=1200 SEED_LOW=$((LSEEDBASE+i)) NUM_CONVERSATIONS=220 \
      ./run-phase.sh configs/low-priority.yaml "$LJOB" >/dev/null 2>&1
    echo "launched flood iteration $i ($LJOB, seed $((LSEEDBASE+i)))"
  fi
  sleep 20
done

# High done — stop the in-flight flood iteration.
kubectl delete job "exp-${TAG}-low-${i}" -n "$NS" --ignore-not-found >/dev/null 2>&1
echo "stopped in-flight flood iteration $i"
./snapshot-metrics.sh "${TAG}-post" >/dev/null 2>&1
echo "=== DONE: high=exp-${TAG}-high, low iterations 1..$((i-1)) completed (iteration $i stopped mid-run) ==="
