#!/usr/bin/env bash
# serve-shard.sh — SCALE-UP: one model spanning BOTH nodes via eugr's launch-cluster.sh (Ray).
# Runs on the HEAD node only (clio); launch-cluster.sh brings up the worker on urania over the
# fabric (needs node<->node passwordless SSH, set up by 20-vllm.sh). Run by clio-vllm-shard.service.
set -euo pipefail
CLUSTER_DIR=${CLUSTER_DIR:-/opt/clio}
VLLM_PORT=${VLLM_PORT:-8000}

[ -f "$CLUSTER_DIR/shard-profile.env" ] || { echo "no shard profile; nothing to serve"; exit 0; }
# shellcheck disable=SC1090,SC1091
. "$CLUSTER_DIR/shard-profile.env"
[ -f "$CLUSTER_DIR/secrets.env" ] && . "$CLUSTER_DIR/secrets.env"
[ "${PROFILE_MODE:-}" = "shard" ] || { echo "shard profile mode=$PROFILE_MODE is not shard; skipping"; exit 0; }

# NCCL/RoCE safety knobs (see cluster/nccl.env). launch-cluster.sh forwards NCCL_* into the cluster.
set -a; . "$CLUSTER_DIR/nccl.env"; set +a

cd "$CLUSTER_DIR/spark-vllm-docker"
exec ./launch-cluster.sh exec \
  vllm serve "$VLLM_MODEL" \
  --served-model-name "$VLLM_SERVED_NAME" \
  --host 0.0.0.0 --port "$VLLM_PORT" \
  --max-model-len "${VLLM_MAX_LEN:-32768}" \
  --gpu-memory-utilization "${VLLM_GPU_UTIL:-0.85}" \
  --enable-prefix-caching --load-format fastsafetensors \
  ${VLLM_ARGS:-}
