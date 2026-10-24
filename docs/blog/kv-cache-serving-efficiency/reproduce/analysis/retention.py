#!/usr/bin/env python3
"""Retention-capped closed-loop ceiling for the B200 agentic fleet.
A replica holds S sessions; each cycles: engine time (O decode steps of the step-time law, prefill spread into them)
then a tool pause T_p during which its KV stays in HBM. Closed loop: S = n + lam*T_p, lam = n/(O*t(n)).
R_ret(h) = throughput at S = S_max = C/L_held (all held sessions' KV in HBM), capped by R(h) (compute/bandwidth walls)."""
import sys, io, contextlib, json, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stdout(io.StringIO()):
    from ceilings import HW, wread, N_act
    from kvmodel import Replica
P = HW['B200 v0.28.0']; b = P['b']; W = P['W']; C = P['C']; G = P['g']; TB = 8192; FLOOR = 0.0058
def step(I, O, h, n):
    """step time with n decoding sequences at hit rate h (prefill spread evenly: nU/O prefill tokens per step)"""
    U = (1 - h) * I; L = I + O / 2; Ftok = P['R'] * (I / 8192) ** P['p']; pre = n * U / O
    t_mem = (wread(n + pre) + n * L * b + (pre / TB) * L * b) / W; t_cmp = (n + pre) / Ftok
    return max(t_mem, t_cmp, FLOOR)
def closed_loop(I, O, h, S, Tp):
    """solve S = n + lam*Tp with lam = n/(O t(n)); return per-GPU tok/s, n, step"""
    lo, hi = 1e-6, S
    for _ in range(80):
        n = (lo + hi) / 2; lam = n / (O * step(I, O, h, n))
        if n + lam * Tp > S: hi = n
        else: lo = n
    n = lo; t = step(I, O, h, n); lam = n / (O * t)
    return lam * (I + O) / G, n, t
def R_open(I, O, h):
    F = P['R'] * 2 * N_act * (I / 8192) ** P['p']; n = 20
    for _ in range(6):
        rep = Replica('b', N_act, b, wread(n), F, W, C, 8192); lam, why, t = rep.max_rate(I, O, h); n = max(1, lam * O * t)
    return lam * (I + O) / G
if __name__ == '__main__':
    runs = json.load(open(sys.argv[1]))
    for r in runs:
        I, O, h, Tp, S_meas = r['I'], r['O'], r['h'], r['Tp'], r['S_eff']
        pred, n, t = closed_loop(I, O, h, S_meas, Tp)
        Smax = C / (I + O)
        print(f"{r['name']:14s} S_eff/rep {S_meas:5.1f}  Tp {Tp:4.1f}s | model: n {n:4.1f} step {t*1000:5.1f}ms {pred/1000:6.2f}k/GPU | measured: n {r['n_run']:4.1f}(+{r['n_wait']:.1f} wait) {r['tps_gpu']/1000:6.2f}k/GPU  ratio {r['tps_gpu']/pred:5.2f} | S_max {Smax:4.1f}")
