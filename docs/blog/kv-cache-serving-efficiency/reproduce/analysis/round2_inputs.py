#!/usr/bin/env python3
"""Per-run inputs for retention.py from the per-request records and the engine-counter samples (steady window as judge_level.py)."""
import json, glob, bisect, csv, collections, sys, os
HERE=os.path.dirname(os.path.abspath(__file__)); RESULTS=os.environ.get('RESULTS', os.path.join(HERE,'..','results'))  # run dirs: RESULTS/<arm>/conc_<N>
RUNS = [('llm-d 120',RESULTS+'/llmd_w32/conc_120','llmd',120,0.98),('llm-d 180',RESULTS+'/llmd_w32/conc_180','llmd',180,0.92),('llm-d 240',RESULTS+'/llmd_w32/conc_240','llmd',240,0.90),('llm-d 300',RESULTS+'/llmd_w32/conc_300','llmd',300,0.75),
        ('KV-load router 60',RESULTS+'/kvrouter_w32/conc_60','kvrouter',60,0.76),('KV-load router 90',RESULTS+'/kvrouter_w32/conc_90','kvrouter',90,0.74),('KV-load router 120',RESULTS+'/kvrouter_w32/conc_120','kvrouter',120,0.67),
        ('rr 90',RESULTS+'/rr_w32/conc_90','llmd',90,0.62),('rr 120',RESULTS+'/rr_w32/conc_120','llmd',120,0.53)]
RAW = os.path.join(RESULTS, 'raw')   # the published compact records (analysis/compact_records.py) serve when the harness JSON is absent

def load_records(d):
    """Per-request records as the harness wrote them, or the published compact CSV of the same run with the same fields."""
    fs = glob.glob(d + '/*/inference-perf_*/per_request_lifecycle_metrics.json*')
    if fs:
        out = []
        for r in json.load(open(fs[0])):
            if r.get('error'): continue
            i = r['info']; rm = i.get('response_metrics') or {}; ot = rm.get('output_token_times') or []; ch = rm.get('response_chunks') or []
            try: created = json.loads(ch[0])['created'] if ch else None
            except Exception: created = None
            out.append(dict(start_time=r['start_time'], end_time=r['end_time'], input_tokens=i['request_metrics']['text']['input_tokens'],
                            max_tokens=json.loads(r['request']).get('max_tokens') or 0, last_token=ot[-1] if ot else None, first_token=ot[0] if ot else None, created=created))
        return out
    arm, conc = os.path.basename(os.path.dirname(d.rstrip('/'))), os.path.basename(d.rstrip('/'))
    arm = {'dynamo_w32': 'kvrouter_w32'}.get(arm, arm)
    import gzip
    out = []
    for r in csv.DictReader(gzip.open(f'{RAW}/{arm}/{conc}.csv.gz', 'rt')):
        if r['error'] == '1': continue
        f = lambda k: float(r[k]) if r[k] != '' else None
        out.append(dict(start_time=f('start_s'), end_time=f('end_s'), input_tokens=int(r['input_tokens']), max_tokens=int(r['max_tokens'] or 0),
                        last_token=f('last_token_s'), first_token=f('first_token_s'), created=f('created_epoch')))
    return out

samp = collections.defaultdict(list)
import gzip
_sf = RESULTS+'/metrics/samples.csv'; _src = open(_sf) if os.path.exists(_sf) else gzip.open(RAW+'/counters.csv.gz', 'rt')
for r in csv.DictReader(_src):
    if r['metric'] in ('vllm:num_requests_running', 'vllm:num_requests_waiting'): samp[r['metric']].append((int(r['ts']), r['pod'], float(r['value'])))
out = []
for name, d, kind, conc, h in RUNS:
    recs = load_records(d)
    st = sorted(r['start_time'] for r in recs); en = sorted(r['end_time'] for r in recs)
    act = []; t = st[0]
    while t <= en[-1]: act.append((t, bisect.bisect_right(st, t) - bisect.bisect_right(en, t))); t += 10
    pk = max(a for _, a in act); win = [t for t, a in act if a >= 0.5 * pk]; ws, we = min(win), max(win)
    inw = [r for r in recs if ws <= r['end_time'] <= we]
    def mt(r): return r['max_tokens']
    I = sum(r['input_tokens'] for r in inw) / len(inw); O = sum(mt(r) for r in inw) / len(inw)
    tps = sum(r['input_tokens'] + mt(r) for r in inw) / (we - ws)
    eng, pause = [], []
    for r in inw:
        if r['last_token'] is not None: eng.append(r['last_token'] - r['start_time']); pause.append(r['end_time'] - r['last_token'])
    inflight = sum(a for t, a in act if ws <= t <= we) / len([1 for t, a in act if ws <= t <= we])
    lam = len(inw) / (we - ws); gap = conc / lam - (sum(r['end_time'] - r['start_time'] for r in inw) / len(inw))
    # epoch offset (harness monotonic clock -> server 'created')
    offs = []
    for r in recs[:200]:
        if r['created'] is not None and r['first_token'] is not None: offs.append(r['created'] - r['first_token'])
    off = sorted(offs)[len(offs) // 2]; e0, e1 = ws + off, we + off
    pods = set(p for t, p, v in samp['vllm:num_requests_running'] if e0 <= t <= e1 and (kind in p or (kind == 'llmd' and p.startswith('decode')) or (kind == 'kvrouter' and p.startswith('dynamo'))))
    def avg(m): v = [x for t, p, x in samp[m] if e0 <= t <= e1 and p in pods]; return sum(v) / max(1, len(v))
    out.append(dict(name=name, conc=conc, I=round(I), O=round(O, 1), h=h, tps_gpu=round(tps / 48), T_engine=round(sum(eng) / len(eng), 2),
                    Tp=round(sum(pause) / len(pause), 2), gap=round(gap, 2), inflight=round(inflight, 1), S_eff=round(inflight / 6, 2), S_nom=conc / 6,
                    n_run=round(avg('vllm:num_requests_running'), 2), n_wait=round(avg('vllm:num_requests_waiting'), 2), pods=len(pods)))
    print(json.dumps(out[-1]), flush=True)
json.dump(out, open(sys.argv[1], 'w'), indent=1)
