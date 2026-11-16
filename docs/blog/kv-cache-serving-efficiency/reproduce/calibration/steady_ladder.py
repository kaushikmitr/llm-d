#!/usr/bin/env python3
"""Steady-batch decode ladder against a live vLLM endpoint (stdlib only).

Extends calibration/decode_calib.py with a steady window. For each case (ctx, conc): start `conc` streaming
requests at once, each with a fresh random prompt of `ctx` tokens and OUT output tokens. The engine prefills
them one after another, so early requests decode while later ones still prefill. Only the window after the
LAST request's first token and before the FIRST request's last token has all `conc` requests decoding at once
with no prefill in flight; inter-token gaps inside that window are the steady step time at batch `conc`.

Reports per case: steady-window median / mean / p90 step time, decode tokens per second in the window, window
length, and the whole-run median for comparison with decode_calib.py.

Interference mode (PREFILL_STREAM=1): while the `conc` decoders run (long OUT), one background client sends
back-to-back cold prompts of `ctx` tokens with max_tokens=1, so every step carries prefill chunks. Reports the
decoders' step time in the window plus the prefill tokens per second achieved.

Env: VLLM_ENDPOINT, MODEL_NAME, CASES="ctx:conc,...", OUT (default 256), PREFILL_STREAM (0/1),
     PREFILL_CTX (default = case ctx), TOKEN_MIN/MAX.
"""
import json, os, random, statistics, threading, time, urllib.request

EP = os.environ["VLLM_ENDPOINT"].rstrip("/"); MODEL = os.environ["MODEL_NAME"]
CASES = [tuple(int(x) for x in c.split(":")) for c in os.environ.get("CASES", "116684:1,116684:12,116684:18,116684:24,116684:28").split(",")]
OUT = int(os.environ.get("OUT", "256")); TMIN = int(os.environ.get("TOKEN_MIN", "100")); TMAX = int(os.environ.get("TOKEN_MAX", "10000"))
STREAM = os.environ.get("PREFILL_STREAM", "0") == "1"; PCTX = int(os.environ.get("PREFILL_CTX", "0"))
rng = random.Random(time.time_ns())

def stream_one(ctx, out, result, idx):
    prompt = [rng.randint(TMIN, TMAX) for _ in range(ctx)]
    body = json.dumps({"model": MODEL, "prompt": prompt, "max_tokens": out, "min_tokens": out, "stream": True,
                       "temperature": 0, "ignore_eos": True}).encode()
    req = urllib.request.Request(f"{EP}/v1/completions", data=body, headers={"Content-Type": "application/json"})
    t0 = time.monotonic(); stamps = []
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            for line in resp:
                if line.startswith(b"data:") and b"[DONE]" not in line:
                    stamps.append(time.monotonic())
    except Exception as e:
        print(f"  request {idx} failed: {e}", flush=True)
    result[idx] = (t0, stamps)

def prefill_stream(ctx, stop, counter):
    while not stop.is_set():
        prompt = [rng.randint(TMIN, TMAX) for _ in range(ctx)]
        body = json.dumps({"model": MODEL, "prompt": prompt, "max_tokens": 1, "temperature": 0}).encode()
        req = urllib.request.Request(f"{EP}/v1/completions", data=body, headers={"Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            urllib.request.urlopen(req, timeout=3600).read()
            counter.append((t0, time.monotonic(), ctx))
        except Exception as e:
            print(f"  prefill request failed: {e}", flush=True); time.sleep(1)

def pct(xs, q): return sorted(xs)[min(len(xs) - 1, int(q * len(xs)))]

for ctx, conc in CASES:
    res = [None] * conc
    stop = threading.Event(); pre = []
    pt = None
    if STREAM:
        pt = threading.Thread(target=prefill_stream, args=(PCTX or ctx, stop, pre), daemon=True)
    th = [threading.Thread(target=stream_one, args=(ctx, OUT, res, i)) for i in range(conc)]
    t_start = time.monotonic(); [t.start() for t in th]
    if pt: pt.start()
    [t.join() for t in th]; wall = time.monotonic() - t_start
    if pt: stop.set(); pt.join(timeout=3600)
    ok = [r for r in res if r and len(r[1]) >= 3]
    if len(ok) < conc:
        print(f"CASE ctx={ctx} conc={conc}: only {len(ok)} of {conc} requests completed", flush=True)
    if not ok:
        continue
    first_tokens = [r[1][0] for r in ok]; last_tokens = [r[1][-1] for r in ok]
    w0, w1 = max(first_tokens), min(last_tokens)
    ttft = [r[1][0] - r[0] for r in ok]
    all_itl = [b - a for r in ok for a, b in zip(r[1][1:], r[1][2:])]
    win_itl = [b - a for r in ok for a, b in zip(r[1][1:], r[1][2:]) if a >= w0 and b <= w1]
    n_win_tokens = sum(1 for r in ok for s in r[1] if w0 < s <= w1)
    med_all = statistics.median(all_itl) * 1e3
    if len(win_itl) >= 10 and w1 > w0:
        med = statistics.median(win_itl) * 1e3; mean = statistics.mean(win_itl) * 1e3; p90 = pct(win_itl, 0.9) * 1e3
        tps = n_win_tokens / (w1 - w0)
        print(f"CASE ctx={ctx} conc={conc}: TTFT median={statistics.median(ttft):.1f}s max={max(ttft):.1f}s | steady window {w1-w0:.1f}s, "
              f"{n_win_tokens} tokens | step median={med:.2f} ms mean={mean:.2f} p90={p90:.2f} | decode {tps:.0f} tok/s "
              f"(= {tps*(ctx+OUT)/OUT/1e3:.0f}k tok/s with prompt tokens credited) | whole-run median={med_all:.2f} ms", flush=True)
        print(f"RESULT ctx={ctx} conc={conc} out={OUT} steady_ms={med:.3f} steady_mean_ms={mean:.3f} steady_p90_ms={p90:.3f} "
              f"window_s={w1-w0:.1f} decode_tps={tps:.1f} whole_ms={med_all:.3f} ttft_med_s={statistics.median(ttft):.2f}", flush=True)
    else:
        print(f"CASE ctx={ctx} conc={conc}: no steady window (last first-token at +{w0-t_start:.1f}s, first finish at +{w1-t_start:.1f}s); "
              f"raise OUT. whole-run median={med_all:.2f} ms", flush=True)
        print(f"RESULT ctx={ctx} conc={conc} out={OUT} steady_ms=nan whole_ms={med_all:.3f}", flush=True)
    if STREAM:
        inwin = [p for p in pre if p[0] >= w0 and p[1] <= w1]
        anyp = [p for p in pre if p[1] > w0 and p[0] < w1]
        ptoks = sum(p[2] for p in inwin);
        print(f"  prefill stream: {len(pre)} prompts total, {len(inwin)} fully inside window -> {ptoks/(w1-w0) if w1>w0 else float('nan'):.0f} prefill tok/s; "
              f"{len(anyp)} overlapping window; mean prompt latency {statistics.mean([p[1]-p[0] for p in pre]) if pre else float('nan'):.1f}s", flush=True)
        print(f"RESULT_PREFILL ctx={ctx} conc={conc} prefill_tps={ptoks/(w1-w0) if w1>w0 else float('nan'):.1f} prompts={len(pre)}", flush=True)
