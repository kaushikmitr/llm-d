# Well-lit Path: Predicted Latency based Load Balancing

> **What problem does this solve?**  
> Modern LLM serving needs to meet per‑request **latency SLOs** (e.g., TTFT ≤ _X_ ms, TPOT ≤ _Y_ ms) while pools are under mixed, bursty load. Only looking at queue depth or KV‑cache utilization may **misroute** traffic, causing SLO violations (slow TTFT spikes, tail TPOT) or over‑conservative placement that wastes capacity.

> **How it works (high level)**  
> This guide enables **prediction‑based scheduling**: the EPP calls in‑pod **latency predictor sidecars** to estimate **p90 TTFT** and **p90 TPOT** for each candidate pod using live features (KV‑cache %, input length, waiting/running counts, prefix‑overlap score, etc.). The **SLO scorer** then routes requests to pods with **positive headroom** vs the request’s SLOs.  
> - Turn it on per request with the header **`x-prediction-based-scheduling: true`.  
> - If enabled **without SLOs**, it assumes SLO=0 and picks the **lowest‑latency** destination.  
> - Models are trained online from **streaming** observations; For observability, **TPOT is sampled every 200th token** and both predictions and actuals are surfaced at the end of the stream for validation.



This guide shows how to run the Endpoint Picker (EPP) with **one training** and **three prediction** sidecars, and how to enable **SLO‑aware routing** via the `plugins-config` profiles.

---

## What the sidecars do (and how they fit together)

EPP schedules requests. The two latency sidecar types sit **next to** EPP in the same Pod and provide prediction + training services that EPP calls over `localhost`:

### 1) Training sidecar (`training-server`, port **8000**)

**Purpose**
- Collects latency samples (TTFT, TPOT) sent by EPP during streaming.
- Periodically **re‑trains** the TTFT/TPOT models when enough new samples arrive.
- Publishes fresh model artifacts for predictors to fetch.

**API**
- `POST /sample` – ingest latency samples (bulk buffered by EPP).
- `GET  /model/{name}/info` – current model metadata (versions, timestamps, counts).
- `GET  /model/{name}/download` – model artifact binaries (e.g., `.joblib`).
- `GET  /status` – basic readiness / buffer depth.
- Health probes: `/healthz`, `/readyz`.

**Config & storage**
- Controlled by `latency-predictor-config` (see below).
- Writes artifacts to its **own** volume (`/models`) so predictors can sync from it.
- Retraining knobs:
  - `LATENCY_RETRAINING_INTERVAL_SEC`
  - `LATENCY_MIN_SAMPLES_FOR_RETRAIN`
  - `LATENCY_MAX_TRAINING_DATA_SIZE_PER_BUCKET`

### 2) Prediction sidecars (`prediction-server-1/2/3`, ports **8001/8002/8003**)

**Purpose**
- Serve low‑latency **predictions** of TTFT/TPOT for a given request + pod state.
- Keep an up‑to‑date **local copy** of model artifacts, synced from the training sidecar.

**API**
- `POST /predict` – returns `{ ttft_ms, tpot_ms, uncertainty }`.
- `GET  /status`, `/healthz`, `/readyz` – health + basic info.
- `POST /reload` – manual artifact reload if needed.

**Config & storage**
- Controlled by `prediction-server-config` (see below).
- Each predictor uses its **own** volume at `/server_models` (one per container)
  to avoid contention; a background syncer refreshes local artifacts when the
  training sidecar publishes a new version.
- Typical model type: **XGBoost** (quantile p90 for SLO‑aware routing).

### How EPP uses the sidecars

- When a request is being **scheduled**, EPP queries one of the predictor sidecars
  on `localhost:{8001|8002|8003}` to estimate per‑pod TTFT/TPOT under current load.
- EPP compares predictions against the request’s SLOs and pod’s strictest running SLO,
  then routes using the **SLO scorer** (positive/negative buckets + weighted choice).
- During streaming, EPP buffers **observed** latency samples and periodically bulk‑posts
  them to the training sidecar’s `/sample`, closing the loop for continual learning.

---

## Sidecars & EPP containers in the Deployment

### EPP container
- **Image**: `epp-wlp-latencypredictor`
- **Args**
  - `--config-file=/config/default-plugins.yaml` (enables profiles below)
  - `-enable-latency-predictor` (turns on the async predictor client in EPP)
- **Env**
  - `PREDICTION_SERVER_URL`: CSV of in‑pod predictor endpoints (e.g. `http://localhost:8001,http://localhost:8002,http://localhost:8003`)
  - `TRAINING_SERVER_URL`: `http://localhost:8000`
  - `LATENCY_MAX_SAMPLE_SIZE`: cap for buffered training samples (EPP bulk‑POSTs to `/sample`)
  - `NEG_HEADROOM_TTFT_WEIGHT`, `NEG_HEADROOM_TPOT_WEIGHT`: weights used by the SLO scorer for negative bucket selection
- `HEADROOM_TTFT_WEIGHT`, `HEADROOM_TPOT_WEIGHT`: blend weights for **positive** headroom (TTFT vs TPOT)
- `HEADROOM_SELECTION_STRATEGY`: `least` (compact) or `most` (spread) for positive bucket selection
- `SLO_BUFFER_FACTOR`: multiplier applied to TPOT SLO to add a safety buffer (default `1.0`)

### Training sidecar (`training-server`)
- **Port**: 8000
- **EnvFrom**: `latency-predictor-config`
- **Volume**: `/models` (exclusive) – artifacts live here

### Prediction sidecars (`prediction-server-1/2/3`)
- **Ports**: 8001, 8002, 8003
- **EnvFrom**: `prediction-server-config`
- **Volumes**: `/server_models` (exclusive per predictor) – local artifact cache

---

## ConfigMaps you must apply

### 1) `latency-predictor-config` (training)
```yaml
data:
  LATENCY_RETRAINING_INTERVAL_SEC: "1"
  LATENCY_MIN_SAMPLES_FOR_RETRAIN: "100"
  LATENCY_TTFT_MODEL_PATH: "/models/ttft.joblib"
  LATENCY_TPOT_MODEL_PATH: "/models/tpot.joblib"
  LATENCY_TTFT_SCALER_PATH: "/models/ttft_scaler.joblib"
  LATENCY_TPOT_SCALER_PATH: "/models/tpot_scaler.joblib"
  LATENCY_MODEL_TYPE: "xgboost"
  LATENCY_MAX_TRAINING_DATA_SIZE_PER_BUCKET: "5000"
```

### 2) `prediction-server-config` (predictors)
```yaml
data:
  LATENCY_MODEL_TYPE: "xgboost"
  PREDICT_HOST: "0.0.0.0"
  LOCAL_TTFT_MODEL_PATH: "/server_models/ttft.joblib"
  LOCAL_TPOT_MODEL_PATH: "/server_models/tpot.joblib"
  LOCAL_TTFT_SCALER_PATH: "/server_models/ttft_scaler.joblib"
  LOCAL_TPOT_SCALER_PATH: "/server_models/tpot_scaler.joblib"
```

---

## Profiles & Plugins (the important part)

ConfigMap: `plugins-config` → `default-plugins.yaml`:

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
  - type: queue-scorer
  - type: kv-cache-utilization-scorer
  - type: prefix-cache-scorer
  - type: slo-request-tracker
  - type: slo-scorer
  - type: slo-aware-profile-handler
  - type: max-score-picker

schedulingProfiles:
  - name: default
    plugins:
      - pluginRef: slo-request-tracker
      - pluginRef: prefix-cache-scorer
      - pluginRef: queue-scorer
      - pluginRef: kv-cache-utilization-scorer
      - pluginRef: max-score-picker

  - name: slo
    plugins:
      - pluginRef: prefix-cache-scorer
        weight: 0
      - pluginRef: slo-request-tracker
      - pluginRef: slo-scorer
      - pluginRef: max-score-picker
```

### What these do
- **`slo-request-tracker`** – captures per‑request SLOs and tracks them for routing & training.
- **`slo-scorer`** – uses predicted TTFT/TPOT to compare against SLOs; classifies into positive/negative buckets; picks via weighted randomness (tunable α/β/γ/δ).
- **`slo-aware-profile-handler`** – routes to the SLO profile when SLO headers/context are present.
- **`queue-scorer`**, **`kv-cache-utilization-scorer`**, **`prefix-cache-scorer`** – baseline scorers for the `default` profile.

### Choosing a profile
- **`default`** – queue + kv‑util scoring only.
- **`slo`** – enable SLO‑aware routing; send headers like `X-SLO-TTFT` and `X-SLO-TPOT`.


### Headroom strategies (positive bucket)

#### Configure strategies via environment variables

Set these **EPP container** env vars to tune behavior (defaults in parentheses):

- `HEADROOM_SELECTION_STRATEGY` — `least` (**default**) or `most`.  *least = compact (pack onto few pods), most = spread (distribute load).*  
- `HEADROOM_TTFT_WEIGHT` (0.8) and `HEADROOM_TPOT_WEIGHT` (0.2) — blend TTFT vs TPOT when ranking **positive** headroom pods.
- `NEG_HEADROOM_TTFT_WEIGHT` (0.8) and `NEG_HEADROOM_TPOT_WEIGHT` (0.2) — blend of TTFT vs TPOT **deficits** inside the negative bucket.


When multiple pods have **positive headroom**, EPP can bias the pick by headroom style:

- **Least headroom (_compact_)** — prefer pods just above the SLO threshold. This **packs** work onto fewer pods to improve cache locality and free others.
- **Most headroom (_spread_)** — prefer pods with the largest slack. This **spreads** work to reduce risk of tail latency on any one pod.

This choice is applied alongside the bucket’s weighted‑random selection and the headroom blend weights (α/β for positive). Pick **least** to consolidate, **most** to distribute.

---

## Enable prediction‑based scheduling (SLO‑aware)

To turn on SLO‑aware routing *for a given request*, set the **prediction-based scheduling** header.

### Header
- `x-prediction-based-scheduling: true`

### Behavior
- If **enabled** and **SLO headers are present** (e.g., `X-SLO-TTFT`, `X-SLO-TPOT`), the SLO scorer compares predicted TTFT/TPOT to those thresholds.
- If **enabled** but **no SLO headers are provided**, it assumes **SLO = 0** and routes to **minimize latency** (i.e., “best‑effort lowest latency” behavior).
- If **disabled**, routing falls back to the active profile (e.g., `default` queue/kv‑util scoring).

### Percentile & training mode (current status)
- **Only one percentile is supported right now: p90** (used for quantile predictions). Working toward making it **configurable**.
- **Model training is currently wired for streaming mode** (continuous token streaming), and uses streamed observations to build/update the training set.
- **TPOT sampling:** during streaming we record TPOT at a fixed sampling stride (**every 200th generated token**) and surface those samples in responses.

---

## Sample request/response (SSE)

### Request
```bash
curl -v $GW_IP/v1/completions   -H 'Content-Type: application/json'   -H 'x-prediction-based-scheduling: true'   -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "prompt": "what is the difference between a Franz and Apache Kafka?",
    "max_tokens": 200,
    "temperature": 0,
    "stream_options": {"inlcude_usage": "true"},
    "stream": "true",
    "slo": {
      "ttft_ms": 250,    // optional
      "tpot_ms": 100     // optional
    }
  }'
```

> You may also send the SLOs via headers if your setup prefers that style (e.g., `X-SLO-TTFT`, `X-SLO-TPOT`).

### Response (abridged SSE)
```
< HTTP/1.1 200 OK
< content-type: text/event-stream; charset=utf-8
...
data: {"choices":[{"index":0,"text":" Apache"}], "object":"text_completion", ...}
data: {"choices":[{"index":0,"text":" Kafka"}],  "object":"text_completion", ...}
... (many streamed tokens) ...
data: {
  "object":"text_completion",
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 200,
    "total_tokens": 212,
    "ttft_ms": 59,
    "tpot_observations_ms": [9, 6],
    "avg_tpot_ms": 7.5,
    "predicted_ttft_ms": 273.23,
    "predicted_tpot_observations_ms": [176.22, 18.17],
    "avg_predicted_tpot_ms": 97.19
  }
}
data: [DONE]
```

**Notes**
- The final SSE frame includes **both predictions and actuals** so you can validate accuracy (e.g., `predicted_ttft_ms` vs `ttft_ms`, predicted TPOT vs observed TPOT).
- **TPOTs are sampled** (every **200th** token) — the arrays like `tpot_observations_ms` and `predicted_tpot_observations_ms` reflect those sampled tokens rather than every single token.

---

## Deployment flow

1) **Install the Inference Gateway extension**  
   Follow the official steps here:  
   https://gateway-api-inference-extension.sigs.k8s.io/guides/

2) **Build your EPP image** from the experimental branch (contains SLO prediction code paths & sidecars):  
   https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/slo-prediction-experimental

3) **Apply your InferencePool/EPP YAML** (the one shown earlier), updating the EPP container **image** to the one you just built.  
   - The YAML already defines:
     - The `Service` exposing EPP gRPC (9002), training (8000), predictors (8001–8003), and Prometheus (9090).
     - The `Deployment` with EPP + training + 3×prediction sidecars, each with their own volumes.
     - The `plugins-config` ConfigMap with `default` and `slo` profiles.
     - RBAC and bindings needed by EPP.

4) **Confirm readiness**
   - `kubectl get pods` – EPP Pod should be `Running/Ready`.
   - Training sidecar: `/readyz` on **8000**.
   - Each predictor: `/readyz` on **8001/8002/8003**.
   - EPP gRPC health: port **9003** (liveness/readiness probes).

5) **Send traffic**
   - Baseline: run with the **`default`** profile.
   - SLO‑aware: run with the **`slo`** profile, set `x-prediction-based-scheduling: true`, and optionally include SLOs (headers or JSON body).

---

## Minimal “it works” checklist

- [ ] Installed the Inference Gateway from the official guide.
- [ ] Built and pushed your EPP image from the **slo-prediction-experimental** branch.
- [ ] Applied the YAML with your image reference.
- [ ] Training/predictors are **Ready**; EPP health probes green.
- [ ] Requests with prediction‑based scheduling enabled route via the **`slo`** profile and show positive/negative bucket decisions in logs/metrics.

---



---

## Example EPP YAML

For a ready-to-use manifest, see the example here (experimental branch):

- **Example EPP YAML:** https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/slo-prediction-experimental/config/manifests/inferencepool-resources.yaml


## References

- **Gateway API Inference Extension (SLO Prediction – experimental branch):**  
  https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/slo-prediction-experimental

- **Design Doc (Prediction-based scheduling / SLO-aware routing):**  
  https://docs.google.com/document/d/1q56wr3N5XGx0B21MzHu5oBsCiGi9VrbZAvyhP2VFG_c/edit?tab=t.vq4xxydkz7ze#heading=h.bckmljf2iw8n