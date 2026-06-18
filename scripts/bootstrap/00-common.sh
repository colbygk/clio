#!/usr/bin/env bash
# 00-common.sh — base setup, idempotent, run as root on each node.
# Packages, docker group, data dirs, docker nvidia runtime sanity, unified-memory safety.
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
need_root

REAL_USER=${SUDO_USER:-colby}

log "Ensuring base packages"
export DEBIAN_FRONTEND=noninteractive
# Only touch apt if something is actually missing. A blanket apt-get install triggers
# `dpkg --configure --pending`, which on these DGX boxes tries to rebuild the half-configured
# DOCA/MLNX-OFED DKMS module (a known failure on kernel 6.17.0-1018-nvidia) and blocks.
MISSING=()
for p in jq curl rsync ethtool iproute2; do
  command -v "$p" >/dev/null 2>&1 || dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
done
if [ ${#MISSING[@]} -gt 0 ]; then
  warn "Installing missing tools: ${MISSING[*]}"
  apt-get install -y -q "${MISSING[@]}" \
    || warn "apt install failed (likely the known OFED DKMS pending-configure issue) — these are non-critical, continuing"
else
  log "base packages already present — skipping apt"
fi

log "Adding $REAL_USER to docker group (effective on next login/SSH session)"
getent group docker >/dev/null || groupadd docker
id -nG "$REAL_USER" | tr ' ' '\n' | grep -qx docker || usermod -aG docker "$REAL_USER"

log "Creating data dirs ($MODELS_DIR, $COMFY_DIR, $CLUSTER_DIR)"
mkdir -p "$MODELS_DIR/hf" "$COMFY_DIR/models" "$COMFY_DIR/output" "$COMFY_DIR/custom_nodes" "$CLUSTER_DIR"
chown -R "$REAL_USER":"$REAL_USER" "$DATA_DIR" "$CLUSTER_DIR"

log "Verifying NVIDIA container runtime"
if ! grep -q '"nvidia"' /etc/docker/daemon.json 2>/dev/null; then
  log "Registering nvidia runtime with docker"
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi
# Quick GPU-in-container smoke test (non-fatal if image missing offline).
if docker info >/dev/null 2>&1; then
  if docker run --rm --runtime=nvidia --gpus all nvcr.io/nvidia/cuda:13.0.2-base-ubuntu24.04 \
       nvidia-smi -L >/dev/null 2>&1; then
    log "GPU visible inside containers"
  else
    warn "GPU-in-container test skipped/failed (recheck after first image pull)"
  fi
fi

log "Unified-memory safety note"
# 128GB is ONE shared CPU+GPU pool; OOM can freeze the box. We leave swap enabled
# (acts as a thrash cushion) but cap model GPU-mem-util in the profiles instead.
# If you prefer hard OOM-kill over freeze, run: swapoff -a (and comment the swap line in fstab).

log "00-common done"
