# Current deployed state (snapshot)

Single source of truth for **what is actually running right now**. Design rationale lives in
[architecture.md](architecture.md); this is the as-deployed reality (it has diverged from the original
scale-out design into a **split**: one model role per box). Last updated 2026-06-19.

## Topology

| Node | Reachable | Running now | Role |
|---|---|---|---|
| **clio** | `clio.cgk.sh` / `clio` / `10.10.10.149` (1GbE wired) | **ComfyUI** (`:8188`) + **LiteLLM gateway** (`:4000`); GPT-OSS-120B swappable | switchable: LLM capability model **or** image/video — plus the single endpoint |
| **urania** | `urania.cgk.sh` / `urania` (mDNS; wired `.155` has no sshd — or fabric `10.51.50.12`) | **ComfyUI** (`:8188`) | image / video generation |

### clio is dual-role — one at a time
A node has **one** 119 GiB unified-memory pool shared by CPU and GPU, so GPT-OSS-120B and ComfyUI
cannot coexist on clio (an OOM freezes the box). The systemd units declare `Conflicts=` on each other,
so starting one stops the other atomically:

```bash
clusterctl mode clio comfy   # -> ComfyUI at http://clio:8188 (local-big goes offline)
clusterctl mode clio llm     # -> GPT-OSS-120B back on the gateway (~20 min cold start)
clusterctl mode clio         # -> prints the current role
```

The gateway (`:4000`) stays up either way; while clio is in `comfy` mode, `local-big` returns an
upstream error and `clusterctl use claude` is the spillover.

Use `clusterctl mode`, not raw `systemctl`: `Conflicts=` prevents the two from *staying* up together
but imposes no ordering, and vLLM's startup free-memory check can fail if it races ComfyUI's
shutdown. `mode` stops the outgoing role synchronously first.

### Shared model store
The ComfyUI `models/` and `input/` trees are a **single copy on urania**, NFSv4.2-over-RDMA mounted
onto clio across the fabric (~1.5–1.8 GB/s). Add a model on either node, both see it. Consequence:
**clio's ComfyUI depends on urania being up.** Details: [comfyui-shared-store.md](comfyui-shared-store.md).

- Fabric: 4×200G ConnectX-7 RoCE, MTU 9000 / `active_mtu` 4096 (see [networking.md](networking.md)).
- **Management LAN is MTU 1500 — jumbo frames belong on the fabric only.** urania's `enP7s7` was
  wrongly set to 9000 (fixed 2026-08-07 via `nmcli`, persisted). On a 1500 switch that black-holes
  large packets: TCP connects, then SSH hangs and TLS dies at the certificate, with no ICMP
  "fragmentation needed" because a layer-2 switch does not send one. It presented as "urania is
  busy/unreachable" and only became constant once HTTPS made responses big enough to trip it.
  `10-network.sh` now warns if a management interface is not 1500. Note **ceres's `en0` is also
  MTU 9000**, which is why it was one end of the broken pair — worth setting to 1500 as well.
- The **coder** (`local-coder`, Qwen3-Coder-30B-FP8) is **parked** — urania was repurposed to ComfyUI.
  To bring it back you'd free a box and `clusterctl serve local-coder` (it'd then need a node).
- GUI is masked/headless on both nodes; passwordless sudo for `colby`; `colby` in the docker group.

## Access to ComfyUI (changed 2026-08-07)
ComfyUI is now **only** at `https://clio.cgk.sh/` and `https://urania.cgk.sh/`, behind nginx with a
**required client certificate**. Port 8188 is bound to `127.0.0.1` on both nodes — unreachable from
the LAN and from the other node. Enroll a device with `clio-pki client <name>`.
Full detail: [security.md](security.md).

## The one endpoint
- Gateway: **`http://clio:4000`** — Anthropic API at `/v1/messages` (Claude Code) **and** OpenAI API at `/v1` (aider/VS Code/curl).
- Master key: in `/opt/clio/gateway/gateway.env` on clio (`LITELLM_MASTER_KEY`); also written into `~/.clio/claude-code.env` by `clusterctl use`.
- Live model names: **`local-big`** (GPT-OSS-120B). `local-coder` is routed but has no backend while parked. `claude` (via `clusterctl use claude`) = your Max subscription.

## GPT-OSS-120B (`local-big`) specifics
- Image: `vllm-node-mxfp4` (eugr build); flags `--quantization mxfp4 --mxfp4-backend CUTLASS --load-format fastsafetensors`.
- **`--gpu-memory-utilization 0.70`, `--max-model-len 65536`** — higher util crash-loops on the unified-memory startup free-check (~86 GB free; page cache counts against it). Profile: `cluster/profiles/gpt-oss-120b.env`.
- First cold start is long (~20 min): ~63 GB download → load → **MXFP4/sm_121 kernels JIT-compile + CUDA-graph capture**. ~23 tok/s decode. Model cache: `/data/models/hf`.
- systemd unit `clio-vllm.service` reads `/opt/clio/active-profile.env` and survives reboot.

## How to use it (see [usage.md](usage.md) for full detail)
- **Claude Code:** `clusterctl use local-big && source ~/.clio/claude-code.env && claude` (separate shell; your normal `claude` stays on real Claude).
- **curl:** `curl http://clio:4000/v1/chat/completions -H "Authorization: Bearer <master key>" -H 'content-type: application/json' -d '{"model":"local-big","messages":[{"role":"user","content":"hi"}]}'`
- **VS Code:** Continue.dev → model `local-big`, apiBase `http://clio:4000/v1`.
- **ComfyUI:** `http://urania:8188` (or `http://10.10.10.155:8188`). Details: [comfyui-deployed.md](comfyui-deployed.md).

## Control (from ceres)
`clusterctl status | models | serve <p> | shard <p> | use <model> | mode <node> [llm|comfy|off] | comfyui up|down [clio|urania] | logs <svc> [node]`

## Open items (yours, no rush)
1. ~~**DNS**~~ — **done.** `clio.cgk.sh` / `urania.cgk.sh` resolve; `inventory.env` now uses the FQDNs
   (the bare mDNS names no longer resolve from ceres, which had silently broken every `clusterctl` verb).
2. **OFED/`apt` wedge — root cause identified 2026-08-07.**
   DOCA-OFED 25.10 ships `mlnx-ofed-kernel-dkms`, its own *out-of-tree* mlx5 driver. It does not
   compile against the running kernel **6.17.0-1018-nvidia**: the kernel's TLS-offload API changed,
   so `drivers/.../mlx5/core/en_accel/ktls_rx.c` passes the wrong pointer type to
   `tls_offload_rx_resync_async_request_{start,end}()`, and GCC 14's
   `-Werror=incompatible-pointer-types` makes it fatal. DKMS only ever built it for the previous
   kernel (6.11.0-1016-nvidia).
   Result: `mlnx-ofed-kernel-dkms` sits in `iF` (config failed) and the four packages depending on it
   (`doca-ofed`, `iser-dkms`, `isert-dkms`, `srp-dkms`) in `iU` (unpacked, never configured). Any
   `apt install/upgrade` retries that configure and aborts — which is why package installs on these
   nodes use `apt-get download`/`install -d` + explicit `dpkg -i`.
   **Nothing is actually broken at runtime:** the ConnectX-7s are driven by the kernel's *in-tree*
   `mlx5_core`/`mlx5_ib`, which is why RoCE, `ib_write_bw` and NFS-over-RDMA all work. The OFED
   userspace (`rdma-core`, `ibverbs-providers`, `perftest`) is packaged separately and is fine.
   **Clean fix when you want it:** `sudo apt-get remove --purge mlnx-ofed-kernel-dkms iser-dkms
   isert-dkms srp-dkms doca-ofed`. Simulated: it removes exactly those five and nothing else, and
   `apt` is unwedged permanently. You lose only iSER/SRP (RDMA storage targets), which this cluster
   does not use. The alternative is waiting for a DOCA release that supports 6.17.
3. ~~cross-mount model dirs over the fabric~~ — **done**, see [comfyui-shared-store.md](comfyui-shared-store.md).
4. Optional: re-enable the coder (needs a box), urania wired mgmt.
5. `clio-gateway` has been reporting `unhealthy` to Docker for weeks even though `/health/liveliness`
   answers — the container's own healthcheck, worth a look sometime.
6. clio still holds ~18 GB of now-redundant model downloads at `/data/comfyui/local-backup/` (superseded
   by the shared store). Reclaim with `sudo rm -rf /data/comfyui/local-backup` once you're happy.
