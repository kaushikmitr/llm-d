#!/bin/bash
# Round 2 of the offload experiment: 32 harness workers (the 8-worker harness was CPU-bound above ~150 live sessions)
# and the corrected router index (lruCapacityPerServer 193500). Runs llm-d 120..300 and round-robin 90/120 on the
# live llm-d model servers. Collapse-stop disabled (all levels run;
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

log "OVERNIGHT2_COMPLETE"
