#!/bin/bash
# Extend the Dynamo ladder upward while throughput keeps rising: after dynamo2 finishes 60/90, run 120 if tps(90)>tps(60),
# then 180 if tps(120)>tps(90). Appends to dynamo_w32/manifest.tsv. Log: dynamo3.log
set -u; cd "$(dirname "$0")/.."   # reproduce/
R=results; export TEMPLATE=$R/config.template.w32.yaml HIT_COLLAPSE=0 TTFT_COLLAPSE=999
log(){ echo "$(date -u '+%F %T') $*"; }
tps(){ awk -F'\t' -v c=$1 'NR>1 && $1==c{print int($11)}' $R/dynamo_w32/manifest.tsv | tail -1; }
run(){ cp $R/dynamo_w32/manifest.tsv $R/dynamo_w32/manifest.keep.tsv; ARM=dynamo OUT_NAME=dynamo_w32 LEVELS="$1" bash harness/ladder.sh >> $R/ladder_dynamo_w32.log 2>&1
  tail -n +2 $R/dynamo_w32/manifest.tsv >> $R/dynamo_w32/manifest.keep.tsv; mv $R/dynamo_w32/manifest.keep.tsv $R/dynamo_w32/manifest.tsv; }
until grep -qE 'DYNAMO2_COMPLETE|STOP:' $R/dynamo2.log; do sleep 60; done
grep -q STOP: $R/dynamo2.log && { log "dynamo2 stopped; not extending"; exit 1; }
log "dynamo2 done: 60=$(tps 60) 90=$(tps 90)"
prev=90
for c in 120 180; do
  p=$(( prev==90 ? 60 : 90 ))
  if [ "$(tps $prev)" -gt "$(tps $p)" ]; then log "tps($prev) > tps($p): running $c"; run $c; log "$c -> $(tps $c)"; prev=$c; else log "tps($prev) <= tps($p): peak passed, stopping"; break; fi
done
log "DYNAMO3_COMPLETE: $(tail -n +2 $R/dynamo_w32/manifest.tsv | cut -f1,9-11 | tr '\n' ';')"
