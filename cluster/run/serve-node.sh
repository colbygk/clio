#!/usr/bin/env bash
# serve-node.sh — SCALE-OUT: serve one model on THIS node (TP=1). Run by clio-vllm.service.
# Uses a direct `docker run` with eugr's documented solo flags so we can select the image
# per-profile (vllm-node vs vllm-node-mxfp4). Reads the active profile written by clusterctl.
set -euo pipefail
CLUSTER_DIR=${CLUSTER_DIR:-/opt/clio}
MODELS_DIR=${MODELS_DIR:-/data/models}
VLLM_PORT=${VLLM_PORT:-8000}

[ -f "$CLUSTER_DIR/active-profile.env" ] || { echo "no active profile; nothing to serve"; exit 0; }
# shellcheck disable=SC1090,SC1091
. "$CLUSTER_DIR/active-profile.env"
[ -f "$CLUSTER_DIR/secrets.env" ] && . "$CLUSTER_DIR/secrets.env"   # optional HF_TOKEN
[ "${PROFILE_MODE:-}" = "scaleout" ] || { echo "active profile mode=$PROFILE_MODE is not scaleout; skipping"; exit 0; }

docker rm -f vllm >/dev/null 2>&1 || true

CMD="vllm serve '$VLLM_MODEL' \
  --served-model-name '$VLLM_SERVED_NAME' \
  --host 0.0.0.0 --port $VLLM_PORT \
  --max-model-len ${VLLM_MAX_LEN:-131072} \
  --gpu-memory-utilization ${VLLM_GPU_UTIL:-0.85} \
  --max-num-seqs ${VLLM_MAX_SEQS:-4} \
  --enable-prefix-caching --load-format fastsafetensors ${VLLM_ARGS:-}"

exec docker run --rm --name vllm \
  --privileged --runtime nvidia --gpus all \
  --network host --ipc=host \
  --ulimit nofile=1048576:1048576 --ulimit memlock=-1 \
  -e HF_TOKEN="${HF_TOKEN:-}" \
  -v "${MODELS_DIR}/hf:/root/.cache/huggingface" \
  "${VLLM_IMAGE:-vllm-node}" bash -lc "$CMD"
