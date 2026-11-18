# TPU7x calibration (Appendix A, third column)

One replica of Qwen3-Coder-480B-A35B-FP8 at TP=16 across two tpu7x hosts (8 chips, 16 TensorCore devices), vLLM TPU v0.27.0, fp8 KV, 8k chunk, 64 sequences, 0.80 memory utilisation, 256-token blocks, host KV tier. Measured 18 November 2026.

- `prefill_8k.log`, `prefill_32768.log`, `prefill_131072.log`: the llm-d router calibration script at the three prompt lengths (23,573 / 20,591 / 12,937 tokens per second for the replica).
- `ladder.log`: the steady-batch decode ladder (`calibration/steady_ladder.py`) at 116,684 tokens of context, batches 1 to 8, plus a single decode at 1k. Fit: 18.1 ms fixed plus bytes at 49.8 TB/s.
- `kv.txt`: the engine's KV allocation line (1,350,144 tokens). `cases.txt`: the rungs the capacity cap allowed.
- `inputs.json`: the ceiling inputs in the form `analysis/ceiling_v3.py` takes; `kv_replication` 2 because sixteen devices share eight KV heads.

The interference rung was not run on this replica.
