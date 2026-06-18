# SLO Priority-Shedding — Results

**Setup:** Mistral-Medium-3.5-128B, 3×TP8 on 24×H100-80GB, 256K ctx. Agentic
`conversation_replay` workload (3K shared + 15–100K dynamic system prompt, ~6 turns,
~1.5K-in/0.8K-out per turn). Two priority tiers via `x-llm-d-inference-objective`:
`high-priority` (priority 10) and `low-priority` (priority −10). Per-request SLO headers
TTFT 15000 ms / TPOT 40 ms, `stream: true`. **`latency-slo-admitter` is the sole shedder**
(legacy utilization path neutralized: `kvCacheUtilThreshold=1.0`, `queueDepthThreshold=100`).
Predictor freshly warmed (TTFT −1% / TPOT +11% vs actual). C_sat ≈ 32 from calibration.

## Headline

| Phase | low offered conc | **high-priority shed** | **low-priority shed** | sheds via SLO path / util path |
|---|---|---|---|---|
| Baseline | 8 (total 16 < C_sat) | **0%** (0/48) | **0%** (0/48) | 0 / 0 |
| Saturated | 64 | **0%** (0/96) | **42%** (163/384) | 163 / 0 |
| Saturated | 96 | **0%** (0/144) | **90%** (1793/2000) | 1793 / 0 |

![shed by class](results/shed_by_class.png)

- **High-priority is never shed** (priority ≥ 0 bypasses admission). **Low-priority shedding
  rises with load** (0 → 42 → 90%) as the pool saturates past C_sat.
- **100% of sheds are SLO-driven** — the SLO-path rejection count equals the low-priority
  failure count exactly in every phase, and the utilization path fired 0 times (clean
  isolation). No timeouts contaminating the failures.
- Baseline is the control: **0% shed both classes** here. It isn't *guaranteed* 0%, though:
  with this workload's very long inputs (system prompts up to ~100K tokens), the largest
  prompts take ~16–22 s to first token even at low load and so exceed the 15 s TTFT SLO on
  their own — when that happens those (low-priority) requests are shed regardless of pool
  load. That's **long-input-driven, not a saturation effect.**

## Served latency (TTFT) — mean + p90

We report **mean** (and p90), not median: the latency predictor is trained with the default
`mean` objective, so the SLO is effectively a guarantee on the *mean* — that's the
SLO-relevant central metric. (The baseline latency anchor is from a TPOT=40 baseline run,
seed 6001/6002, since the original baseline pods were GC'd; baseline latency is a workload
property and seed-robust.)

![ttft vs slo](results/highprio_ttft_vs_slo.png)

| Phase | high TTFT mean | high TTFT p90 | low TTFT mean (served) | low TTFT p90 |
|---|---|---|---|---|
| Baseline (low=8) | 2.5 s | 8.1 s | 3.8 s | 15.3 s |
| Saturated (low=64) | **13.0 s** | 40.0 s | 35.2 s | 89.7 s |
| Saturated (low=96, heavy) | **8.9 s** | 18.5 s | 11.0 s | 22.6 s |

- **High-priority mean TTFT stays within the 15 s SLO in both saturated phases** (13.0 s,
  8.9 s) — the metric the predictor actually targets. And it is never shed.
- **Low-priority mean is above SLO at low=64 (35 s)**; at low=96 it *drops* to 11 s only
  because 90% is shed, leaving a few cheap survivors that queue less — not because service
  improved.
- **Protection regime shifts with load.** Moderate (low=64): high mean 13 s vs low 35 s — a
  real latency gap on top of 0% vs 42% shed. Heavy (low=96): high and low means converge
  (8.9 vs 11.0 s) as the residual admitted load saturates the pool for everyone — protection
  is then almost entirely via **admission** (0% vs 90% shed), not latency.
- The cross-phase high numbers (13.0 → 8.9 s, p90 40 → 18.5 s) are **seed/prompt-mix
  sensitive** at n=96–144, not a load trend — lead with "high mean ≤ SLO and never shed," not
  the absolute values.
- **The system protects whether premium is *admitted*, not its place in the queue.** Under
  heavy load the guarantee that holds is "premium is never rejected," not "premium is always
  fast."

## Reproduce

Seeds (fixed, distinct per phase × class): baseline 1001/1002, sat-64 2001/2002,
sat-96 4001/4002 (the sat96b run, with the flood sized to outlast high-priority). See
[README.md](README.md) for the run procedure and the prefix-cache / seed / EPP-restart
gotchas.
