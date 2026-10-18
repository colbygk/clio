#!/usr/bin/env bash
# 32-comfyui-profiles.sh — the studio/private split, and the systemd unit that runs either.
#
# ComfyUI runs as one of two profiles (cluster/comfyui/<profile>.yaml, layered over the upstream
# compose file):
#   studio   the animation work. Its own input/output/user dirs under comfyui-docker/studio/, and
#            a curated model tree (cluster/comfyui/studio-models.txt) of symlinks into the store.
#   private  the original basedir/ layout, untouched, reached only on :8443 with a private-* cert.
# Runs on BOTH nodes. Idempotent; re-run it (`clusterctl comfyui sync`) after editing the model list.
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"

REPO="$CLUSTER_DIR/comfyui-docker"
B=$REPO/basedir
S=$REPO/studio
LIST=$CLUSTER_DIR/comfyui/studio-models.txt
[ -f "$LIST" ] || die "missing $LIST"

# ---------- 1. studio directories ----------
log "Studio dirs under $S"
mkdir -p "$S"/{input,output,models,user/default/workflows}
# Seed neutral UI and Manager settings from the original user dir, once. Nothing that lists files.
if [ ! -f "$S/user/default/comfy.settings.json" ] && [ -f "$B/user/default/comfy.settings.json" ]; then
  cp "$B/user/default/comfy.settings.json" "$S/user/default/"
fi
if [ ! -f "$S/user/__manager/config.ini" ] && [ -f "$B/user/__manager/config.ini" ]; then
  mkdir -p "$S/user/__manager"
  cp "$B/user/__manager/config.ini" "$B/user/__manager/channels.list" "$S/user/__manager/" 2>/dev/null || true
fi
# On the node that mounts studio/input over NFS, chown would go through to the exporter's files.
mountpoint -q "$S/input" || chown 1000:1000 "$S/input"
chown -R 1000:1000 "$S/output" "$S/models" "$S/user"

# ---------- 2. curated model tree ----------
# Rebuilt from scratch each run so removing a line removes the model. Only symlinks are deleted:
# a real file here was put there by hand (or by a Manager download) and is left alone.
log "Building the studio model tree from $(basename "$LIST")"
find "$S/models" -type l -delete
n=0
while read -r pat; do
  pat=${pat%%#*}; pat=$(echo "$pat" | xargs)   # strip comments and surrounding space
  [ -n "$pat" ] || continue
  found=0
  for src in "$B/models"/$pat; do
    [ -e "$src" ] || continue
    rel=${src#"$B/models/"}
    mkdir -p "$S/models/$(dirname "$rel")"
    # The target only resolves inside the container, where the full store is mounted at /store/models.
    ln -sfn "/store/models/$rel" "$S/models/$rel"
    found=1; n=$((n + 1))
  done
  [ "$found" = 1 ] || warn "studio-models.txt: nothing in the store matches '$pat'"
done < "$LIST"
find "$S/models" -type d -empty -delete 2>/dev/null || true
mkdir -p "$S/models"
chown -R -h 1000:1000 "$S/models"
log "Studio sees $n model entries"

# ---------- 3. systemd unit ----------
COMPOSE_FILE=$(ls "$REPO"/compose-dgx_spark.yaml "$REPO"/compose*spark*.yaml 2>/dev/null | head -1 || true)
[ -n "$COMPOSE_FILE" ] || warn "compose-dgx_spark.yaml not found in repo — check $REPO and edit clio-comfyui.service"
log "Installing clio-comfyui.service (compose: ${COMPOSE_FILE:-<not found>})"
sed -e "s|__COMPOSE_FILE__|${COMPOSE_FILE:-$REPO/compose-dgx_spark.yaml}|g" -e "s|__REPO__|$REPO|g" \
    -e "s|__CLUSTER_DIR__|$CLUSTER_DIR|g" \
  "$CLUSTER_DIR/systemd/clio-comfyui.service.tmpl" > /etc/systemd/system/clio-comfyui.service
systemctl daemon-reload
systemctl disable clio-comfyui.service 2>/dev/null || true   # default OFF — start on demand via clusterctl
[ -f "$CLUSTER_DIR/comfyui-profile.env" ] || echo "COMFY_PROFILE=studio" > "$CLUSTER_DIR/comfyui-profile.env"

log "32-comfyui-profiles done. Profile: $(cut -d= -f2 "$CLUSTER_DIR/comfyui-profile.env")"
