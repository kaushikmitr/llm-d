#!/bin/bash
# Dynamo rerun after the worker-set admission fix (node p8nw cache made identical) and self_describing_kv_events=true.
# Waits for the worker rollout, checks pinning, restarts the frontend, verifies ALL 6 workers are admitted and that routing
# spreads a burst across workers, then runs the ladder at 60 and 90 (32 harness workers). Log: results/dynamo2.log
set -u
export PATH=$PATH
cd "$(dirname "$0")/.."   # reproduce/
CTX=${KUBE_CONTEXT:?set KUBE_CONTEXT to the kubectl context of the fleet}
K="kubectl --context $CTX -n kv-fleet"
R=results
FLEET=harness
export TEMPLATE=$R/config.template.w32.yaml HIT_COLLAPSE=0 TTFT_COLLAPSE=999
log(){ echo "$(date -u '+%F %T') $*"; }
die(){ log "STOP: $*"; exit 1; }
sampler(){ ps -eo cmd | grep -q "^python3 .*sample_metrics_offload.py" || { log "sampler not running, restarting"; setsid nohup python3 harness/sample_metrics_offload.py $R/metrics/samples.csv 15 >> $R/metrics/sampler.log 2>&1 < /dev/null & }; }
FE(){ $K exec deploy/dynamo-frontend -- curl -s localhost:8000/metrics 2>/dev/null; }

log "dynamo2 start"
sleep 60
for i in $(seq 1 180); do r=$($K get deploy dynamo-decode -o jsonpath='{.status.readyReplicas}'); u=$($K get deploy dynamo-decode -o jsonpath='{.status.updatedReplicas}'); [ "$r" = "6" ] && [ "$u" = "6" ] && [ "$($K get pods -l app=dynamo-decode --no-headers | wc -l)" = "6" ] && break
  $K get pods -l app=dynamo-decode -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{" "}{end}' | grep -q '[1-9]' && die "a Dynamo worker restarted during startup"; sleep 30; done
[ "$r" = "6" ] || die "Dynamo workers not ready after 90 min"
sleep 30
for p in $($K get pods -l app=dynamo-decode -o jsonpath='{.items[*].metadata.name}'); do L=$($K logs $p --tail=30000)
  n=$(echo "$L" | grep -c 'Host-registered CPU offload region'); e=$(echo "$L" | grep -c -E 'RuntimeError|out of memory|cudaHostRegister failed')
  log "$p node=$($K get pod $p -o jsonpath='{.spec.nodeName}') pinned_ranks=$n errors=$e"; [ "$n" = "8" ] && [ "$e" = "0" ] || die "$p did not pin cleanly"; done
$K rollout restart deploy/dynamo-frontend >/dev/null && $K rollout status deploy/dynamo-frontend --timeout=600s || die "frontend restart failed"
sleep 45
REG=$(FE | grep -c '^dynamo_component_router_worker_registered{.*} 1$')
log "router_worker_registered: $REG of 6"
[ "$REG" = "6" ] || die "router admitted only $REG workers: $($K logs deploy/dynamo-frontend | sed -E 's/\x1b\[[0-9;]*m//g' | grep -a 'Rejected incompatible' | head -1 | cut -c1-400)"
T=$(date -u +%Y-%m-%dT%H:%M:%S)
$K run burst-dynamo --rm -i --restart=Never --image=curlimages/curl:8.10.1 -- sh -c 'for i in $(seq 1 24); do curl -s -m 120 http://dynamo-frontend.kv-fleet.svc:8000/v1/completions -H "Content-Type: application/json" -d "{\"model\":\"Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8\",\"prompt\":\"Admission burst test $i, unique subject $i: one sentence.\",\"max_tokens\":4}" >/dev/null & done; wait; echo burst-done' >/dev/null 2>&1
sleep 5
DIST=$($K logs deploy/dynamo-frontend --since-time=${T}Z | sed -E 's/\x1b\[[0-9;]*m//g' | grep -a 'Selected worker' | grep -oE 'worker_id=[0-9]+' | sort | uniq -c | tr '\n' ';')
log "burst of 24 distinct prompts -> $DIST"
[ "$(echo "$DIST" | tr ';' '\n' | grep -c worker_id)" -ge 4 ] || die "burst went to fewer than 4 workers"
sampler
log "dynamo ladder (dynamo_w32): levels 60 90"
ARM=dynamo OUT_NAME=dynamo_w32 LEVELS="60 90" bash harness/ladder.sh >> $R/ladder_dynamo_w32.log 2>&1
log "dynamo ladder finished: $(tail -n +2 $R/dynamo_w32/manifest.tsv | cut -f1,9-11 | tr '\n' ';')"
log "DYNAMO2_COMPLETE"
