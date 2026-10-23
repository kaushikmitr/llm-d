#!/usr/bin/env python3
"""Fewest model servers reporting metrics during a level's run window. Usage: fleet_check.py samples.csv kind t0 t1
kind: llmd (llm-d decode pods, also used by rr) or dynamo. Prints the minimum pod count over 15 s samples in [t0+60, t1-60]."""
import csv, sys, collections
f, kind, t0, t1 = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
pods = collections.defaultdict(set)
for r in csv.DictReader(open(f)):
    try: t = int(r['ts'])
    except Exception: continue
    if t0 + 60 <= t <= t1 - 60 and r['kind'] == kind and r['metric'] == 'vllm:num_requests_running': pods[t].add(r['pod'])
print(min((len(v) for v in pods.values()), default=0))
