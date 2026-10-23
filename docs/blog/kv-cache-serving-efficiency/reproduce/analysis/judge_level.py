#!/usr/bin/env python3
"""Judge one ladder level: steady-state tok/s, median TTFT, engine prefix hit rate over the steady window, and whether it collapsed.
Usage: judge_level.py <level result dir> <samples.csv> [ttft_collapse_s=5] [hit_collapse=0.6]
Prints one line: ttft_med hit steady_tps collapsed"""
import json, glob, sys, bisect, csv, collections, gzip
d, samples = sys.argv[1], sys.argv[2]; TT = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0; HC = float(sys.argv[4]) if len(sys.argv) > 4 else 0.6
fs = glob.glob(d + '/*/inference-perf_*/per_request_lifecycle_metrics.json*') or glob.glob(d + '/inference-perf_*/per_request_lifecycle_metrics.json*')
if not fs: print('NA NA NA 1'); sys.exit(0)
reqs = [r for r in json.load(gzip.open(fs[0],'rt') if fs[0].endswith('.gz') else open(fs[0])) if not r.get('error')]
def out_tok(r):
    try: mt = json.loads(r['request']).get('max_tokens')
    except Exception: mt = None
    return mt or ((r.get('info', {}).get('response_metrics') or {}).get('output_tokens') or 0)
starts = sorted(r['start_time'] for r in reqs); ends = sorted(r['end_time'] for r in reqs); t0, t1 = min(starts), max(ends)
act = []; t = t0
while t <= t1: act.append((t, bisect.bisect_right(starts, t) - bisect.bisect_right(ends, t))); t += 10
peak = max(a for _, a in act); win = [t for t, a in act if a >= 0.5 * peak]; ws, we = min(win), max(win)
tps = sum(r['info']['input_tokens'] + out_tok(r) for r in reqs if ws <= r['end_time'] <= we) / max(we - ws, 1)
# TTFT = first output token time - start (both on the harness's monotonic clock); map that clock to epoch via the server's 'created'
def first_tok(r): ot = (r['info'].get('response_metrics') or {}).get('output_token_times') or []; return ot[0] if ot else None
ttfts = sorted(first_tok(r) - r['start_time'] for r in reqs if ws <= r['end_time'] <= we and first_tok(r))
ttft = ttfts[len(ttfts) // 2] if ttfts else float('nan')
offs = []
for r in reqs[:200]:
    ch = (r['info'].get('response_metrics') or {}).get('response_chunks') or []
    if ch and first_tok(r):
        try: offs.append(json.loads(ch[0])['created'] - first_tok(r))
        except Exception: pass
off = sorted(offs)[len(offs) // 2] if offs else 0
ws, we = ws + off, we + off   # epoch window from here on
# engine hit rate over the window from the sampler
first = {}; last = {}
try:
    for row in csv.DictReader(open(samples)):
        ts = int(row['ts'])
        if not (ws <= ts <= we) or row['metric'] not in ('vllm:prefix_cache_queries_total', 'vllm:prefix_cache_hits_total'): continue
        k = (row['pod'], row['metric']); v = float(row['value'])
        if k not in first or ts < first[k][0]: first[k] = (ts, v)
        if k not in last or ts > last[k][0]: last[k] = (ts, v)
    q = sum(last[k][1] - first[k][1] for k in last if k[1].endswith('queries_total')); h = sum(last[k][1] - first[k][1] for k in last if k[1].endswith('hits_total'))
    hit = h / q if q > 0 else None
except Exception: hit = None
collapsed = int(ttft > TT or (hit is not None and hit < HC))
print(f"{ttft:.2f} {'NA' if hit is None else f'{hit:.3f}'} {tps:.0f} {collapsed}" + (f" {ws:.0f} {we:.0f}" if len(sys.argv) > 5 else ""))
