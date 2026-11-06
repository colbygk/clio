#!/usr/bin/env bash
# 35-comfyui-share.sh — one copy of the ComfyUI models, visible on both nodes.
#
# The model store (basedir/models and basedir/input, plus the studio profile's studio/input) lives
# on COMFY_STORE_HOST and is exported
# over the 200G fabric via NFSv4.2 with the RDMA transport; the other node mounts it. Measured on
# a 4 GiB direct read of a Wan checkpoint, server cache warm:
#     NFS/RDMA            1.5 - 1.8 GB/s
#     NFS/TCP nconnect=16   ~970 MB/s
#     NFS/TCP single conn   ~320 MB/s
# so RDMA is worth the extra moving part. Runs on BOTH nodes; picks its role from the hostname.
#
# Note on packaging: nfs-kernel-server is installed with `dpkg -i` on a pre-downloaded .deb rather
# than `apt-get install`, because apt would also try to configure the 8 half-installed DOCA/OFED
# DKMS packages on these nodes (they fail to build against kernel 6.17) and abort the transaction.
# All of nfs-kernel-server's dependencies are already satisfied, so dpkg -i is clean.
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"

B=$CLUSTER_DIR/comfyui-docker/basedir
S=$CLUSTER_DIR/comfyui-docker/studio
ME=$(hostname -s)
# Everything shared, as "<path> <tag>" (tag names the local-backup dir). Input dirs are shared so a reference uploaded on
# one node is loadable on the other; output/ and user/ stay node-local.
SHARED=("$B/models models" "$B/input input" "$S/input studio-input")
PORT=${COMFY_STORE_RDMA_PORT:-20049}

echo rpcrdma > /etc/modules-load.d/rpcrdma.conf
modprobe rpcrdma 2>/dev/null || warn "could not load rpcrdma now (it is set to load at boot)"

if [ "$ME" = "$COMFY_STORE_HOST" ]; then
  # ---------- server ----------
  log "$ME owns the ComfyUI model store — configuring the NFS export"
  for e in "${SHARED[@]}"; do mkdir -p "${e% *}"; done

  if ! dpkg-query -W -f='${db:Status-Abbrev}' nfs-kernel-server 2>/dev/null | grep -q '^ii'; then
    log "Installing nfs-kernel-server (dpkg -i, to avoid apt's broken-DKMS cascade)"
    tmp=$(mktemp -d); ( cd "$tmp" && apt-get download nfs-kernel-server >/dev/null )
    dpkg -i "$tmp"/nfs-kernel-server_*.deb
    rm -rf "$tmp"
  fi

  # Persist the RDMA listener — /proc/fs/nfsd/portlist is not durable across restarts.
  mkdir -p /etc/nfs.conf.d
  cat > /etc/nfs.conf.d/10-rdma.conf <<EOF
# NFS over RDMA for the ComfyUI shared model store (managed by 35-comfyui-share.sh).
[nfsd]
rdma=y
rdma-port=$PORT
EOF

  # Export to the peer's fabric IP only. Both nodes run the container as uid 1000, so ids line up;
  # no_root_squash is there so root-run maintenance (chown, model drops) works over the mount.
  # The desk (ceres) gets the input dirs only, over the LAN, all_squashed to uid 1000 — see inventory.env.
  cat > /etc/exports <<EOF
# Managed by scripts/bootstrap/35-comfyui-share.sh — the peer node over the fabric, plus inputs to the desk.
$(for e in "${SHARED[@]}"; do echo "${e% *} $COMFY_STORE_CLIENT_IP(rw,sync,no_subtree_check,no_root_squash)"; done)
$(if [ -n "${COMFY_STORE_DESK_IP:-}" ]; then for e in "${SHARED[@]}"; do case ${e#* } in *input)
  echo "${e% *} $COMFY_STORE_DESK_IP(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)";; esac; done; fi)
EOF
  systemctl enable --now nfs-server >/dev/null 2>&1 || true
  systemctl restart nfs-server
  exportfs -ra
  grep -q rdma /proc/fs/nfsd/portlist || warn "nfsd is not listening on RDMA — check /etc/nfs.conf.d/10-rdma.conf"
  log "Exported: $(exportfs | tr '\n' ' ')"

else
  # ---------- client ----------
  log "$ME mounts the model store from $COMFY_STORE_HOST ($COMFY_STORE_SERVER_IP)"
  SRV=$COMFY_STORE_SERVER_IP
  OPTS="vers=4.2,proto=rdma,port=$PORT,rsize=1048576,wsize=1048576,hard,_netdev,nofail,x-systemd.mount-timeout=30"

  # Anything already sitting at the mountpoints is a private copy that the mount would hide.
  # Move it aside rather than silently shadowing (or deleting) it.
  for e in "${SHARED[@]}"; do
    p=${e% *} tag=${e#* }
    if ! mountpoint -q "$p" && [ -d "$p" ] && [ -n "$(ls -A "$p" 2>/dev/null)" ]; then
      dest=/data/comfyui/local-backup/$tag-$ME-local
      warn "$p is non-empty and not yet mounted — moving it to $dest"
      mkdir -p /data/comfyui/local-backup
      rm -rf "$dest"; mv "$p" "$dest"
    fi
    mkdir -p "$p"; mountpoint -q "$p" || chown 1000:1000 "$p"
  done

  cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d-%H%M%S)"
  sed -i -e '\|comfyui-docker/basedir|d' -e '\|comfyui-docker/studio|d' /etc/fstab
  cat >> /etc/fstab <<EOF

# ComfyUI shared model store (managed by 35-comfyui-share.sh). nofail so a down $COMFY_STORE_HOST
# never blocks boot; clio-comfyui.service carries RequiresMountsFor= so ComfyUI waits instead.
$(for e in "${SHARED[@]}"; do echo "$SRV:${e% *} ${e% *} nfs $OPTS 0 0"; done)
EOF
  systemctl daemon-reload
  mount -a
  for e in "${SHARED[@]}"; do
    mountpoint -q "${e% *}" && log "mounted ${e% *}" || die "failed to mount ${e% *}"
  done
fi

log "35-comfyui-share done."
