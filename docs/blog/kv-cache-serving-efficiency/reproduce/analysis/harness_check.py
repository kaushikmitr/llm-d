#!/usr/bin/env python3
"""Check whether a conversation-replay run still has harness lockstep.
Usage: harness_check.py <run dir> <num_conversations>
Steady window = time in which in-flight (start..end) >= 50% of its peak. end_time includes the tool sleep.
Per conversation, cycle = N / completion rate (Little). Leftover wait = cycle - mean(start..end). ~0 means no lockstep."""
import json, glob, sys, bisect, gzip
d, N = sys.argv[1], int(sys.argv[2])
f = glob.glob(d + '/*/inference-perf_*/per_request_lifecycle_metrics.json*') + glob.glob(d + '/inference-perf_*/per_request_lifecycle_metrics.json*')
recs = [r for r in json.load(gzip.open(f[0], 'rt') if f[0].endswith('.gz') else open(f[0])) if not r.get('error')]
st = sorted(r['start_time'] for r in recs); en = sorted(r['end_time'] for r in recs)
act = []; t = st[0]
while t <= en[-1]: act.append((t, bisect.bisect_right(st, t) - bisect.bisect_right(en, t))); t += 5
pk = max(a for _, a in act); win = [t for t, a in act if a >= 0.5 * pk]; ws, we = min(win), max(win)
inw = [r for r in recs if ws <= r['end_time'] <= we]
lam = len(inw) / (we - ws)
svc = sum(r['end_time'] - r['start_time'] for r in inw) / len(inw)
eng, tool = [], []
for r in inw:
    ot = (r['info'].get('response_metrics') or {}).get('output_token_times') or []
    if ot: eng.append(ot[-1] - r['start_time']); tool.append(r['end_time'] - ot[-1])
E, T = sum(eng) / len(eng), sum(tool) / len(tool)
infl = sum(a for t, a in act if ws <= t <= we) / len([1 for t, a in act if ws <= t <= we])
print(json.dumps({'requests_ok': len(recs), 'errors': sum(1 for r in json.load(open(f[0])) if r.get('error')) if not f[0].endswith('.gz') else None,
  'window_s': round(we - ws), 'conversations': N, 'peak_in_flight_incl_tool': pk, 'mean_in_flight_incl_tool': round(infl, 1),
  'mean_engine_s': round(E, 1), 'mean_tool_s': round(T, 1), 'cycle_from_rate_s': round(N / lam, 1),
  'leftover_wait_per_turn_s': round(N / lam - svc, 1), 'expected_on_wire': round(N * E / (E + T), 1)}, indent=1))
