# Architecture

```
                 ceres (your Mac)
                 Claude Code ──ANTHROPIC_BASE_URL──┐
                 clusterctl (SSH control) ─────────┼──────────────┐
                                                    │              │ (mgmt LAN, by name)
                                                    ▼              ▼
   ┌──────────────────────── clio (HEAD) ───────────────┐   ┌──── urania ────────┐
   │  LiteLLM gateway :4000                              │   │                    │
   │   • /v1/messages (Anthropic)  ← Claude Code         │   │                    │
   │   • routes local-coder/local-big → both vLLMs       │   │                    │
   │   • spillover: claude-* → real Anthropic API        │   │                    │
   │  vLLM :8000  (clio-vllm.service)  ◀── load-balanced ──▶ vLLM :8000           │
   │  ComfyUI :8188 (on demand)                          │   │  ComfyUI :8188     │
   └───────────────────────┬─────────────────────────────┘   └──────┬─────────────┘
                           └────── 4× 200G RoCE fabric (10.51.50–53.x) ──────┘
                              service traffic + NCCL (sharded mode only)
```

## Two modes, one switch

**Scale-out (default).** `clusterctl serve <profile>` runs the chosen model independently on each
Spark; the gateway load-balances across them. Benefits: ~2× concurrent throughput (multiple agent
sessions), full redundancy (one box down → other serves), trivial model switching. Single-stream
latency = one Spark's bandwidth-bound speed (clustering can't beat that — see docs/models.md).

**Scale-up (on demand).** `clusterctl shard <profile>` runs ONE model across both nodes via Ray
with **pipeline parallelism (PP=2, TP=1)** — ~2.2× faster than TP over this link. Use only for
models too big for one 119 GB box (e.g. 235B-class). Expect ~10–15 tok/s: capacity, not speed.
Mutually exclusive with scale-out (clusterctl stops one before starting the other).

## Why this shape

- **Single endpoint on ceres.** Claude Code only ever knows `http://clio:4000`. Switching models,
  scaling out, or sharding never changes ceres config — `clusterctl use <model>` just changes which
  gateway model name Claude Code asks for.
- **Spillover uses your Max subscription, not the API.** `clusterctl use local-coder|local-big`
  points Claude Code at the Sparks; `clusterctl use claude` clears the env so Claude Code falls back
  to its normal Max login (no API key, no metered cost). The gateway *also* has optional
  `claude-opus-4-8`/`claude-sonnet-4-6` routes to the real Anthropic API — those only work if you
  add an `ANTHROPIC_API_KEY` to `gateway.env` (separate Console billing), and are mainly useful for
  non-Claude-Code clients (aider/scripts). For Claude Code, prefer `clusterctl use claude`.
- **eugr scripts do the hard vLLM work.** `build-and-copy.sh` builds the sm_121 image;
  `launch-cluster.sh` handles Ray + fabric topology for sharding. We wrap them in systemd for
  auto-restart and reboot-survival, and drive them from `clusterctl`.
- **State on the nodes is tiny and declarative:** `/opt/clio/active-profile.env` (scale-out) or
  `/opt/clio/shard-profile.env` (scale-up) + a systemd restart. Easy to reason about and recover.

## Files → roles
- `cluster/profiles/*.env` — what to serve (model, image, quant flags, mode).
- `cluster/run/serve-{node,shard}.sh` — how a node launches it (read by systemd).
- `cluster/systemd/*` — robustness (restart, boot).
- `cluster/gateway/*` — the Claude Code endpoint + spillover.
- `cluster/nccl.env` — fabric correctness for sharded mode.
- `scripts/bootstrap/*` — idempotent one-time node setup, ordered 00→40.
- `bin/clusterctl` — the only thing you run on ceres.
