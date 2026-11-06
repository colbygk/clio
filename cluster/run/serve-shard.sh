#!/usr/bin/env bash
# serve-shard.sh — SCALE-UP: one model spanning BOTH nodes via eugr's launch-cluster.sh (Ray).
# Runs on the HEAD node only (clio); launch-cluster.sh brings up the worker on urania over the
# fabric by ssh, as colby (the only user with the node<->node key — hence User= in the unit).
# Run by clio-vllm-shard.service.
set -euo pipefail
CLUSTER_DIR=${CLUSTER_DIR:-/opt/clio}
VLLM_PORT=${VLLM_PORT:-8000}

[ -f "$CLUSTER_DIR/shard-profile.env" ] || { echo "no shard profile; nothing to serve"; exit 0; }
# shellcheck disable=SC1090,SC1091
. "$CLUSTER_DIR/shard-profile.env"
[ -f "$CLUSTER_DIR/secrets.env" ] && . "$CLUSTER_DIR/secrets.env"
[ "${PROFILE_MODE:-}" = "shard" ] || { echo "shard profile mode=$PROFILE_MODE is not shard; skipping"; exit 0; }

. "$CLUSTER_DIR/inventory.env"

# Weights in the same cache the solo profiles use, on each node; the launcher otherwise mounts
# $HOME/.cache/huggingface. Each node downloads the files for its own half on first start.
export HF_HOME=${MODELS_DIR:-/data/models}/hf

# NCCL/RoCE safety knobs (see cluster/nccl.env). launch-cluster.sh forwards NCCL_* into the cluster.
set -a; . "$CLUSTER_DIR/nccl.env"; set +a

# Container patches the profile asks for (cluster/vllm-mods/<name>/run.sh), applied on both nodes.
MODS=()
for m in ${VLLM_MODS:-}; do MODS+=(--apply-mod "$CLUSTER_DIR/vllm-mods/$m"); done

cd "$CLUSTER_DIR/spark-vllm-docker"
# Name the nodes. Left to itself the launcher scans the LAN (enP7s7) for GB10s by ssh, finds none
# reachable, and silently drops to solo mode — vLLM then waits forever for a second GPU (2026-11-06).
# --eth-if puts Ray/Gloo on the fabric too, matching NCCL_SOCKET_IFNAME in nccl.env.
exec ./launch-cluster.sh -n "$CLIO_FABRIC,$URANIA_FABRIC" --eth-if enp1s0f1np1 "${MODS[@]}" exec \
  vllm serve "$VLLM_MODEL" \
  --served-model-name "$VLLM_SERVED_NAME" \
  --host 0.0.0.0 --port "$VLLM_PORT" \
  --max-model-len "${VLLM_MAX_LEN:-32768}" \
  --gpu-memory-utilization "${VLLM_GPU_UTIL:-0.85}" \
  --enable-prefix-caching --load-format fastsafetensors \
  ${VLLM_ARGS:-}
