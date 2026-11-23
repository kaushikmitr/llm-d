#!/usr/bin/env python3
"""Publish the raw run data behind the post in a compact form: one CSV per run with the per-request fields the analysis
uses (no prompt or response text), and one CSV of the engine counters sampled over the nine runs' windows.
Pod names are replaced by stable aliases: decode-fleet-<k> for the six replicas that served the llm-d and round-robin
runs, kvrouter-decode-<k> for the replicas behind the KV-load router.
Usage: RESULTS=<run root> analysis/compact_records.py <out dir>      (writes <out dir>/<arm>/conc_<N>.csv.gz, counters.csv.gz, runs.csv)"""
import csv, glob, gzip, json, os, sys, collections
RESULTS = os.environ.get('RESULTS', os.path.expanduser('~/llm-d/results/b200_fleet_offload'))
OUT = sys.argv[1]
RUNS = [('llmd_w32', 'llmd_w32', (120, 180, 240, 300)), ('rr_w32', 'rr_w32', (90, 120)), ('dynamo_w32', 'kvrouter_w32', (60, 90, 120))]
PAD = 120
COLS = ['conversation', 'turn', 'start_s', 'end_s', 'first_token_s', 'last_token_s', 'created_epoch', 'input_tokens', 'prompt_tokens', 'completion_tokens', 'max_tokens', 'error']
def keep(metric):
    return metric.startswith('smi:') or (metric.startswith('vllm:') and not metric.endswith(('_bucket', '_created')) and
            (metric.endswith('_total') or metric in ('vllm:kv_cache_usage_perc', 'vllm:num_requests_running', 'vllm:num_requests_waiting',
             'vllm:iteration_tokens_total_count', 'vllm:iteration_tokens_total_sum', 'vllm:kv_offload_cpu_cache_usage_perc',
             'vllm:kv_offload_cpu_cache_read_usage_perc', 'vllm:kv_offload_cpu_cache_write_usage_perc')))
windows = []; runs_rows = []
for src, dst, concs in RUNS:
    man = {int(r['conversations']): r for r in csv.DictReader(open(f'{RESULTS}/{src}/manifest.tsv'), delimiter='\t')}
    for c in concs:
        m = man[c]; t0, t1 = int(m['t0']), int(m['t1']); windows.append((t0 - PAD, t1 + PAD))
        runs_rows.append(dict(arm=dst, sessions=c, requests=m['num_requests'], seed=m['seed'], t0_epoch=t0, t1_epoch=t1, ttft_med_s=m.get('ttft_med_s', ''), steady_tps=m.get('steady_tps', '')))
        f = glob.glob(f'{RESULTS}/{src}/conc_{c}/*/inference-perf_*/per_request_lifecycle_metrics.json*')[0]
        print('reading', f, flush=True)
        recs = json.load(gzip.open(f, 'rt') if f.endswith('.gz') else open(f))
        base = min(r['start_time'] for r in recs)
        os.makedirs(f'{OUT}/{dst}', exist_ok=True)
        with gzip.open(f'{OUT}/{dst}/conc_{c}.csv.gz', 'wt', newline='') as fh:
            w = csv.writer(fh); w.writerow(COLS)
            for r in recs:
                i = r.get('info') or {}; x = i.get('extra_info') or {}; rm = i.get('response_metrics') or {}; su = rm.get('server_usage') or {}
                ot = rm.get('output_token_times') or []; ch = rm.get('response_chunks') or []
                try: created = json.loads(ch[0])['created'] if ch else ''
                except Exception: created = ''
                try: req = json.loads(r['request']); mt = req.get('max_tokens') or ''
                except Exception: mt = ''
                conv = str(x.get('user_session', '')).replace('conv_', ''); turn = x.get('chat_round', '')
                w.writerow([conv, turn, round(r['start_time'] - base, 3), round(r['end_time'] - base, 3), round(ot[0] - base, 3) if ot else '',
                            round(ot[-1] - base, 3) if ot else '', created, i.get('input_tokens', ''), su.get('prompt_tokens', ''), su.get('completion_tokens', ''), mt,
                            1 if r.get('error') else 0])
        del recs
        print('wrote', f'{OUT}/{dst}/conc_{c}.csv.gz', flush=True)
with open(f'{OUT}/runs.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(runs_rows[0].keys())); w.writeheader(); w.writerows(runs_rows)
alias = {}; n = collections.Counter(); kept = 0
with gzip.open(f'{OUT}/counters.csv.gz', 'wt', newline='') as fh:
    w = csv.writer(fh); w.writerow(['ts', 'pod', 'kind', 'port', 'metric', 'value'])
    for r in csv.DictReader(open(f'{RESULTS}/metrics/samples.csv')):
        try: ts = int(r['ts'])
        except ValueError: continue
        if not any(a <= ts <= b for a, b in windows) or not keep(r['metric']): continue
        p = r['pod']
        if p.startswith('dynamo-decode-'): fam = 'kvrouter-decode'
        elif p.startswith('decode-'): fam = 'decode-fleet'
        else: continue
        if p not in alias: n[fam] += 1; alias[p] = f'{fam}-{n[fam]}'
        w.writerow([ts, alias[p], 'kvrouter' if r['kind'] == 'dynamo' else r['kind'], r['port'], r['metric'], r['value']]); kept += 1
print('counters rows', kept, 'pods', alias)
