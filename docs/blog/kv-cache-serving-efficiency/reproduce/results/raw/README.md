# Raw run data behind the post

The per-request records and engine counters for the nine fleet runs of Section 3.3, in a compact form that holds every field the analysis uses and no prompt or response text. `analysis/compact_records.py` produced them from the harness output and the counter samples; `analysis/round2_inputs.py` reads them when the harness JSON is not present, so every factor in the post can be recomputed from this folder alone.

## Per-request records: `<arm>/conc_<N>.csv.gz`

One row per request of that run. Arms: `llmd_w32` (llm-d, 120 to 300 sessions), `kvrouter_w32` (the KV-load router, 60 to 120), `rr_w32` (round-robin, 90 and 120). Times are seconds on the harness clock, relative to the run's first request.

| Column | Meaning |
| --- | --- |
| `conversation`, `turn` | the replayed conversation and its turn number, as the harness labelled them |
| `start_s`, `end_s` | request sent, response complete |
| `first_token_s`, `last_token_s` | first and last output token received |
| `created_epoch` | the server's `created` timestamp of the first response chunk (epoch seconds); its difference from `first_token_s` maps the harness clock onto the counter samples |
| `input_tokens` | prompt tokens as the harness counted them |
| `prompt_tokens`, `completion_tokens` | the server's usage report |
| `max_tokens` | the request's output length (the harness's own output count is wrong on about one turn in 200, so the post uses this) |
| `error` | 1 if the request failed (disconnect); the analysis drops these |

`runs.csv` lists each run's epoch window, seed and request count.

## Engine counters: `counters.csv.gz`

The Prometheus counters and gauges of every decode replica, sampled every 15 s, over each run's window plus two minutes either side: `vllm:*` totals and gauges (prompt and generation tokens, prefix-cache queries and hits in HBM and in the host tier, iteration counts, running and waiting requests, KV usage, preemptions, host-tier load and store bytes) and the per-GPU driver readings (`smi:*`). Histogram buckets are left out. Pod names are replaced by stable aliases: `decode-fleet-<k>` for the six replicas that served the llm-d and round-robin runs, `kvrouter-decode-<k>` for the replicas behind the KV-load router.

## Recomputing

```bash
RESULTS=results python3 analysis/round2_inputs.py results/round2_inputs.json   # per-run inputs from results/raw
python3 analysis/ceiling_v3.py results/round2_inputs.json                      # ceiling, eta and the four-factor split
```
