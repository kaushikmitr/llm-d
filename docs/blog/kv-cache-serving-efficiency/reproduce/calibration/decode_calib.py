#!/usr/bin/env python3
"""Decode calibration against a live vLLM endpoint (stdlib only).

For each (context_len, concurrency) case: start `concurrency` streaming requests at once, each with a fresh
random token-ID prompt of context_len tokens and max_tokens=OUT, and record per-token arrival times after the
first token. Reports median inter-token latency (TPOT) per case.

  c=1, short context  -> step time ~ weight stream = W_b / W      -> alpha_W
  c=1, long context   -> step time ~ (W_b + ctx*b) / W            -> checks b and W together
  c=k, long context   -> step time ~ (W_b + k*ctx*b) / W          -> KV-read slope

Env: VLLM_ENDPOINT, MODEL_NAME, CASES="ctx:conc,ctx:conc,...", OUT (default 128), TOKEN_MIN/MAX.
"""
import json, os, random, statistics, threading, time, urllib.request

EP = os.environ["VLLM_ENDPOINT"].rstrip("/"); MODEL = os.environ["MODEL_NAME"]
CASES = [tuple(int(x) for x in c.split(":")) for c in os.environ.get("CASES", "1024:1,131072:1,131072:4").split(",")]
OUT = int(os.environ.get("OUT", "128")); TMIN = int(os.environ.get("TOKEN_MIN", "100")); TMAX = int(os.environ.get("TOKEN_MAX", "10000"))
rng = random.Random(time.time_ns())

def one(ctx, out, result, idx):
    prompt = [rng.randint(TMIN, TMAX) for _ in range(ctx)]
    body = json.dumps({"model": MODEL, "prompt": prompt, "max_tokens": out, "min_tokens": out, "stream": True, "temperature": 0, "ignore_eos": True}).encode()
    req = urllib.request.Request(f"{EP}/v1/completions", data=body, headers={"Content-Type": "application/json"})
    t0 = time.monotonic(); stamps = []
    with urllib.request.urlopen(req, timeout=1800) as resp:
        for line in resp:
            if line.startswith(b"data:") and b"[DONE]" not in line:
                stamps.append(time.monotonic())
    result[idx] = (stamps[0] - t0 if stamps else None, [b - a for a, b in zip(stamps[1:], stamps[2:])])  # skip first gap (scheduling)

for ctx, conc in CASES:
    res = [None] * conc
    th = [threading.Thread(target=one, args=(ctx, OUT, res, i)) for i in range(conc)]
    t0 = time.monotonic(); [t.start() for t in th]; [t.join() for t in th]; wall = time.monotonic() - t0
    itls = [x for r in res if r for x in r[1]]; ttfts = [r[0] for r in res if r and r[0] is not None]
    med = statistics.median(itls) if itls else float("nan")
    print(f"CASE ctx={ctx} conc={conc}: TTFT median={statistics.median(ttfts):.3f}s  TPOT median={med*1000:.2f} ms  p90={sorted(itls)[int(0.9*len(itls))]*1000:.2f} ms  tokens={len(itls)+2*conc}  wall={wall:.1f}s", flush=True)
    print(f"RESULT ctx={ctx} conc={conc} tpot_ms={med*1000:.3f}", flush=True)
