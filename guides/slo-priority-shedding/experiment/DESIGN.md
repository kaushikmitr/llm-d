# Experiment: SLO-Aware Priority Shedding

Quantify that, under saturation, the EPP `latency-slo-admitter` **sheds low-priority
(sheddable) requests** while **high-priority requests keep being served within SLO**.

## Hypothesis

Given the 3×TP8 Mistral-Medium-3.5-128B pool driven by a realistic multi-turn agentic
workload, when offered load saturates the pool such that no endpoint is predicted to meet
the request SLO, the EPP:

- **sheds** `low-priority` (priority −10) requests at admission (HTTP `429`,
  `x-llm-d-request-dropped-reason`), and
- **admits and serves** `high-priority` (priority 10) requests with TTFT/TPOT near SLO.

Below saturation, neither class is shed (control).

## Workload (held constant across classes)

The agentic `conversation_replay` profile from llm-d-benchmark
([`guide_predicted-latency-routing_1.yaml`](https://github.com/llm-d/llm-d-benchmark/blob/main/workload/profiles/inference-perf/guide_predicted-latency-routing_1.yaml)):
multi-turn conversations, 3000-token shared system prompt + a 15K–100K per-conversation
system prompt reused across turns, ~6 turns/conversation, ~1500 input / ~800 output tokens
per turn, streaming. Heavy cross-turn KV-cache reuse — the regime predicted-latency routing
targets. **Both priority classes draw this identical distribution**; only the
`x-llm-d-inference-objective` header differs.

## Harness

Two **inference-perf** instances run concurrently (it natively implements
`conversation_replay` and supports static `api.headers`), differing only in headers:

| Instance | `x-llm-d-inference-objective` | SLO headers | Load |
| --- | --- | --- | --- |
| **high** | `high-priority` | `x-llm-d-slo-ttft-ms`, `x-llm-d-slo-tpot-ms` | sustainable concurrency |
| **low** | `low-priority` | same SLO headers | flood (oversubscribe) |

Each instance records per-request: HTTP status, TTFT, end-to-end latency, output tokens.
Sheds = `429` count per instance. Cross-checked against EPP metrics
(`inference_objective_request_ttft_slo_violation_total`, admitter shed counters,
`inference_pool_average_queue_size`) scraped via GMP.

> **Closed-loop caveat.** `conversation_replay` is inherently closed-loop (turns depend on
> prior responses), so a shed turn reduces that conversation's subsequent offered load. We
> therefore report **shed fraction = 429 / attempted requests per class**, not an absolute
> open-loop shed rate. The asymmetric design keeps the qualitative result robust:
> low-priority sees many 429s, high-priority sees ~none.

## Phases

| # | Name | Purpose | Load | Pass criteria |
| --- | --- | --- | --- | --- |
| 0 | **Warmup** | train the XGBoost predictor | high-prio only (or no SLO), moderate concurrency, ~few hundred reqs | predictor produces predictions; predicted TTFT tracks actual within a few % |
| 0.5 | **Calibrate** | find pool capacity + set SLO | high-prio only, concurrency ramp (e.g. 4→8→16→32→48) | identify C_sat (concurrency where TTFT/TPOT SLO starts breaking); set SLO = idle p50 + margin |
| 1 | **Baseline** | control: headroom ⇒ no shedding | high+low each at low concurrency (< C_sat total) | **shed ≈ 0% both classes**; both meet SLO |
| 2 | **Saturated** | headline: protect premium | high-prio at sustainable rate **+ low-prio flood** (≫ C_sat) | **low-prio shed ≫ 0 (target > 80%)**, **high-prio shed ≈ 0%**, **high-prio TTFT p90 ≤ SLO** |
| 3 | **Recovery** *(opt)* | sheddable recovers | drop low-prio flood back to baseline | low-prio admitted again |

SLO thresholds and the per-phase concurrency levels are **derived from Phase 0.5**, not
guessed (auto-calibration).

## Why these controls matter

- **Predictor warmth** — the admitter acts on `predicted-latency-producer` output; a cold
  predictor sheds ~randomly. Phase 0 + the convergence gate remove this confound.
- **Identical workload per class** — only the objective header differs, so shedding is
  attributable to priority, not prompt mix.
- **Auto-calibrated SLO** — set from measured idle/saturated latency so it is genuinely
  "met when idle, violated when saturated."
- **Spot risk** — H100 nodes are spot; keep runs short and re-check pod health between
  phases (a preemption mid-run invalidates that phase).

## Outputs

- Per-phase, per-class table: attempted, served (200), shed (429), shed %, TTFT p50/p90,
  TPOT p50/p90.
- Plots: shed % by class × phase; high-prio TTFT p90 vs SLO line across phases; offered vs
  served over time.
- EPP metric deltas per phase (SLO violations, shed counters, queue size).

## Success statement

The experiment **passes** if Phase 1 shows ~0% shedding for both classes while Phase 2
shows high low-priority shedding with ~0% high-priority shedding and high-priority TTFT p90
at or below the SLO — i.e. premium traffic is protected by shedding sheddable traffic.
