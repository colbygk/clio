#!/usr/bin/env bash
# 30-comfyui.sh — set up ComfyUI on each node using the community DGX-Spark Docker compose.
# Runs on BOTH nodes. ComfyUI is NOT auto-started (it would hold GPU memory); start it on
# demand with `clusterctl comfyui up`. Parallelism across the two boxes = ComfyUI-Distributed
# (add via ComfyUI-Manager once running; see docs/comfyui.md).
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"
REAL_USER=${SUDO_USER:-colby}

REPO="$CLUSTER_DIR/comfyui-docker"
if [ -d "$REPO/.git" ]; then
  log "Updating ComfyUI-Nvidia-Docker"
  sudo -u "$REAL_USER" git -C "$REPO" pull --ff-only || warn "git pull failed (keeping existing checkout)"
else
  log "Cloning mmartial/ComfyUI-Nvidia-Docker (has compose-dgx_spark.yaml, SageAttention sm_121)"
  sudo -u "$REAL_USER" git clone https://github.com/mmartial/ComfyUI-Nvidia-Docker "$REPO"
fi

# Use SDPA instead of SageAttention: the image is launched with --use-sage-attention, but the
# sageattention package isn't installed (it needs an on-device sm_121 compile) so ComfyUI
# crash-loops. SDPA is fine/faster on Blackwell anyway. Idempotent. (Compile SageAttention later
# as a perf optimization if desired — see docs/comfyui.md.)
sed -i 's/--use-sage-attention //' "$REPO"/compose-dgx_spark.yaml 2>/dev/null || true

# ComfyUI's Assets browser (the sidebar listing models, inputs and outputs) ships DISABLED; without
# --enable-assets the panel renders empty and /api/assets returns 503 SERVICE_DISABLED, which looks
# like missing data rather than a missing flag. Idempotent.
grep -q -- '--enable-assets' "$REPO"/compose-dgx_spark.yaml 2>/dev/null \
  || sed -i 's|COMFY_CMDLINE_EXTRA: --bf16-unet|COMFY_CMDLINE_EXTRA: --enable-assets --bf16-unet|' \
       "$REPO"/compose-dgx_spark.yaml 2>/dev/null || true

# Publish ComfyUI on loopback only. It has no authentication of its own and custom nodes run
# arbitrary code, so the LAN must reach it solely through nginx's mutual-TLS front door
# (45-nginx.sh). Docker's default 0.0.0.0 binding is what exposes it. Idempotent.
# NOTE: this makes ComfyUI unreachable from the OTHER node too, which would matter if you ever
# adopt ComfyUI-Distributed — that needs a deliberate exception, not an accidental one.
sed -i -E 's|^(\s*)-\s*"?8188:8188"?|\1- 127.0.0.1:8188:8188|' "$REPO"/compose-dgx_spark.yaml 2>/dev/null || true

# Persist models/output/custom_nodes under /data/comfyui (created in 00-common).
# We expose these to the compose via env so the upstream file can mount them.
cat > "$REPO/.env" <<EOF
COMFY_DATA=$COMFY_DIR
COMFYUI_PORT=${COMFYUI_PORT:-8188}
EOF
chown "$REAL_USER":"$REAL_USER" "$REPO/.env"

# The mmartial image runs ComfyUI as uid 1000 and REFUSES to chown PATH-mounted dirs, so it
# crash-loops if these bind-mount sources are root-owned (Docker auto-creates them as root).
# Pre-create them owned by 1000:1000 (= colby) to avoid that.
for d in basedir run userscripts_dir; do mkdir -p "$REPO/$d"; done
chown -R 1000:1000 "$REPO/basedir" "$REPO/run" "$REPO/userscripts_dir"

# The systemd unit is installed by 32-comfyui-profiles.sh, which owns the studio/private layout it runs.
log "30-comfyui done. Next: 32-comfyui-profiles"
