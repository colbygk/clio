# The clio ↔ urania fabric

## What you have (verified 2026-06-17)

Two QSFP cages per Spark, each ConnectX-7 exposing **2× 100G MACs on separate PCIe roots** →
**four RDMA devices / four netdevs per host**, each linked at 200 Gb/s (RoCEv2, Ethernet link layer).
This is what "multiple 100G SFP112 links" means physically.

| RDMA dev | netdev | subnet | clio | urania | state |
|---|---|---|---|---|---|
| mlx5_0 | enp1s0f0np0 | 10.51.52.0/24 | .13 | .12 | configured by bootstrap |
| mlx5_1 | enp1s0f1np1 | 10.51.50.0/24 | .13 | .12 | pre-existing |
| mlx5_2 | enP2p1s0f0np0 | 10.51.53.0/24 | .13 | .12 | configured by bootstrap |
| mlx5_3 | enP2p1s0f1np1 | 10.51.51.0/24 | .13 | .12 | pre-existing |

Management (how ceres reaches the boxes): clio `clio.cgk.sh` / `10.10.10.149` and urania
`urania.cgk.sh` / `10.10.10.155`, both **wired 10Gbase-T** into the USW Flex XG, both at **MTU
9000**. (Earlier notes describing clio as 1GbE and urania as WiFi at `.39` are obsolete — corrected
2026-08-07.)

## What `10-network.sh` changes

1. **MTU 1500 → 9000** on all four rails. This is the big one: at 1500, RoCE `active_mtu`
   is pinned to **1024**; at 9000 it reaches the **4096** ceiling. Verified via `ibv_devinfo`.
2. **Brings up the two idle rails** (mlx5_0, mlx5_2) with IPs on `.52`/`.53`.
3. Writes one netplan file (`/etc/netplan/40-cx7.yaml`, original backed up to `*.clio-orig`).

No PFC/ECN config: direct-attach with no switch, so **Zero-Touch RoCE** handles it.

## How the links are actually used

- **Inter-node service traffic** (gateway→urania vLLM, Ray bootstrap): rides the fabric (`10.51.50.x`).
- **NCCL in sharded mode**: drives **all four rails at once** via `NCCL_IB_HCA=mlx5_0,..,mlx5_3`
  (multi-rail), **not Linux bonding** — bonding/LACP pins a single RDMA QP to one link and breaks
  RDMA failover. See `cluster/nccl.env`.

## Expectations (so nothing surprises you)

- **No GPUDirect RDMA on GB10 — by design, permanently.** NCCL still works over RoCE through
  host/unified memory. In `NCCL_DEBUG=INFO` you'll see `NET/IB` **without** `/GDRDMA` — that's
  correct, not a misconfiguration.
- Realistic NCCL ceiling ≈ **22–24 GB/s per cable**, ~190 Gb/s aggregate across rails — not a
  clean 200. Single-link "200G" in NCCL is not achievable on this hardware.
- The **#1 trap is silent TCP fallback** (~1–3 GB/s) when the bundled OFI plugin fails →
  `NCCL_NET_PLUGIN=none` (set in `nccl.env`) avoids it. Verify the path says `NET/IB` not `NET/Socket`.

## Verify
```
clusterctl status        # shows active_mtu per rail (want 4096) + ping
clusterctl nccl-test     # ib_write_bw across a rail; ~190 Gb/s aggregate target
# Diagnostics that need no model:
#   ibv_devinfo -d mlx5_1 | grep active_mtu      -> 4096 (3)
#   ibdev2netdev                                  -> dev -> netdev map, all (Up)
#   show_gids | grep v2                           -> RoCEv2 GIDs present
```

## Troubleshooting (from field reports — recheck if you hit them)
- **~16–17 GB/s**: stale CX-7 firmware → `fwupdmgr enable-remote lvfs-testing; fwupdmgr update`.
  (Yours is 28.47.1088, recent — likely fine.)
- **~half expected bandwidth**: only one rail bound — confirm all four have IPs + are `(Up)`.
- **A QSFP port won't come up**: the CX-7 PCIe hotplug power-saving feature (added Jan 2026) is the
  usual culprit; a full power drain (unplug ~1 min) has fixed "200G negotiated but tiny payload".
- **OFED won't rebuild after a kernel update**: DOCA/MLNX-OFED 25.10 DKMS fails on some
  `6.17.x-nvidia` kernels; NVIDIA's workaround pins `6.17.0-1014-nvidia`. (RoCE works on your
  current `-1018` kernel today.)
- **Multi-node hang with no error**: NCCL 2.30.4 NVLS regression → `NCCL_NVLS_ENABLE=0` (already set).

## The management LAN — two different MTU worlds

Do not confuse the two networks. The **fabric** (4×200G ConnectX-7, direct cable, no switch) runs
MTU 9000 and that is what lifts RoCE `active_mtu` to 4096. The **management LAN** is a separate
thing: ceres (10.10.10.123), clio (.149) and urania (.155) all sit on one **USW Flex XG** at
10Gbase-T, and its MTU is whatever that switch is configured to forward.

**The trap.** UniFi ships with **Jumbo Frames disabled**. A host set to MTU 9000 on a 1500 switch
does not fail cleanly — the TCP handshake succeeds because SYN packets are small, and then anything
larger is dropped by a layer-2 switch that sends no ICMP "fragmentation needed", so path-MTU
discovery never engages. The result reads as "the machine is flaky": SSH connects then hangs, HTTPS
dies right after the certificate. urania sat in exactly this state until 2026-08-07 and looked, for
a while, like it was merely busy.

**Raising the LAN to 9000, in the only safe order:**

1. **Switch first.** UniFi Network → Settings → Networks → *Global Switch Settings* → enable
   **Jumbo Frames**. This re-provisions the switches, so expect a brief blip. Nothing else works
   until this is done — the USW Flex XG supports it, it is just off by default.
2. **ceres.** `sudo ifconfig en0 mtu 9000` (persist via a Network location/profile if you want it
   across reboots). It happens to be 9000 already.
3. **The nodes.** `clusterctl lan-mtu 9000`, then set `LAN_MTU=9000` in `cluster/inventory.env` so
   `10-network.sh` stops warning.
4. Confirm **from a node, not from the Mac** (see the next section for why):
   `ssh clio.cgk.sh 'ping -c3 -M do -s 8972 10.10.10.123'` — with DF set, an 8972-byte payload
   only survives if every hop really carries the frame. A bulk TCP transfer is the other good check.

`clusterctl lan-mtu` applies the change **live with an auto-revert timer armed**, sends a full-size
DF ping, and persists only if that packet survives; otherwise the node puts itself back. That
matters because the interface you would use to undo a bad MTU is the one it breaks. Every host on
the segment — including any other machine plugged into that switch — must agree.

### Considered: a dedicated jumbo VLAN (10.10.20.0/24)

Not done, and not required — jumbo works today on the flat `10.10.10.0/24`. Recorded because the
reasoning is worth keeping.

**The argument for it.** MTU is a property of a *broadcast domain*, not of a switch. `10.10.10.0/24`
spans the UDW's other ports too, where everything else is at 1500, so the segment is genuinely
mixed. That is mostly survivable — TCP negotiates MSS down, and the UDW is a *router*, so unlike a
switch it does send the ICMP "fragmentation needed" that makes path-MTU discovery work — but it is
fragile for UDP and for anything that assumes it can emit a full-size frame. A dedicated VLAN
guarantees every host in the domain agrees on 9000, which is the standard shape for a storage or
compute segment.

**The argument against.** It buys robustness, not speed; the three hosts that matter are already
verified at 9000; and the migration touches more than it first appears.

**If you do it, the work is:**
1. UniFi: new network/VLAN + DHCP scope, assign the USW Flex XG ports to it.
2. Re-IP clio, urania and ceres's `en0`; update `LAN_GATEWAY` in `cluster/inventory.env`.
3. **Re-issue the server certificates.** They carry IP SANs (`10.10.10.149`, `10.10.10.155`), so
   they stop matching: `clio-pki server clio && clio-pki server urania && clusterctl nginx deploy`.
4. DNS re-points itself once UniFi reports the new addresses (cgk-dns-sync).
5. Leave the fabric alone — `10.51.50–53.x` is direct-attach, off the LAN entirely, and the NFS
   export ACL is written against fabric IPs, so none of it is affected.

### Do not test jumbo with macOS `ping`

macOS cannot build an ICMP datagram larger than **8192 bytes**. Past that you get:

```
$ ping -D -s 8972 clio.cgk.sh
ping: sendto: Message too long
```

That is `EMSGSIZE` raised **locally** — the packet never reaches the wire. It fires even with
fragmentation allowed, and even though `route -n get` reports MTU 9000, because it is a limit of
the raw-socket path in `ping`, not of the interface. The macOS *kernel* has no such problem: it
answers an 8972-byte ping sent from a Linux host, and 800 MB TCP transfers run clean in both
directions at MTU 9000.

This cost real time on 2026-08-07: it made a correctly-configured jumbo network look broken and
sent the investigation toward cabling. **Test from the Linux side** — `ping -M do -s 8972 <mac>` —
or with an actual TCP transfer. `clusterctl lan-mtu` now runs its probe on the node, aimed back at
`$SSH_CLIENT`, for exactly this reason. Ceiling on this Mac, for reference: payload 8164 is the
largest that works (8164 + 8 ICMP + 20 IP = 8192 exactly).

## Resolved: urania management is now wired
urania used to be reachable only over WiFi (`wlP9s9`, ~300 ms) because its wired port had no
carrier. It is now on `enP7s7` at 10Gbase-T, same as clio, and `wlP9s9` is down. Nothing in the
repo depends on the old `10.10.10.39` address.
