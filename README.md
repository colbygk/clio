# clio — a two-node DGX Spark cluster for coding LLMs + generative media

Operate two NVIDIA DGX Sparks (**clio** + **urania**, GB10, 119 GB each, joined by a 4×200G RoCE
fabric) from one machine (**ceres**), with minimal effort to switch models and functions.

- **Coding:** Claude Code on ceres points at a single gateway that serves local models on the
  Sparks and **spills over to the real Claude API** for heavy lifts / double-checks.
- **Media:** ComfyUI on each Spark, parallelized across both via ComfyUI-Distributed.
- **Two modes:** *scale-out* (a model per Spark, load-balanced — the default) and *scale-up* (one
  big model sharded across both). Switching is one command; ceres config never changes.

> New here? Read `docs/architecture.md` first, then `docs/models.md`, `docs/networking.md`,
> `docs/comfyui.md`. Everything is grounded in a 2026-06-17 inspection of the actual machines and
> current sources — see those docs for citations and the reality checks (bandwidth limits, no
> GPUDirect RDMA, MXFP4 caveats).

## Quick start

```bash
# On ceres (this machine):
./ceres/install.sh                 # puts `clusterctl` on your PATH
clusterctl bootstrap               # one-time. Configures BOTH Sparks. Prompts for the sudo
                                   # password ONCE per node (to enable passwordless sudo), then:
                                   #   fabric MTU 9000 + all 4 rails, docker group, builds the
                                   #   vLLM images (LONG — compiles from source), ComfyUI, gateway.

clusterctl serve local-coder       # run the daily coding model on both Sparks (scale-out)
clusterctl use   local-coder       # point Claude Code here
source ~/.clio/claude-code.env     # ...then just run `claude` as usual
```

## Everyday use

```bash
clusterctl status                  # nodes, GPUs, services, fabric MTU, gateway health
clusterctl models                  # list model profiles

# Switch what the cluster serves:
clusterctl serve gpt-oss-120b      # heavier local model, both nodes
clusterctl shard shard-qwen3-235b  # one model too big for a single box, across both (PP=2)
clusterctl down                    # stop serving

# Switch what Claude Code talks to (re-source ~/.clio/claude-code.env after):
clusterctl use local-big           # local capability model
clusterctl use claude              # spill over to your Claude Max subscription (no API key)

# Media:
clusterctl comfyui up              # ComfyUI on both: http://clio:8188 , http://urania:8188
clusterctl comfyui down

# Fabric / debugging:
clusterctl nccl-test               # RoCE bandwidth across the fabric
clusterctl logs vllm clio          # tail a service
```

## Adding a model
Drop a `cluster/profiles/<name>.env` (copy an existing one), then `clusterctl serve <name>`
(or `shard <name>`). For a new gateway-visible name or spillover target, add it to
`cluster/gateway/config.yaml` and restart the gateway. See `docs/models.md` for what runs well.

## Layout
```
bin/clusterctl            the one command you run on ceres
ceres/install.sh          installs clusterctl on ceres
cluster/inventory.env     single source of truth (IPs, ifaces, ports, hardware)
cluster/profiles/         model profiles (what to serve)
cluster/run/              node launch scripts (driven by systemd)
cluster/systemd/          service units (auto-restart, reboot-survival)
cluster/gateway/          LiteLLM gateway (Claude Code endpoint + spillover)
cluster/nccl.env          RoCE/NCCL tuning for sharded mode
scripts/bootstrap/        idempotent node setup, ordered 00 -> 40
docs/                     architecture, models, networking, comfyui, operations
                          (as-built + tooling + how-to), generative-sharding (image/video)
```

## Important reality checks (don't skip)
- GB10 decode is **memory-bandwidth-bound (~273 GB/s)**. Clustering raises capacity/throughput,
  **not** single-stream typing speed. Run a model that fits one box for best latency.
- **No GPUDirect RDMA on GB10** (by design). NCCL works over RoCE anyway; `NET/IB` without
  `/GDRDMA` is normal. ~22–24 GB/s/cable realistic.
- **MXFP4 (GPT-OSS-120B)** needs the `vllm-node-mxfp4` image + CUTLASS backend, or output is broken
  on stock vLLM.
- urania's management is on **WiFi** today — works, but wire it for robustness (`docs/networking.md`).
