#!/usr/bin/env bash
# 10-network.sh — configure the clio<->urania 200G RoCE fabric. Idempotent, run as root.
# - Brings up ALL FOUR ConnectX-7 rails on four /24 subnets (two were already configured).
# - Sets MTU 9000 so RoCE active_mtu jumps from 1024 -> 4096 (the big throughput unlock).
# - Verifies active_mtu and peer reachability on every rail.
# Direct-attach, no switch: no PFC/ECN needed (Zero-Touch RoCE handles it).
set -euo pipefail
cd "$(dirname "$0")"
. ../lib/common.sh
. "$CLUSTER_DIR/inventory.env"
need_root

HOST=$(hostname -s)
case "$HOST" in
  clio)   OCT=13 ;;
  urania) OCT=12 ;;
  *) die "unknown host '$HOST' — expected clio or urania. Edit OCT mapping if you add nodes." ;;
esac
log "Configuring fabric for $HOST (host octet .$OCT), MTU $FABRIC_MTU"

# iface -> subnet third-octet. Mapping verified 2026-06-17 via ibdev2netdev.
declare -A SUBNET=(
  [enp1s0f1np1]=50    # mlx5_1  (already configured)
  [enP2p1s0f1np1]=51  # mlx5_3  (already configured)
  [enp1s0f0np0]=52    # mlx5_0  (new)
  [enP2p1s0f0np0]=53  # mlx5_2  (new)
)

# Preserve the renderer the existing CX-7 netplan uses; fall back to detecting NetworkManager.
NETPLAN=/etc/netplan/40-cx7.yaml
if [ -f "$NETPLAN" ] && grep -q 'renderer:' "$NETPLAN"; then
  RENDERER=$(grep -m1 'renderer:' "$NETPLAN" | awk '{print $2}')
elif systemctl is-active --quiet NetworkManager; then
  RENDERER=NetworkManager
else
  RENDERER=networkd
fi
log "Using netplan renderer: $RENDERER"

# Back up the existing config once.
if [ -f "$NETPLAN" ] && [ ! -f "$NETPLAN.clio-orig" ]; then
  cp -a "$NETPLAN" "$NETPLAN.clio-orig"
  log "Backed up original to $NETPLAN.clio-orig"
fi

# Render the full 4-rail config.
{
  echo "# Managed by clio bootstrap (scripts/bootstrap/10-network.sh). Do not edit by hand."
  echo "network:"
  echo "  version: 2"
  echo "  renderer: $RENDERER"
  echo "  ethernets:"
  for IF in enp1s0f1np1 enP2p1s0f1np1 enp1s0f0np0 enP2p1s0f0np0; do
    echo "    $IF:"
    echo "      addresses: [10.51.${SUBNET[$IF]}.$OCT/24]"
    echo "      mtu: $FABRIC_MTU"
    echo "      optional: true"      # don't block boot if a cable is out
  done
} > "$NETPLAN"
chmod 600 "$NETPLAN"

log "Applying netplan (safe — we are connected via the management NIC, not the fabric)"
netplan generate
netplan apply
sleep 3

log "Verifying RoCE active_mtu (want 4096) on all rails"
ok=1
for d in mlx5_0 mlx5_1 mlx5_2 mlx5_3; do
  amtu=$(ibv_devinfo -d "$d" 2>/dev/null | awk '/active_mtu/{print $2}')   # value, e.g. 4096
  echo "  $d active_mtu=${amtu:-?}"
  [ "$amtu" = "4096" ] || { warn "$d active_mtu is $amtu, expected 4096"; ok=0; }
done

log "Pinging peer on every rail"
PEER_OCT=$([ "$OCT" = 13 ] && echo 12 || echo 13)
for s in 50 51 52 53; do
  if ping -c1 -W2 "10.51.$s.$PEER_OCT" >/dev/null 2>&1; then
    echo "  10.51.$s.$PEER_OCT  reachable"
  else
    warn "10.51.$s.$PEER_OCT  NOT reachable (cable out, or peer not yet configured — re-run after both nodes are done)"
  fi
done

# Jumbo frames belong on the FABRIC only. The management LAN switch carries 1500, and an oversized
# MTU there is a silent killer: the TCP handshake succeeds (small packets) while anything larger is
# dropped by the switch, which — being layer 2 — sends no "fragmentation needed" back, so path-MTU
# discovery never kicks in. Symptoms are SSH sessions that connect then hang and TLS that dies right
# at the certificate. urania sat like this until 2026-08-07. Warn rather than change it: the fix
# belongs in NetworkManager (`nmcli con mod <con> 802-3-ethernet.mtu 1500`), not in a bootstrap run.
MGMT_IFACE=$(ip -4 -o route get 10.10.10.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')
if [ -n "${MGMT_IFACE:-}" ]; then
  MGMT_MTU=$(cat "/sys/class/net/$MGMT_IFACE/mtu" 2>/dev/null || echo "?")
  if [ "$MGMT_MTU" != "1500" ]; then
    warn "management interface $MGMT_IFACE has MTU $MGMT_MTU (expected 1500)."
    warn "  On a 1500 LAN this black-holes large packets: ssh hangs, TLS fails after the cert."
    warn "  Fix: sudo nmcli con mod \"\$(nmcli -g GENERAL.CONNECTION dev show $MGMT_IFACE)\" 802-3-ethernet.mtu 1500"
    ok=0
  else
    echo "  mgmt $MGMT_IFACE mtu=1500 (correct — jumbo is fabric-only)"
  fi
fi

[ "$ok" = 1 ] && log "10-network done (jumbo MTU active)" || warn "10-network done with warnings — recheck active_mtu after BOTH nodes run this"
