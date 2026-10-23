#!/bin/bash
# Adaptive ladder: run ascending LEVELS for ARM; after each level judge collapse (median TTFT > TTFT_COLLAPSE s in the steady
# window, or engine prefix hit rate < HIT_COLLAPSE); stop after the first collapsed level once a non-collapsed level exists.
# Keeps three points per arm: lowest (no collapse), last non-collapsed (near collapse), first collapsed (post collapse).
export PATH=$PATH
export HARNESS_NODEPOOL=${HARNESS_NODEPOOL:-cpu-pool-3} HARNESS_CPU_NR=${HARNESS_CPU_NR:-32} HARNESS_CPU_MEM=${HARNESS_CPU_MEM:-128Gi}
cd "$(dirname "$0")/.."   # reproduce/
K="kubectl --context ${KUBE_CONTEXT:?set KUBE_CONTEXT} -n ${NAMESPACE:-kv-fleet}"
ARM=${ARM:?}; LEVELS=${LEVELS:?}; OUT=results/${OUT_NAME:-$ARM}; mkdir -p $OUT
case $ARM in llmd*) export BASE_URL=http://agentic-b200-epp.kv-fleet.svc:80; DEP=decode;; rr*) export BASE_URL=http://decode-rr.kv-fleet.svc:8000; DEP=decode;; dynamo*) export BASE_URL=http://dynamo-frontend.kv-fleet.svc:8000; DEP=dynamo-decode;; esac
for i in $(seq 1 360); do R=$($K get deploy $DEP -o jsonpath='{.status.readyReplicas}' 2>/dev/null); [ "$R" = "6" ] && break; sleep 30; done; echo "$(date -u) $ARM ready replicas: $R"
echo -e "conversations\tnum_requests\tseed\tstatus\texit\tresult_dir\tt0\tt1\tttft_med_s\tengine_hit\tsteady_tps\tcollapsed" > $OUT/manifest.tsv
NONC=0
for C in $LEVELS; do
 for TRY in 1 2; do
  for i in $(seq 1 240); do R=$($K get deploy $DEP -o jsonpath='{.status.readyReplicas}' 2>/dev/null); [ "$R" = "6" ] && break; sleep 30; done
  [ "$R" = "6" ] || { echo "$(date -u) LADDER_ABORT only ${R:-0} ready replicas after 2 h"; break 2; }
  $K delete pod llmdbench-harness-launcher --ignore-not-found --wait=true >/dev/null 2>&1
  export NUM_CONVERSATIONS=$C CONCURRENCY_LEVEL=$((4*C)) NUM_REQUESTS=$((20*C)) SEED=$((3007+C))
  envsubst < ${TEMPLATE:-results/config.template.yaml} > $OUT/config_c$C.yaml
  T0=$(date +%s); echo "$(date -u) === $ARM conc $C start"
  harness/run_only.sh -c $OUT/config_c$C.yaml -o $OUT/conc_$C > $OUT/conc_$C.log 2>&1; rc=$?; T1=$(date +%s)
  read TTFT HIT TPS COL <<< "$(python3 analysis/judge_level.py $OUT/conc_$C results/metrics/samples.csv ${TTFT_COLLAPSE:-5} ${HIT_COLLAPSE:-0.6})"
  echo -e "$C\t$NUM_REQUESTS\t$SEED\t$([ $rc -eq 0 ] && echo DONE || echo FAIL)\t$rc\t$OUT/conc_$C\t$T0\t$T1\t$TTFT\t$HIT\t$TPS\t$COL" >> $OUT/manifest.tsv
  echo "$(date -u) === $ARM conc $C exit $rc | ttft_med $TTFT s, engine hit $HIT, steady $TPS tok/s, collapsed=$COL"
  MINP=$(python3 harness/fleet_check.py results/metrics/samples.csv $( [ "$DEP" = "dynamo-decode" ] && echo dynamo || echo llmd ) $T0 $T1)
  if [ "$MINP" -lt 6 ]; then echo "$(date -u) === $ARM conc $C ran with only $MINP model servers at some point; $( [ $TRY = 1 ] && echo rerunning || echo 'keeping, flagged')"
    [ $TRY = 1 ] && { sed -i '$ d' $OUT/manifest.tsv; mv $OUT/conc_$C $OUT/conc_${C}_partial_fleet_$T0; continue; }
    sed -i '$ s/$/\tPARTIAL_FLEET/' $OUT/manifest.tsv; fi
  break
 done
  if [ "$COL" = "1" ] && [ $NONC -ge 1 ]; then echo "$(date -u) LADDER_STOP after first collapsed level $C"; break; fi
  [ "$COL" = "1" ] && CONSEC=$((${CONSEC:-0}+1)) || CONSEC=0
  if [ "$NONC" -eq 0 ] && [ "${CONSEC:-0}" -ge 2 ]; then echo "$(date -u) LADDER_STOP two consecutive collapsed levels and none healthy ($C)"; break; fi
  [ "$COL" = "0" ] && NONC=$((NONC+1))
done
$K delete pod llmdbench-harness-launcher --ignore-not-found --wait=true >/dev/null 2>&1
echo PIPELINE_COMPLETE
