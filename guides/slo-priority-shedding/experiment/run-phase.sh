#!/usr/bin/env bash
# Render an inference-perf config and run it as an in-cluster Job against the EPP.
#
# Usage:
#   ./run-phase.sh <config.yaml> <job-name> [--wait]
#
# Env (with defaults):
#   NAMESPACE        llm-d-predicted-latency
#   RELEASE          predicted-latency-routing        # EPP service is ${RELEASE}-epp
#   MODEL_NAME       mistralai/Mistral-Medium-3.5-128B
#   IMAGE            quay.io/inference-perf/inference-perf:latest
#   HF_SECRET        llm-d-hf-token                   # key HF_TOKEN
#   NUM_WORKERS, SLO_TTFT_MS, SLO_TPOT_MS, CONC_HIGH, NREQ_HIGH, CONC_LOW, NREQ_LOW,
#   NUM_CONVERSATIONS, SEED   # substituted into the config via envsubst
#
# Two classes run concurrently by invoking this twice (high + low) without --wait, then
# waiting on both Jobs.
set -euo pipefail

CONFIG="${1:?config yaml path}"
JOB="${2:?job name}"
WAIT="${3:-}"

export NAMESPACE="${NAMESPACE:-llm-d-predicted-latency}"
export RELEASE="${RELEASE:-predicted-latency-routing}"
export MODEL_NAME="${MODEL_NAME:-mistralai/Mistral-Medium-3.5-128B}"
# Use the REAL Mistral tokenizer. The inference-perf image ships transformers 4.57 (too old
# for Mistral-Medium-3.5's TokenizersBackend tokenizer); the Job upgrades transformers +
# mistral_common into the venv at startup (see the container command below). Validated:
# tokenizer loads (vocab 131072) and inference-perf still imports under transformers 5.x.
export TOKENIZER="${TOKENIZER:-mistralai/Mistral-Medium-3.5-128B}"
IMAGE="${IMAGE:-quay.io/inference-perf/inference-perf:latest}"
HF_SECRET="${HF_SECRET:-llm-d-hf-token}"
export NUM_WORKERS="${NUM_WORKERS:-8}"
# Default SEED to a unique value (timestamp) each run. IMPORTANT for warmup: restarting the
# EPP resets the latency predictor but NOT vLLM's prefix cache, so a fixed seed would retrain
# the fresh predictor on cache-HIT (artificially fast) prompts. A unique seed each warmup
# uses fresh cache-miss conversations. Callers needing determinism (calibration) pass SEED.
export SEED="${SEED:-$(date +%s)}"
export NUM_CONVERSATIONS="${NUM_CONVERSATIONS:-64}"
# EPP ClusterIP service, port 80 -> inference (Envoy) :8081
export ENDPOINT_URL="${ENDPOINT_URL:-http://$(kubectl get svc ${RELEASE}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')}"

echo "[run-phase] job=${JOB} config=${CONFIG} endpoint=${ENDPOINT_URL} model=${MODEL_NAME}"

# Render config (envsubst only the vars we own; leave any stray $ alone).
RENDERED="$(envsubst < "${CONFIG}")"

# (Re)create the ConfigMap with the rendered config as config.yml.
kubectl create configmap "${JOB}-cfg" -n "${NAMESPACE}" \
  --from-literal=config.yml="${RENDERED}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Launch the Job: inference-perf image, our config mounted over /workspace/config.yml,
# HF token for tokenizer download, results to an emptyDir at /workspace.
kubectl delete job "${JOB}" -n "${NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
cat <<EOF | kubectl apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
  namespace: ${NAMESPACE}
  labels: { app: slo-shedding-experiment }
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 7200
  template:
    metadata:
      labels: { app: slo-shedding-experiment, job: ${JOB} }
    spec:
      restartPolicy: Never
      containers:
        - name: inference-perf
          image: ${IMAGE}
          imagePullPolicy: IfNotPresent
          # Upgrade transformers + mistral_common into the venv so the REAL Mistral-Medium-3.5
          # tokenizer loads, then run inference-perf. (~1-2 min one-time per Job.)
          command:
            - sh
            - -c
            - >-
              /usr/local/bin/pip --python /workspace/.venv/bin/python install -q -U
              'transformers>=5.4.0' 'mistral_common>=1.11.1' &&
              python inference_perf/main.py --config_file /etc/ip/config.yml
          env:
            - { name: HF_TOKEN, valueFrom: { secretKeyRef: { name: ${HF_SECRET}, key: HF_TOKEN } } }
            - { name: HUGGING_FACE_HUB_TOKEN, valueFrom: { secretKeyRef: { name: ${HF_SECRET}, key: HF_TOKEN } } }
          volumeMounts:
            # Mount ONLY the config (at /etc/ip). Do NOT mount over /workspace — that is the
            # image WORKDIR holding inference_perf/main.py; a volume there clobbers the app.
            - { name: cfg, mountPath: /etc/ip }
          resources:
            requests: { cpu: "4", memory: 8Gi }
            limits:   { cpu: "8", memory: 16Gi }
      volumes:
        - name: cfg
          configMap: { name: ${JOB}-cfg }
EOF

echo "[run-phase] launched Job/${JOB}"
if [[ "${WAIT}" == "--wait" ]]; then
  kubectl wait --for=condition=complete --timeout=1800s job/${JOB} -n ${NAMESPACE} &
  CW=$!
  kubectl wait --for=condition=failed --timeout=1800s job/${JOB} -n ${NAMESPACE} && FAILED=1 &
  FW=$!
  wait -n $CW $FW || true
  kubectl logs job/${JOB} -n ${NAMESPACE} --tail=60 || true
fi
