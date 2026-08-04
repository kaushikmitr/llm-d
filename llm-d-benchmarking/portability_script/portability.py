#!/usr/bin/env python3
"""
Compute τ_sat = peakPrefillThroughput × T_max for the §7.4 portability table.

Every row is a measured serving path; sources:
- 'anchored' — our single-request TTFT fit (§7.3): R_peak = B / T(B) = 8192 / 0.40s.
- 'matrix'   — measured peakPrefillThroughput values distributed with the router
               (guides/recipes/router/calibration/configuration-matrix.md).
- 'guide'    — the agentic-serving guide's TPU v7x calibration.
- 'fleet'    — production Vertex AI B200 fleet calibration (GLM-5.2-NVFP4,
               SGLang with EAGLE spec-decode, 8×B200 per pod, 32k chunk):
               ten ~32k-token cache-miss prefills through the full gateway
               path, median TTFT 1.369s.

Matrix/guide/fleet values are measured through the full serving path (median
TTFT of repeated requests) and read lower than a single-request fit of the
same path (15,928 vs 20,480 on the reference H100 path), so τ values derived
from them are conservative.

estimate_T_B() below retains the FLOPs model used before these measurements
existed; it can seed a starting value for hardware with no measurement yet.
"""

T_MAX = 14.0   # seconds, operator's TTFT degradation tolerance
B = 8192       # max-num-batched-tokens on the reference paths

MEASURED = [
    # (path, engine, tp, peakPrefillThroughput tok/s, source)
    ('gpt-oss-120B / H100',            'vLLM',   1, 39065, 'matrix'),
    ('Qwen3-32B / TPU v7x',            'vLLM',   8, 27336, 'matrix'),
    ('Qwen3-32B / TPU v6e',            'vLLM',   8, 26290, 'matrix'),
    ('GLM-5.2-NVFP4 / 8x B200',        'SGLang', 8, 24027, 'fleet'),
    ('Qwen3-32B / H100 (anchored)',    'vLLM',   2, 20480, 'anchored'),
    ('Qwen3-Coder-480B-FP8 / TPU v7x', 'vLLM',   8, 16444, 'guide'),
    ('Qwen3-VL-32B / H200',            'vLLM',   2, 15751, 'matrix'),
    # Corrected: the matrix currently publishes 30720 for this path, 2x too high.
    ('Qwen3-32B / H100',               'SGLang', 2, 15360, 'matrix'),
]


# --- FLOPs-based seeding estimate (pre-measurement fallback only) ---

hardware_tflops = {   # vendor peak bf16 TFLOPS
    'H100': 989,
    'H200': 989,
    'B200': 2200,
    'A100': 312,
    'MI300X': 1307,
    'TPU v5e': 197,
    'TPU v5p': 459,
    'TPU v6e': 918,
}

# Combined MFU × collective-overhead per TP degree (conservative).
tp_efficiency = {1: 0.60, 2: 0.55, 4: 0.48, 8: 0.38}


def estimate_T_B(model_params_b: float, hardware: str, tp: int, B: int = 8192) -> float:
    """Predicted single-chunk prefill wall time, in seconds."""
    peak = hardware_tflops[hardware]
    total_flops = 2 * (model_params_b * 1e9) * B
    eff = tp_efficiency.get(tp, 0.60 * (0.85 ** (tp // 2)))
    return total_flops / (peak * tp * eff * 1e12)


if __name__ == '__main__':
    print(f"T_max = {T_MAX}s\n")
    print(f"{'Path':<32} {'Engine':<7} {'TP':>3} {'R_peak':>8} {'τ_sat':>10}")
    print('-' * 66)
    for path, engine, tp, r_peak, source in MEASURED:
        tau = int(r_peak * T_MAX)
        print(f"{path:<32} {engine:<7} {tp:>3} {r_peak:>7,}  {tau:>9,}")
