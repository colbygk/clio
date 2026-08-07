#!/usr/bin/env bash
# sync-comfyui-from-urania.sh — bring clio's node-LOCAL ComfyUI state in line with urania's.
#
# Models and input/ are NOT handled here: they are a single copy on urania, NFS-mounted onto clio
# over the fabric (see docs/comfyui-shared-store.md). Adding a model on either node is instantly
# visible on both. What stays node-local, and therefore needs this script:
#
#   user/default/workflows/  saved workflow JSON (small; lives under user/, which isn't exported)
#   custom_nodes/            deliberately local — each node has its own venv, so nodes must be
#                            installed per-node; this script only reports drift, it won't clone.
#
#   sudo bash /opt/clio/scripts/sync-comfyui-from-urania.sh            # workflows + drift report
#   sudo bash /opt/clio/scripts/sync-comfyui-from-urania.sh workflows  # workflows only
set -euo pipefail
cd "$(dirname "$0")"
. lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"
REAL_USER=${SUDO_USER:-colby}

SRC_HOST=${SRC_HOST:-$URANIA_FABRIC}          # fabric IP; override with SRC_HOST=urania.cgk.sh
B=/opt/clio/comfyui-docker/basedir
WHAT=${1:-all}

sudo -u "$REAL_USER" ssh -o BatchMode=yes -o ConnectTimeout=10 "$SRC_HOST" true \
  || die "cannot reach $SRC_HOST over the fabric (is urania up? node<->node SSH set up?)"

# Guard: if models/ is the NFS share, source and destination are the same bytes — never rsync it.
if mountpoint -q "$B/models"; then
  log "models/ is the shared NFS store (nothing to copy) — as expected"
else
  warn "models/ is NOT mounted from urania; this node has a private copy. See docs/comfyui-shared-store.md"
fi

sync_workflows() {
  local d="$B/user/default/workflows"
  mkdir -p "$d"
  # rsync runs as $REAL_USER, so the target must be writable by them BEFORE the transfer,
  # not just chowned afterwards (mkdir -p above runs as root).
  chown -R 1000:1000 "$B/user"
  log "syncing workflows from $SRC_HOST"
  sudo -u "$REAL_USER" rsync -a --info=stats2 "$SRC_HOST:$d/" "$d/"
  chown -R 1000:1000 "$B/user"
  log "workflows now on this node: $(ls -1 "$d" | wc -l)"
}

report_nodes() {
  log "custom_nodes drift (install missing ones per-node via ComfyUI-Manager or git clone):"
  diff <(sudo -u "$REAL_USER" ssh "$SRC_HOST" "ls -1 $B/custom_nodes" 2>/dev/null | sort) \
       <(ls -1 "$B/custom_nodes" | sort) \
    | sed 's/^</  only on urania: /; s/^>/  only on clio:   /' | grep -E 'only on' || echo "  (in sync)"
}

case "$WHAT" in
  all)       sync_workflows; report_nodes;;
  workflows) sync_workflows;;
  nodes)     report_nodes;;
  *) die "usage: $0 [all|workflows|nodes]";;
esac
log "done. Hit Refresh in the ComfyUI UI (or restart clio-comfyui) to pick up new workflows."
