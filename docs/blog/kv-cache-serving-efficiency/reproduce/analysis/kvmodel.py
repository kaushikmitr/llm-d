"""Iteration-level serving model: one continuous-batching step holds both
prefill chunk tokens and decode tokens. Finds, per (I, O, h), the largest
sustainable request rate per replica and which resource binds.

Resources: F (prefill FLOP/s, calibrated), W (HBM bandwidth, effective),
C (HBM bytes for KV), T_b (prefill token budget per iteration), B (link).
"""
import math, json, sys

GiB = 1024**3

class Replica:
    def __init__(self, name, n_params, kv_bytes_per_tok, weight_bytes, F, W, C_tokens, T_b):
        self.name = name
        self.N = n_params           # active params (FLOPs = 2N per token)
        self.b = kv_bytes_per_tok
        self.Wb = weight_bytes
        self.F = F                  # effective prefill FLOP/s
        self.W = W                  # effective HBM bytes/s
        self.C = C_tokens * kv_bytes_per_tok
        self.T_b = T_b

    def iter_time(self, lam, I, O, h):
        """Steady-state iteration time at rate lam (req/s). Returns (t, feasible, why)."""
        A = 2 * self.N * lam * (I * (1 - h) + O) / self.F
        ctx = I + O / 2
        Bc = lam * self.b * (O * ctx + I * (1 - h) * I / max(self.T_b, 1)) / self.W
        w0 = self.Wb / self.W
        if A >= 1:
            return None, False, "compute"
        if Bc >= 1:
            return None, False, "bandwidth"
        t = w0 / (1 - Bc)
        return t, True, None

    def feasible(self, lam, I, O, h):
        t, ok, why = self.iter_time(lam, I, O, h)
        if not ok:
            return False, why
        ctx = I + O / 2
        n_dec = lam * O * t
        n_pre = lam * I * (1 - h) * t
        kv = n_dec * self.b * ctx + lam * (I * (1 - h) / max(self.T_b, 1)) * t * self.b * I / 2
        if kv > self.C:
            return False, "capacity"
        if n_pre > self.T_b:
            return False, "prefill-budget"
        return True, None

    def max_rate(self, I, O, h):
        lo, hi = 0.0, 1.0
        while self.feasible(hi, I, O, h)[0]:
            hi *= 2
            if hi > 1e6:
                break
        why = self.feasible(hi, I, O, h)[1]
        for _ in range(60):
            mid = (lo + hi) / 2
            ok, w = self.feasible(mid, I, O, h)
            if ok:
                lo = mid
            else:
                hi, why = mid, w
        t, _, _ = self.iter_time(lo, I, O, h)
        return lo, why, t

    def bounds(self, I, O, h):
        """Independent per-resource bounds (req/s), for explanation."""
        lam_c = self.F / (2 * self.N * (I * (1 - h) + O))
        ctx = I + O / 2
        lam_w = self.W / (self.b * (O * ctx + I * (1 - h) * I / max(self.T_b, 1)))
        return lam_c, lam_w


# ---- Qwen3-32B on 2x H100 (TP=2), calibrated ------------------------------
N = 32.8e9
b_qwen = 2 * 64 * 8 * 128 * 2          # 262,144 B/token (bf16)
F_cal = 15928 * 2 * N                  # from peakPrefillThroughput
W_eff = 2 * 3.35e12 * 0.7
C_tok = 2_384_000 / 8                  # per replica, from the tiered guide
qwen = Replica("Qwen3-32B H100 TP2 (calibrated)", N, b_qwen, 2 * N, F_cal, W_eff, C_tok, 8192)
qwen_fp = Replica("Qwen3-32B H100 TP2 (first principles, 55% MFU)", N, b_qwen, 2 * N,
                  2 * 989e12 * 0.55, W_eff, C_tok, 8192)

print(f"calibrated F = {F_cal/1e15:.2f} PFLOP/s per replica = {F_cal/(2*989e12)*100:.0f}% of 2x H100 dense bf16 peak")
print(f"KV bytes/token Qwen3-32B = {b_qwen/1024:.0f} KiB; C per replica = {qwen.C/GiB:.0f} GiB = {C_tok:,.0f} tokens")

# ---- Validation 1: tiered-prefix-cache guide (I=16256, O=256, 8 replicas) ----
print("\n== Tiered-cache validation (8 replicas, I=16,256, O=256) ==")
for h, label, measured in [(0.0, "HBM-only, LRU thrash (h~0)", 115_645), (0.79, "HBM+CPU tier (h~0.79)", 331_213)]:
    lam, why, t = qwen.max_rate(16256, 256, h)
    lam_c, lam_w = qwen.bounds(16256, 256, h)
    print(f"{label:30s} max {lam*8:6.1f} req/s fleet -> {lam*8*16512/1000:6.0f}k tok/s  binds on {why:14s} "
          f"(compute bound {lam_c*8:5.1f}, bandwidth bound {lam_w*8:5.1f}) iter {t*1000:.1f} ms   measured {measured/1000:.0f}k tok/s")

# ---- Validation 2: P2P pull vs recompute ladder, gpt-oss-120b H200 TP1 ------
print("\n== P2P ladder (gpt-oss-120b, H200 TP1) ==")
b_oss = 2 * 36 * 8 * 64 * 2            # 73,728 B/token
R_peak = 39065                          # H100 TP1 calibrated; H200 similar compute
ladder = [(2048, 78.3, 34.6), (8192, 249.9, 56.5), (16384, 510.3, 85.9), (32768, 1173.2, 165.2), (49152, 1987.7, 235.0)]
# fit pull = t0 + bytes / B_eff from the two end points
L0, _, p0 = ladder[0]; L1, _, p1 = ladder[-1]
B_eff = (L1 - L0) * b_oss / ((p1 - p0) / 1000)
t0 = p0 / 1000 - L0 * b_oss / B_eff
print(f"pull model: t0 = {t0*1000:.0f} ms setup, effective link {B_eff/1e9:.1f} GB/s")
for L, rc, pl in ladder:
    rc_model = L / R_peak * 1000
    pl_model = (t0 + L * b_oss / B_eff) * 1000
    print(f"  {L:6d} tok  recompute meas {rc:7.1f} model(linear) {rc_model:7.1f} ms   pull meas {pl:6.1f} model {pl_model:6.1f} ms")
cross = t0 / (1 / R_peak - b_oss / B_eff)
print(f"  crossover (pull cheaper above) ~ {cross:,.0f} tokens; guide sets minCachedTokenDelta 2048")

# ---- Phase map ---------------------------------------------------------------
Is = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072]
Os = [64, 128, 256, 512, 1024, 2048, 4096]
def phase(rep, h):
    rows = []
    for O in Os:
        for I in Is:
            lam, why, t = rep.max_rate(I, O, h)
            rows.append({"I": I, "O": O, "h": h, "rate": round(lam, 3), "binds": why, "tpot_ms": round((t or 0) * 1000, 1)})
    return rows
maps = {}
for h in (0.0, 0.5, 0.9):
    rows = phase(qwen, h)
    maps[h] = rows
    print(f"\n== Phase map, h = {h} (cells: binding resource, max req/s per replica) ==")
    print("O\\I   " + "".join(f"{I:>9d}" for I in Is))
    for O in Os:
        line = f"{O:5d} "
        for I in Is:
            r = next(x for x in rows if x["I"] == I and x["O"] == O)
            tag = {"compute": "C", "bandwidth": "W", "capacity": "M", "prefill-budget": "B"}[r["binds"]]
            line += f"{tag}{r['rate']:8.2f}"
        print(line)

# ---- Agentic turn growth: intrinsic overlap and retention --------------------
print("\n== Agentic sessions: intrinsic overlap rho and achieved h vs retention ==")
I0, q, O, K = 20000, 2000, 500, 20     # system+repo context, new tokens per turn, output per turn, turns
inputs = [I0 + k * (q + O) for k in range(K)]
overlap = sum(inputs[k - 1] + O for k in range(1, K))
rho = overlap / sum(inputs)
print(f"session: I0={I0}, +{q} new and {O} output per turn, {K} turns -> rho = {rho:.3f}, final context {inputs[-1]+O:,} tokens")
gap = 30.0                              # seconds between turns (tool call)
avg_ctx = sum(inputs) / K + O / 2
for sessions in (50, 100, 200, 400, 800):
    idle_tokens = sessions * avg_ctx    # all sessions resident at once in steady state
    for tier, cap_tokens in (("HBM (8 replicas)", 2_384_000), ("HBM + 200 GiB CPU/replica", 2_384_000 + 8 * 200 * GiB / b_qwen)):
        retained = min(1.0, cap_tokens / idle_tokens)
        print(f"  {sessions:4d} concurrent sessions, {tier:28s}: working set {idle_tokens/1e6:5.1f}M tok, "
              f"retained {retained*100:5.1f}% -> h = {rho*retained:.2f}")

json.dump({str(k): v for k, v in maps.items()}, open(sys.argv[1] if len(sys.argv) > 1 else "/dev/null", "w"))
