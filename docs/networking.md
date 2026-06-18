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

Management (how ceres reaches the boxes, by mDNS name): clio `10.10.10.149` (1GbE wired),
urania `10.10.10.39` (**WiFi** — wired port has no cable; see caveat below).

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

## Caveat worth fixing: urania management is on WiFi
urania's wired management port (`enP7s7`) has no carrier, so ceres reaches it over WiFi (~300 ms
latency observed). Fine for low-bandwidth control, but for robustness put urania on wired
management. The 200G fabric between the boxes is independent and unaffected.
