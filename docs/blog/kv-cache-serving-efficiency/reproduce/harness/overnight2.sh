#!/bin/bash
# Round 2 of the offload experiment: 32 harness workers (the 8-worker harness was CPU-bound above ~150 live sessions)
# and the corrected router index (lruCapacityPerServer 193500). Runs llm-d 120..300 and round-robin 90/120 on the
# live llm-d model servers, then swaps the GPUs to Dynamo and runs 60/90. Collapse-stop disabled (all levels run;
# collapse is re-judged afterwards on total hit). Log: results/overnight2.log
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
run_ladder(){ local arm=$1 out=$2 lv=$3; log "$arm ladder ($out): levels $lv"; ARM=$arm OUT_NAME=$out LEVELS="$lv" bash harness/ladder.sh >> $R/ladder_$out.log 2>&1; log "$arm ladder finished: $(tail -n +2 $R/$out/manifest.tsv | cut -f1,9-11 | tr '\n' ';')"; }

log "overnight2 start: 32 harness workers, router lruCapacityPerServer 193500"
[ "$($K get deploy decode -o jsonpath='{.status.readyReplicas}')" = "6" ] || die "llm-d model servers not at 6 ready"
$K logs deploy/agentic-b200-epp -c epp | grep -m1 'Prefix DataProducer initialized' | grep -q '"lruCapacityPerServer":193500' || die "router index is not 193500"
sampler; run_ladder llmd llmd_w32 "120 180 240 300"
sampler; run_ladder rr rr_w32 "90 120"

# GPUs to Dynamo
$K scale deploy decode --replicas=0
for i in $(seq 1 60); do [ -z "$($K get pods -l llm-d.ai/role=decode -o name)" ] && break; sleep 20; done
$K scale deploy dynamo-decode --replicas=6
for i in $(seq 1 180); do r=$($K get deploy dynamo-decode -o jsonpath='{.status.readyReplicas}'); [ "$r" = "6" ] && break
  $K get pods -l app=dynamo-decode -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{" "}{end}' | grep -q '[1-9]' && die "a Dynamo worker restarted during startup"; sleep 30; done
[ "$r" = "6" ] || die "Dynamo workers not ready after 90 min"
for p in $($K get pods -l app=dynamo-decode -o jsonpath='{.items[*].metadata.name}'); do L=$($K logs $p --tail=30000)
  n=$(echo "$L" | grep -c 'Host-registered CPU offload region'); e=$(echo "$L" | grep -c -E 'RuntimeError|out of memory|cudaHostRegister failed')
  log "$p pinned_ranks=$n errors=$e"; [ "$n" = "8" ] && [ "$e" = "0" ] || die "$p did not pin cleanly"; done
$K rollout restart deploy/dynamo-frontend && $K rollout status deploy/dynamo-frontend --timeout=600s || die "frontend restart failed"
sleep 60
out=$($K run smoke-dynamo-w32 --rm -i --restart=Never --image=curlimages/curl:8.10.1 -- curl -s -m 180 http://dynamo-frontend.kv-fleet.svc:8000/v1/completions -H 'Content-Type: application/json' -d '{"model":"Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8","prompt":"def add(a, b):","max_tokens":8}' 2>&1)
echo "$out" | grep -q '"finish_reason"' || die "Dynamo smoke test failed: $(echo "$out" | head -c 300)"
log "Dynamo smoke test ok"
sampler; run_ladder dynamo dynamo_w32 "60 90"
log "OVERNIGHT2_COMPLETE (Dynamo left running on the GPUs; llm-d decode scaled to 0)"
