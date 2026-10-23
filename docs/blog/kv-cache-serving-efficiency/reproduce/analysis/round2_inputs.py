#!/usr/bin/env python3
"""Per-run inputs for retention.py from the per-request records and the engine-counter samples (steady window as judge_level.py)."""
import json, glob, bisect, csv, collections, sys, os
HERE=os.path.dirname(os.path.abspath(__file__)); RESULTS=os.environ.get('RESULTS', os.path.join(HERE,'..','results'))  # run dirs: RESULTS/<arm>/conc_<N>
RUNS = [('llm-d 120',RESULTS+'/llmd_w32/conc_120','llmd',120,0.98),('llm-d 180',RESULTS+'/llmd_w32/conc_180','llmd',180,0.92),('llm-d 240',RESULTS+'/llmd_w32/conc_240','llmd',240,0.90),('llm-d 300',RESULTS+'/llmd_w32/conc_300','llmd',300,0.75),('Dynamo 60',RESULTS+'/dynamo_w32/conc_60','dynamo',60,0.76),('Dynamo 90',RESULTS+'/dynamo_w32/conc_90','dynamo',90,0.74),('Dynamo 120',RESULTS+'/dynamo_w32/conc_120','dynamo',120,0.67),('rr 90',RESULTS+'/rr_w32/conc_90','llmd',90,0.62),('rr 120',RESULTS+'/rr_w32/conc_120','llmd',120,0.53)]

samp = collections.defaultdict(list)
for r in csv.DictReader(open(RESULTS+'/metrics/samples.csv')):
    if r['metric'] in ('vllm:num_requests_running', 'vllm:num_requests_waiting'): samp[r['metric']].append((int(r['ts']), r['pod'], float(r['value'])))
out = []
for name, d, kind, conc, h in RUNS:
    f = glob.glob(d + '/*/inference-perf_*/per_request_lifecycle_metrics.json*')[0]
    recs = [r for r in json.load(open(f)) if not r.get('error')]
    st = sorted(r['start_time'] for r in recs); en = sorted(r['end_time'] for r in recs)
    act = []; t = st[0]
    while t <= en[-1]: act.append((t, bisect.bisect_right(st, t) - bisect.bisect_right(en, t))); t += 10
    pk = max(a for _, a in act); win = [t for t, a in act if a >= 0.5 * pk]; ws, we = min(win), max(win)
    inw = [r for r in recs if ws <= r['end_time'] <= we]
    def mt(r): return json.loads(r['request']).get('max_tokens') or 0
    I = sum(r['info']['request_metrics']['text']['input_tokens'] for r in inw) / len(inw); O = sum(mt(r) for r in inw) / len(inw)
    tps = sum(r['info']['request_metrics']['text']['input_tokens'] + mt(r) for r in inw) / (we - ws)
    eng, pause = [], []
    for r in inw:
        ot = (r['info'].get('response_metrics') or {}).get('output_token_times') or []
        if ot: eng.append(ot[-1] - r['start_time']); pause.append(r['end_time'] - ot[-1])
    inflight = sum(a for t, a in act if ws <= t <= we) / len([1 for t, a in act if ws <= t <= we])
    lam = len(inw) / (we - ws); gap = conc / lam - (sum(r['end_time'] - r['start_time'] for r in inw) / len(inw))
    # epoch offset (harness monotonic clock -> server 'created')
    offs = []
    for r in recs[:200]:
        ch = (r['info'].get('response_metrics') or {}).get('response_chunks') or []; ot = (r['info'].get('response_metrics') or {}).get('output_token_times') or []
        if ch and ot:
            try: offs.append(json.loads(ch[0])['created'] - ot[0])
            except Exception: pass
    off = sorted(offs)[len(offs) // 2]; e0, e1 = ws + off, we + off
    pods = set(p for t, p, v in samp['vllm:num_requests_running'] if e0 <= t <= e1 and (kind in p or (kind == 'llmd' and p.startswith('decode'))))
    def avg(m): v = [x for t, p, x in samp[m] if e0 <= t <= e1 and p in pods]; return sum(v) / max(1, len(v))
    out.append(dict(name=name, conc=conc, I=round(I), O=round(O, 1), h=h, tps_gpu=round(tps / 48), T_engine=round(sum(eng) / len(eng), 2),
                    Tp=round(sum(pause) / len(pause), 2), gap=round(gap, 2), inflight=round(inflight, 1), S_eff=round(inflight / 6, 2), S_nom=conc / 6,
                    n_run=round(avg('vllm:num_requests_running'), 2), n_wait=round(avg('vllm:num_requests_waiting'), 2), pods=len(pods)))
    print(json.dumps(out[-1]), flush=True)
json.dump(out, open(sys.argv[1], 'w'), indent=1)
