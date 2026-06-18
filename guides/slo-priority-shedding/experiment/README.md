# SLO Priority-Shedding Experiment — runbook

Design and rationale: [DESIGN.md](DESIGN.md).

Artifacts:

| Path | Purpose |
|---|---|
| `configs/warmup.yaml` | Phase 0 — train the predictor (no SLO headers, nothing shed) |
| `configs/calibrate.yaml` | Phase 0.5 — concurrency ramp to find C_sat + set the SLO |
| `configs/high-priority.yaml` | protected class (objective `high-priority` + SLO headers) |
| `configs/low-priority.yaml` | sheddable class (objective `low-priority` + SLO headers) |
| `run-phase.sh` | render a config + run it as an in-cluster inference-perf Job |
| `snapshot-metrics.sh` | dump EPP SLO/queue/shed metrics for per-phase diffs |
| `results/` | metric snapshots + (to be added) parsed run outputs |

All Jobs run **in-cluster** against the EPP ClusterIP (`predicted-latency-routing-epp:80`)
using `quay.io/inference-perf/inference-perf`, with the `llm-d-hf-token` secret for the
tokenizer. Env knobs are documented at the top of `run-phase.sh`.

## Procedure

```bash
cd guides/slo-priority-shedding/experiment

# Phase 0 — warm the predictor (also validates the harness end-to-end)
./run-phase.sh configs/warmup.yaml exp-warmup --wait
# Gate: confirm predictions exist and track actual TTFT (see "Predictor gate" below).

# Phase 0.5 — calibrate: measure TTFT/TPOT vs concurrency
./run-phase.sh configs/calibrate.yaml exp-calibrate --wait
# From the per-stage summary pick:
#   SLO_TTFT_MS = idle p50 TTFT * margin (e.g. 1.5-2x)
#   SLO_TPOT_MS = idle p50 TPOT * margin
#   CONC_HIGH (sustainable, < C_sat), CONC_LOW (flood, >> C_sat)
export SLO_TTFT_MS=... SLO_TPOT_MS=...

# Phase 1 — baseline (control): both classes below saturation
./snapshot-metrics.sh baseline-pre
CONC_HIGH=8  NREQ_HIGH=96  NUM_CONVERSATIONS=16 ./run-phase.sh configs/high-priority.yaml exp-base-high
CONC_LOW=8   NREQ_LOW=96   NUM_CONVERSATIONS=16 ./run-phase.sh configs/low-priority.yaml  exp-base-low
kubectl wait --for=condition=complete --timeout=1800s job/exp-base-high job/exp-base-low -n llm-d-predicted-latency
./snapshot-metrics.sh baseline-post
# Expect ~0% shed (429) for both classes.

# Phase 2 — saturated (asymmetric "protect premium")
./snapshot-metrics.sh saturated-pre
CONC_HIGH=8   NREQ_HIGH=96  NUM_CONVERSATIONS=16 ./run-phase.sh configs/high-priority.yaml exp-sat-high
CONC_LOW=64   NREQ_LOW=512  NUM_CONVERSATIONS=64 ./run-phase.sh configs/low-priority.yaml  exp-sat-low
kubectl wait --for=condition=complete --timeout=1800s job/exp-sat-high job/exp-sat-low -n llm-d-predicted-latency
./snapshot-metrics.sh saturated-post
# Expect: low-priority shed >> 0, high-priority shed ~0, high-priority TTFT p90 <= SLO.
```

Collect each Job's result with `kubectl logs job/<name> -n llm-d-predicted-latency`
(inference-perf prints a per-stage + summary table including request/error counts and
latency percentiles; 429s show as errors). Parsed analysis + plots are added once the
first run confirms the exact output/log format.

## Predictor gate (run after Phase 0)

```bash
kubectl run pg --rm -i --restart=Never -n llm-d-predicted-latency --image=curlimages/curl:8.10.1 --command -- \
  sh -c 'curl -sS http://predicted-latency-routing-epp:9090/metrics' \
  | grep -E 'predicted_ttft|ttft_prediction_duration|request_ttft_seconds'
```
Proceed only when prediction samples are non-zero and predicted ≈ actual TTFT.

## Notes / risks

- **⚠️ Prefix-cache contamination across runs (the big gotcha).** vLLM's prefix cache
  persists across EPP restarts and across experiment runs — it is only cleared by
  restarting the **model server** (vLLM) pods. So if any run reuses a seed that an earlier
  run already sent, those prompts hit the cache: TTFT is artificially low, the pool looks
  faster than it is, and (during warmup) the predictor trains on cache-HIT latencies and
  then under-predicts on real traffic. Two ways to avoid it — pick one per run:
    1. **Fresh seed every run (default here).** `run-phase.sh` defaults `SEED` to a
       timestamp; the measured phases use distinct fixed seeds per (phase × class)
       (baseline 1001/1002, saturated 2001/2002), all different from warmup/calibration/
       flood. A new seed ⇒ new conversations ⇒ cache-miss, no vLLM restart needed.
    2. **Restart vLLM before each new experiment.** `kubectl rollout restart deploy/
       mistral-medium-decode -n llm-d-predicted-latency` (then wait for all pods Ready and
       re-warm). This flushes the prefix cache so even a reused seed is cache-miss. Slower
       (full model reload), but lets you keep deterministic seeds across repeated experiments.
- **Re-warm + re-check the predictor gate after ANY EPP restart** (`helm upgrade` that rolls
  the pod, or `rollout restart`): the EPP restart resets the predictor's XGBoost models to
  cold even though vLLM keeps serving. Do NOT restart the EPP *between* baseline and
  saturated, or you reset the predictor mid-experiment.
- **Two admission paths shed `priority<0`.** `latency-slo-admitter` (SLO-based) AND the
  legacy `utilization-detector` (KV≥80% OR queue≥5). To attribute shedding solely to the
  SLO, raise the utilization thresholds (`kvCacheUtilThreshold: 1.0`, `queueDepthThreshold:
  100`) as this guide's router values do.
- **`poolRef.group` must match the InferencePool's group** (`inference.networking.k8s.io`),
  or objective priority silently resolves to 0 and nothing is ever shed.
- H100 nodes are **spot** — re-check `kubectl get pods` between phases; a preemption
  mid-phase invalidates that phase.
- The concurrency numbers above are **starting points** — replace `CONC_*`/`NREQ_*` with
  values derived from the Phase 0.5 ramp.
- The admitter shed-counter metric name is captured during the first SLO-tagged run and
  added to `snapshot-metrics.sh`'s grep if useful.
