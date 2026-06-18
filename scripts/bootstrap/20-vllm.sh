#!/usr/bin/env bash
# 20-vllm.sh — build the eugr vLLM images and install the vLLM systemd units. Run as root.
# Runs on BOTH nodes; each builds its own images (no cross-node copy needed this way).
# NOTE: the first build compiles vLLM + FlashInfer from source — expect 30-60+ min per node.
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"
REAL_USER=${SUDO_USER:-colby}
HOST=$(hostname -s)

REPO="$CLUSTER_DIR/spark-vllm-docker"
# Run as root: docker access is guaranteed, and group caching delays colby's docker access
# until a full re-login. Images are system-wide regardless of build user.
if [ -d "$REPO/.git" ]; then
  log "Updating eugr/spark-vllm-docker"
  git -C "$REPO" pull --ff-only || warn "git pull failed (keeping existing checkout)"
else
  log "Cloning eugr/spark-vllm-docker"
  git clone https://github.com/eugr/spark-vllm-docker "$REPO"
fi

cd "$REPO"
log "Building image: vllm-node (long; compiles vLLM/FlashInfer)"
./build-and-copy.sh

# Install units right after the daily-driver image is built, BEFORE the optional mxfp4 build,
# so a mxfp4 failure can't leave the cluster without its systemd units.
log "Installing vLLM systemd units"
install -m 644 "$CLUSTER_DIR/systemd/clio-vllm.service" /etc/systemd/system/clio-vllm.service
if [ "$HOST" = "${HEAD:-clio}" ]; then
  install -m 644 "$CLUSTER_DIR/systemd/clio-vllm-shard.service" /etc/systemd/system/clio-vllm-shard.service
fi
systemctl daemon-reload
systemctl enable clio-vllm.service   # comes back after reboot; doesn't start until a profile is set

if [ "${BUILD_MXFP4:-1}" = 1 ]; then
  log "Building image: vllm-node-mxfp4 (for GPT-OSS-120B / MXFP4) — non-fatal if it fails"
  ./build-and-copy.sh --exp-mxfp4 || warn "mxfp4 image build failed — gpt-oss-120b unavailable until fixed; local-coder unaffected"
fi
log "20-vllm done. Images: $(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^vllm-node' | tr '\n' ' ')"
