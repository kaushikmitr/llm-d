#!/usr/bin/env python3
"""Per ladder level: HBM prefix hits (vllm:prefix_cache_hits_total), CPU-tier hits (= vllm:kv_offload_load_bytes_total / 253,952,
validated against vllm:external_prefix_cache_hits_total on live pods), total hit rate, HBM and CPU-tier fullness, preemptions,
running/waiting per replica, over the level's steady window (judge_level.py)."""
import csv, collections, subprocess
R=RESULTS
B=253952
rows=collections.defaultdict(dict); kind={}
for r in csv.DictReader(open(f'{R}/metrics/samples.csv')):
    try: t=int(r['ts']); v=float(r['value'])
    except Exception: continue
    rows[(r['pod'],t)][r['metric']]=v; kind[r['pod']]=r['kind']
bypod=collections.defaultdict(list)
for (p,t) in rows: bypod[p].append(t)
for p in bypod: bypod[p].sort()
def window(d):
    o=subprocess.run(['python3',HERE+'/judge_level.py',d,f'{R}/metrics/samples.csv','5','0.6','window'],capture_output=True,text=True).stdout.split()
    return int(float(o[4])),int(float(o[5]))
print(f"{'stack':7s}{'conv':>5s} | {'HBM hit':>7s} {'CPU hit':>7s} {'total':>6s} | {'HBM full':>8s} {'CPU full':>8s} | {'preempt/min':>11s} {'run':>5s} {'wait':>5s} | tok/s")
for arm,k in (('llmd_w32','llmd'),('dynamo_w32','dynamo'),('rr_w32','llmd')):
    for r in sorted(csv.DictReader(open(f'{R}/{arm}/manifest.tsv'),delimiter='\t'),key=lambda r:int(r['conversations'])):
        c=r['conversations']; ws,we=window(f"{R}/{arm}/conc_{c}")
        q=h=lb=pre=0; hbm=[];cpu=[];run=[];wait=[]
        for p,ts in bypod.items():
            if kind[p]!=k: continue
            ts=[t for t in ts if ws<=t<=we]
            if len(ts)<2: continue
            a,b=rows[(p,ts[0])],rows[(p,ts[-1])]; g=lambda m:b.get(m,0)-a.get(m,0)
            q+=g('vllm:prefix_cache_queries_total'); h+=g('vllm:prefix_cache_hits_total'); lb+=g('vllm:kv_offload_load_bytes_total'); pre+=g('vllm:num_preemptions_total')
            for t in ts:
                m=rows[(p,t)]; hbm.append(m.get('vllm:kv_cache_usage_perc',0)); cpu.append(m.get('vllm:kv_offload_cpu_cache_usage_perc',0)); run.append(m.get('vllm:num_requests_running',0)); wait.append(m.get('vllm:num_requests_waiting',0))
        ext=lb/B; avg=lambda x:sum(x)/max(1,len(x))
        print(f"{arm:7s}{c:>5s} | {h/q:7.2f} {ext/q:7.2f} {(h+ext)/q:6.2f} | {avg(hbm):8.0%} {avg(cpu):8.0%} | {pre/((we-ws)/60):11.1f} {avg(run):5.1f} {avg(wait):5.1f} | {int(r['steady_tps'])/1000:.0f}k")
