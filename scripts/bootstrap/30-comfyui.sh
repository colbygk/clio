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

# Persist models/output/custom_nodes under /data/comfyui (created in 00-common).
# We expose these to the compose via env so the upstream file can mount them.
cat > "$REPO/.env" <<EOF
COMFY_DATA=$COMFY_DIR
COMFYUI_PORT=${COMFYUI_PORT:-8188}
EOF
chown "$REAL_USER":"$REAL_USER" "$REPO/.env"

COMPOSE_FILE=$(ls "$REPO"/compose-dgx_spark.yaml "$REPO"/compose*spark*.yaml 2>/dev/null | head -1 || true)
[ -n "$COMPOSE_FILE" ] || warn "compose-dgx_spark.yaml not found in repo — check $REPO and edit clio-comfyui.service"
log "ComfyUI compose: ${COMPOSE_FILE:-<not found>}"

log "Installing ComfyUI systemd unit (enabled but not started)"
sed "s|__COMPOSE_FILE__|${COMPOSE_FILE:-$REPO/compose-dgx_spark.yaml}|; s|__REPO__|$REPO|" \
  "$CLUSTER_DIR/systemd/clio-comfyui.service.tmpl" > /etc/systemd/system/clio-comfyui.service
systemctl daemon-reload
systemctl enable clio-comfyui.service   # starts on boot only after first manual `up`; safe to leave enabled
systemctl disable clio-comfyui.service  # default OFF — start on demand via clusterctl
log "30-comfyui done. Start on demand: clusterctl comfyui up"
