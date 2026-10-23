#!/usr/bin/env python3
"""Achieved FLOP/s, HBM bytes/s and arithmetic intensity for a fleet level, from sampled vLLM counters.
Usage: flops_ai.py samples.csv [t_start t_end]   (epoch seconds; default = whole sample span)
FLOPs  = 2*N_act*(P+D) linear  +  4*L*Hq*dh*ctx per token attention (P = uncached prefill tokens, D = decode tokens)
Bytes  = per step: dense + E(1-(1-k/E)^n)*e expert weights (FP8, n = tokens in step)   [weights]
       + D*ctx*b decode KV reads + (P/8192)*ctx*b prefill-chunk KV reads + (P+D)*b KV writes
ctx    = mean resident context = kv_usage * C / running, averaged over the window.
Reference B200 (per GPU): 4.5e15 dense FP8 FLOP/s, 8e12 B/s HBM -> ridge 562 FLOP/B."""
import csv, sys, collections, math
N_ACT=35e9; N_TOT=480e9; E,k=160,8; e=(N_TOT-N_ACT)/(E-k); dense=N_ACT-k*e
L,HQ,DH=62,96,128; ATT=4*L*HQ*DH; B_KV=253952; C_TOK=3403712; GPUS=8; CHUNK=8192
F_PEAK=4.5e15; W_PEAK=8e12
rows=collections.defaultdict(dict)  # (pod,ts) -> {metric:val}
for r in csv.DictReader(open(sys.argv[1])):
    try: rows[(r['pod'],int(r['ts']))][r['metric']]=float(r['value'])
    except (TypeError, ValueError): continue   # truncated or malformed sample row
t0=int(sys.argv[2]) if len(sys.argv)>2 else min(t for _,t in rows); t1=int(sys.argv[3]) if len(sys.argv)>3 else max(t for _,t in rows)
pods=sorted({p for p,_ in rows})
def wread(n): return dense+E*(1-(1-k/E)**n)*e
print(f"window {t1-t0:.0f} s, {len(pods)} pods")
tot=dict(P=0,D=0,S=0,flop=0,byte=0,dt=0); 
for p in pods:
    ts=sorted(t for q,t in rows if q==p and t0<=t<=t1)
    if len(ts)<2: continue
    a,b=rows[(p,ts[0])],rows[(p,ts[-1])]; dt=ts[-1]-ts[0]
    g=lambda m: b.get(m,0)-a.get(m,0)
    P=g('vllm:prompt_tokens_total')-g('vllm:prefix_cache_hits_total')-g('vllm:external_prefix_cache_hits_total'); D=g('vllm:generation_tokens_total'); S=g('vllm:iteration_tokens_total_count')
    hit=(g('vllm:prefix_cache_hits_total')+g('vllm:external_prefix_cache_hits_total'))/max(g('vllm:prefix_cache_queries_total'),1)
    mid=[rows[(p,t)] for t in ts]
    run=sum(m.get('vllm:num_requests_running',0) for m in mid)/len(mid); kv=sum(m.get('vllm:kv_cache_usage_perc',0) for m in mid)/len(mid)
    smi={k2:(sum(m[k2] for m in mid if k2 in m)/max(1,len([m for m in mid if k2 in m]))) for k2 in ('smi:sm_busy_pct','smi:mem_busy_pct','smi:power_w')}
    ctx=kv*C_TOK/max(run,1e-9)
    n_step=(P+D)/max(S,1)
    flop=2*N_ACT*(P+D)+ATT*ctx*(D+P)           # attention over the resident context for every token
    byte=S*wread(n_step)+D*ctx*B_KV+(P/CHUNK)*ctx*B_KV+(P+D)*B_KV
    print(f"{p:26s} dt {dt:5.0f}s  hit {hit:.2f}  P {P/dt:7.0f} tok/s  D {D/dt:6.0f} tok/s  steps {S/dt:5.1f}/s ({n_step:5.0f} tok/step)  running {run:4.1f} kv {kv:.2f} ctx {ctx/1000:5.0f}k | "
          f"{flop/dt/GPUS/1e15:4.2f} PF/s/GPU ({flop/dt/GPUS/F_PEAK:4.1%} of peak)  {byte/dt/GPUS/1e12:4.2f} TB/s/GPU ({byte/dt/GPUS/W_PEAK:4.1%})  AI {flop/byte:5.0f} FLOP/B | smi sm {smi['smi:sm_busy_pct']:3.0f}% mem {smi['smi:mem_busy_pct']:3.0f}% {smi['smi:power_w']:4.0f} W")
    for kk,v in zip(('P','D','S','flop','byte','dt'),(P,D,S,flop,byte,dt)): tot[kk]+=v
if tot['dt']:
    n=len([p for p in pods if len([t for q,t in rows if q==p and t0<=t<=t1])>=2]); dt=tot['dt']/n
    print(f"FLEET  uncached prefill {tot['P']/dt/1000:.1f}k tok/s, decode {tot['D']/dt/1000:.1f}k tok/s | {tot['flop']/dt/(GPUS*n)/1e15:.2f} PF/s/GPU = {tot['flop']/dt/(GPUS*n)/F_PEAK:.1%} of dense FP8 peak | "
          f"{tot['byte']/dt/(GPUS*n)/1e12:.2f} TB/s/GPU = {tot['byte']/dt/(GPUS*n)/W_PEAK:.1%} of HBM | AI {tot['flop']/tot['byte']:.0f} FLOP/B (ridge {F_PEAK/W_PEAK:.0f})")
