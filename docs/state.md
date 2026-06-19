# Current deployed state (snapshot)

Single source of truth for **what is actually running right now**. Design rationale lives in
[architecture.md](architecture.md); this is the as-deployed reality (it has diverged from the original
scale-out design into a **split**: one model role per box). Last updated 2026-06-19.

## Topology

| Node | Reachable | Running now | Role |
|---|---|---|---|
| **clio** | `clio` / `10.10.10.149` (1GbE wired) | **GPT-OSS-120B** (`local-big`) + **LiteLLM gateway** (`:4000`) | LLM capability model + the single endpoint |
| **urania** | `urania` (mDNS; wired `.155` has no sshd — use the name or fabric `10.51.50.12`) | **ComfyUI** (`:8188`) | image / video generation |

- Fabric: 4×200G ConnectX-7 RoCE, MTU 9000 / `active_mtu` 4096 (see [networking.md](networking.md)).
- The **coder** (`local-coder`, Qwen3-Coder-30B-FP8) is **parked** — urania was repurposed to ComfyUI.
  To bring it back you'd free a box and `clusterctl serve local-coder` (it'd then need a node).
- GUI is masked/headless on both nodes; passwordless sudo for `colby`; `colby` in the docker group.

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
`clusterctl status | models | serve <p> | shard <p> | use <model> | comfyui up|down [clio|urania] | logs <svc> [node]`

## Open items (yours, no rush)
1. **DNS:** deploy `cgk-dns-sync` on **eris** (`/Volumes/data02/personal/cgk-dns-sync`) + flip `cgk.sh` nameservers at 101domain to DNSimple → `clio.cgk.sh` etc. resolve everywhere.
2. **OFED/`apt`** on the nodes is wedged (DKMS on kernel 6.17.0-1018; cosmetic — RoCE works on the in-tree driver). Fix if you want host `apt` back.
3. Optional: re-enable the coder (needs a box), cross-mount model dirs over the fabric, urania wired mgmt.
