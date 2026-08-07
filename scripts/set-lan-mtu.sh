#!/usr/bin/env bash
# set-lan-mtu.sh — change the management-LAN MTU on this node, with a dead-man's switch.
#
# Raising MTU on a LAN that cannot carry it black-holes every large packet, which means the SSH
# session you would use to undo it also hangs. So the default mode applies the change LIVE ONLY and
# arms a timer to put it back. If the caller can still reach the node afterwards, it calls --commit
# to cancel the timer and persist. If it cannot, the node repairs itself.
#
#   set-lan-mtu.sh 9000            try it: live change + auto-revert in $REVERT_SECS (default 180)
#   set-lan-mtu.sh 9000 --commit   cancel the timer and persist via NetworkManager
#   set-lan-mtu.sh 1500 --revert   put it back now and persist that
#
# Only the management interface is touched. The fabric NICs keep MTU 9000 regardless — jumbo there
# is what makes RoCE's active_mtu 4096, and it is a private point-to-point link with no switch.
set -euo pipefail
cd "$(dirname "$0")"
. lib/common.sh
need_root
. "$CLUSTER_DIR/inventory.env"

MTU=${1:?"usage: set-lan-mtu.sh <mtu> [--commit|--revert]"}
MODE=${2:-try}
GW=${LAN_GATEWAY:-10.10.10.1}
UNIT=clio-mtu-revert
REVERT_SECS=${REVERT_SECS:-180}

IFACE=$(ip -4 -o route get "$GW" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')
[ -n "${IFACE:-}" ] || die "could not determine the management interface (route to $GW)"
CON=$(nmcli -g GENERAL.CONNECTION dev show "$IFACE" 2>/dev/null | head -1)
OLD=$(cat "/sys/class/net/$IFACE/mtu")

cancel_timer() { systemctl stop "$UNIT.timer" 2>/dev/null || true; systemctl reset-failed "$UNIT.timer" "$UNIT.service" 2>/dev/null || true; }

case "$MODE" in
  try)
    [ "$OLD" = "$MTU" ] && { log "$IFACE already at MTU $MTU"; exit 0; }
    cancel_timer
    # Transient timer, not a script on disk: nothing to clean up if the node reboots instead.
    systemd-run --unit="$UNIT" --on-active="$REVERT_SECS" \
      /usr/sbin/ip link set "$IFACE" mtu "$OLD" >/dev/null 2>&1 \
      || die "could not arm the auto-revert timer — refusing to change MTU blind"
    ip link set "$IFACE" mtu "$MTU"
    log "$IFACE MTU $OLD -> $MTU (live only). Auto-revert to $OLD in ${REVERT_SECS}s unless committed."
    ;;
  --commit)
    cancel_timer
    ip link set "$IFACE" mtu "$MTU"
    [ -n "${CON:-}" ] && nmcli con mod "$CON" 802-3-ethernet.mtu "$MTU" \
      || warn "no NetworkManager connection found for $IFACE — MTU is live but NOT persisted"
    log "committed: $IFACE MTU=$MTU, persisted on connection '${CON:-?}'"
    ;;
  --revert)
    cancel_timer
    ip link set "$IFACE" mtu "$MTU"
    [ -n "${CON:-}" ] && nmcli con mod "$CON" 802-3-ethernet.mtu "$MTU" || true
    log "reverted: $IFACE MTU=$MTU (persisted)"
    ;;
  *) die "usage: set-lan-mtu.sh <mtu> [--commit|--revert]";;
esac
