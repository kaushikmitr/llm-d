# SLO-Aware Priority Shedding (Mistral-Medium-3.5-128B)

> Status: **working setup dump.** This guide records a live deployment on GKE
> (`kaushikmitra-gke-dev`, cluster `vllm-cluster22`, namespace `llm-d-predicted-latency`).
> The benchmark/experiment that quantifies the shedding behavior is **not yet included** —
> see [Experiment (TODO)](#experiment-todo).

## What this demonstrates

Attach a per-request latency **SLO** to every request and assign each a **priority tier**.
When the pool saturates and no endpoint can still meet the SLO, the router **sheds the
low-priority (sheddable) requests at admission** while **high-priority requests keep being
served**. This protects premium traffic instead of letting everyone degrade equally.

The mechanism is the llm-d Router's `latency-slo-admitter` plugin:

> *Sheddable requests (priority < 0) are rejected at admission when no endpoint can meet
> the SLO, rather than routed to a guaranteed miss.*

Routing itself stays load- and prefix-cache-aware (`token-load-scorer` +
`prefix-cache-affinity-filter`), and `predicted-latency-producer` supplies the per-endpoint
TTFT/TPOT predictions the admitter consumes.

## Topology

| Component | Value |
|---|---|
| Namespace | `llm-d-predicted-latency` |
| Router release | `predicted-latency-routing` (standalone, llm-d-router-standalone-dev `v0`) |
| Model | [`mistralai/Mistral-Medium-3.5-128B`](https://huggingface.co/mistralai/Mistral-Medium-3.5-128B) — dense 128B, `mistral3`, 256K context |
| Model server | vLLM nightly, **3 replicas × TP8 = 24 × H100-80GB** (the whole cluster), served BF16, `--max-model-len=262144` |
| Routing | `token-load-scorer` + `prefix-cache-affinity-filter` (`peakPrefillThroughput: 17327`) |
| QoS | `latency-slo-admitter` + `predicted-latency-producer` (`streamingMode: true`) |
| Priority tiers | `high-priority` (10, protected) / `low-priority` (-10, sheddable) |
| Metrics | GKE Managed Prometheus, EPP `/metrics` unauthenticated (`--metrics-endpoint-auth=false`) |

## Prerequisites

```bash
export GAIE_VERSION=v1.5.0
export ROUTER_CHART_VERSION=v0
export NAMESPACE=llm-d-predicted-latency
export RELEASE=predicted-latency-routing      # router release == InferencePool name
export MODEL_NAME=mistralai/Mistral-Medium-3.5-128B
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

- GAIE + llm-d.ai InferenceObjective CRDs installed (see other guides for the exact
  `kubectl apply` of the GAIE `v1-manifests.yaml`).
- `llm-d-hf-token` secret with key `HF_TOKEN` in `${NAMESPACE}` (the repo is **ungated**,
  but a token is still used to pull). 256K context + 24 H100s assumed available.

## 1. Deploy the Router (standalone)

```bash
helm install ${RELEASE} \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/slo-priority-shedding/router/slo-admitter.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

> [!IMPORTANT]
> **Predictor image gotcha.** The chart hardcodes the prediction-server entrypoint as
> `uvicorn llm_d_latency_predictor.prediction_server:app`. That matches the **canonical
> ghcr** images this guide pins (`ghcr.io/llm-d/llm-d-latency-predictor-*-dev:latest`).
> Do **not** swap in the `k8s-staging-images/.../latency-prediction-server:main` image —
> it ships a top-level `prediction_server.py`, so the hardcoded arg fails with
> `No module named 'llm_d_latency_predictor'` and the prediction-server CrashLoops
> (EPP shows 2/4 → 4/4 never reached). The values file already pins the correct images.

## 2. Deploy the Model Server (Mistral-Medium-3.5-128B, 3×TP8)

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/slo-priority-shedding/modelserver/gpu/vllm/
```

This is a separate deployment (`mistral-medium-decode`) labeled
`llm-d.ai/guide=optimized-baseline` so the router/InferencePool selects it. Notes baked into
the overlay:

- vLLM **nightly** image (Mistral-Medium-3.5 / `mistral3` needs `mistral_common >= 1.11.1`,
  `transformers >= 5.4.0`).
- `strategy: Recreate` — 3×TP8 consumes all 24 GPUs, so a RollingUpdate surge pod could
  never schedule.
- `--gpu-memory-utilization=0.8`, `--max-num-batched-tokens=16384`, `--max-num-seqs=128`,
  Mistral tool/reasoning parsers (per the model card).

First start downloads the weights to an emptyDir, so the startup probe allows up to ~2h.

## 3. Apply the priority tiers

```bash
kubectl apply -f ${REPO_ROOT}/guides/slo-priority-shedding/objectives.yaml -n ${NAMESPACE}
```

Defines `high-priority` (priority 10, protected) and `low-priority` (priority -10,
sheddable). `poolRef.name` must equal `${RELEASE}`.

## 4. Enable GKE Prometheus monitoring (auth disabled)

The router values already set `monitoring.provider=gmp` + `prometheus.auth.enabled=false`
(EPP `/metrics` becomes unauthenticated). Apply the unauthenticated PodMonitoring:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/slo-priority-shedding/monitoring/gke/ \
  2>/dev/null || kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/slo-priority-shedding/monitoring/gke/podmonitoring.yaml
```

Verify (expect `HTTP 200`):

```bash
kubectl run curl-m --rm -i --restart=Never -n ${NAMESPACE} --image=curlimages/curl:8.10.1 --command -- \
  sh -c 'curl -sS -o /dev/null -w "%{http_code}\n" http://'${RELEASE}'-epp:9090/metrics'
```

## 5. (Re)calibrate `peakPrefillThroughput`

The committed value (`17327`) was measured for this exact model/hardware. If you change
either, recalibrate (`CHUNK_SIZE` must equal vLLM `--max-num-batched-tokens`):

```bash
cd ${REPO_ROOT}/guides/recipes/router/calibration
GUIDE_NAME=${RELEASE} NAMESPACE=${NAMESPACE} MODEL_NAME=${MODEL_NAME} CHUNK_SIZE=16384 ./calibrate.sh
```

Set the value in [`router/slo-admitter.values.yaml`](router/slo-admitter.values.yaml), then:

```bash
helm upgrade ${RELEASE} oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
  --reuse-values -f ${REPO_ROOT}/guides/slo-priority-shedding/router/slo-admitter.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
# A ConfigMap-only change does NOT roll the EPP — restart it explicitly:
kubectl rollout restart -n ${NAMESPACE} deployment/${RELEASE}-epp
```

## Send requests

Every request must be `"stream": true` and carry SLO header(s); priority comes from the
`x-llm-d-inference-objective` header.

```bash
export IP=$(kubectl get service ${RELEASE}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')

# High-priority (protected)
curl -X POST http://${IP}/v1/completions \
  -H 'Content-Type: application/json' \
  -H 'x-llm-d-inference-objective: high-priority' \
  -H 'x-llm-d-slo-ttft-ms: 5000' \
  -H 'x-llm-d-slo-tpot-ms: 80' \
  -d '{"model":"'${MODEL_NAME}'","prompt":"...","max_tokens":200,"stream":true,"stream_options":{"include_usage":true}}'

# Low-priority (sheddable): same call with `x-llm-d-inference-objective: low-priority`
```

Under saturation, low-priority calls are rejected at admission while high-priority calls
keep streaming.

> [!WARNING]
> **Trust boundary:** never let end users self-assert `x-llm-d-*` headers in production.
> Strip incoming `x-llm-d-*` at the ingress gateway and inject the authoritative
> objective/priority from validated token claims.

## Verify the behavior

- EPP metrics (`http://${RELEASE}-epp:9090/metrics`): `inference_pool_average_queue_size`,
  `llm_d_epp_per_endpoint_queue_size`, predicted vs actual TTFT, and SLO/admission counters
  (`inference_objective_request_ttft_slo_violation_total`, admitter shed counters) — the
  SLO/shed series appear only once SLO-tagged traffic flows.
- A healthy predictor: `inference_objective_request_ttft_prediction_duration_seconds` has
  non-zero samples, and predicted TTFT tracks actual after warmup. **A cold predictor gives
  bad predictions** — warm it with traffic before drawing conclusions.

## Experiment

A controlled experiment quantifying the shedding behavior is in
[`experiment/`](experiment/) — design in [experiment/DESIGN.md](experiment/DESIGN.md),
runbook in [experiment/README.md](experiment/README.md), and **results in
[experiment/RESULTS.md](experiment/RESULTS.md)**.

Headline (Mistral-Medium-3.5-128B, 3×TP8, agentic `conversation_replay`, SLO admitter as
sole shedder):

| Phase | high-priority shed | low-priority shed |
|---|---|---|
| Baseline (headroom) | 0% | 0% |
| Saturated (low conc 64) | **0%** | **42%** |
| Saturated (low conc 96) | **0%** | **69%** |

All sheds are 100% SLO-driven, high-priority TTFT median stays ~0.5 s vs 6–24 s for
low-priority, and shedding the best-effort flood frees capacity for the premium tier. See
RESULTS.md for latency tables, charts, mechanism, and caveats (high-priority *tail* latency
and the prefix-cache contribution are not fully SLO-guaranteed/isolated). Harness = two
header-differentiated `inference-perf` instances (it does support per-instance static
`api.headers`), one per priority class.

## Cleanup

```bash
helm uninstall ${RELEASE} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/slo-priority-shedding/objectives.yaml
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/slo-priority-shedding/modelserver/gpu/vllm/
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/slo-priority-shedding/monitoring/gke/podmonitoring.yaml
```
