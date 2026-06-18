#!/usr/bin/env bash
# Calibration ramp: run each concurrency level as a SEPARATE fresh Job (no cross-stage
# prefix-cache contamination), sequentially, and save each run's full log for parsing.
# Concurrency list and per-point request counts are tunable below.
set -uo pipefail
cd "$(dirname "$0")"
NS="${NAMESPACE:-llm-d-predicted-latency}"
CONCS=(${CONCS:-8 16 32 48 64})
mkdir -p results

for C in "${CONCS[@]}"; do
  NREQ=$(( C * 4 ))            # ~4 turns worth per slot — enough for stable percentiles
  JOB="exp-cal-c${C}"
  echo "=== calibration point: concurrency=${C} nreq=${NREQ} seed=${C} ==="
  CONC=$C NREQ=$NREQ SEED=$C NUM_CONVERSATIONS=$C ./run-phase.sh configs/calibrate-single.yaml "$JOB" >/dev/null 2>&1

  # Poll to completion (fresh pool each point; one concurrency at a time).
  for i in $(seq 1 160); do
    S=$(kubectl get job "$JOB" -n "$NS" -o jsonpath='{.status.succeeded}' 2>/dev/null)
    F=$(kubectl get job "$JOB" -n "$NS" -o jsonpath='{.status.failed}' 2>/dev/null)
    [ "$S" = "1" ] && { echo "  done (succeeded)"; break; }
    [ "$F" = "1" ] && { echo "  FAILED"; break; }
    sleep 15
  done
  POD=$(kubectl get pods -n "$NS" -l job="$JOB" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}' 2>/dev/null)
  kubectl logs "$POD" -n "$NS" > "results/cal_c${C}.log" 2>&1
  echo "  log -> results/cal_c${C}.log"
done
echo "=== ramp complete ==="
