#!/usr/bin/env python3
"""Unlimited-retention prefix-cache oracle over a run's per-request records, at character granularity.
Requests are replayed in start order; every prefix of every prompt (in 256-char blocks, chained hashes) is cached forever.
rho_char = matched chars / prompt chars. Chars stand in for tokens: new and reused text are the same kind of text."""
import json, glob, sys, hashlib
d=sys.argv[1]; B=256
f=glob.glob(d+'/*/inference-perf_*/per_request_lifecycle_metrics.json')[0]
recs=[r for r in json.load(open(f)) if not r.get('error')]
recs.sort(key=lambda r:r['start_time'])
cache=set(); tot=hit=0; first_new=0; nfirst=0
for r in recs:
    p=json.loads(r['request'])['prompt']; n=len(p)
    h=0; matched=0; i=0
    while i+B<=n:
        h=hash((h,p[i:i+B]))
        if h in cache: matched+=B
        else: break
        i+=B
    # insert all prefixes
    h2=0; j=0
    while j+B<=n:
        h2=hash((h2,p[j:j+B])); cache.add(h2); j+=B
    tot+=n; hit+=matched
    s=r['info']; 
    def find(o):
        if isinstance(o,dict):
            if 'chat_round' in o: return o['chat_round']
            for v in o.values():
                x=find(v)
                if x is not None: return x
    if find(s)==1: first_new+=n-matched; nfirst+=1
print(f"{d}: requests {len(recs)}, rho_char = {hit/tot:.3f}; first-turn prompts {nfirst}, mean new chars on first turn {first_new/max(1,nfirst)/1e3:.0f}k of mean prompt {tot/len(recs)/1e3:.0f}k chars")
