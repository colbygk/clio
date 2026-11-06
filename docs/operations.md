# clio cluster — Operations & As-Built Guide

This is the authoritative record of **what was done to clio and urania**, **what the tooling is and
where it comes from**, and **how to operate the cluster**. For design rationale see
[architecture.md](architecture.md); for model/quant specifics [models.md](models.md); for the fabric
[networking.md](networking.md); for image/video [comfyui.md](comfyui.md).

Hardware/OS baseline (both nodes, verified 2026-06-17): NVIDIA **DGX Spark**, **GB10** Grace-Blackwell
superchip (GPU compute capability **sm_121**), 20-core ARM (aarch64), **~119 GiB unified** LPDDR5X
memory, 3.7 TB NVMe. **DGX OS** = Ubuntu 24.04 + NVIDIA kernel `6.17.0-1018-nvidia`, driver
`580.159.03`, **CUDA 13.0**. Docker 29.2.1 + NVIDIA Container Toolkit 1.19.1. DOCA/MLNX-OFED 25.10,
4× **ConnectX-7** ports (RoCEv2).

---

## 1. As-built — what was changed on the hosts

Everything below is idempotent and reproducible from this repo (`clusterctl bootstrap`). Listed as a
record of the live state.

### Access / control plane
- **Passwordless sudo** for `colby` (`/etc/sudoers.d/90-*-nopasswd`, mode 440) — *you* enabled this;
  it lets `clusterctl` run privileged steps non-interactively.
- `colby` added to the **docker** group (effective after a full re-login; services run as root regardless).
- **Node↔node passwordless SSH** over the fabric (ed25519 keypairs exchanged into `~/.ssh/authorized_keys`)
  — required by the sharded-mode launcher.
- Repo deployed to **`/opt/clio`** on each node (`cluster/*` flattened in, plus `scripts/`).

### Base system (`scripts/bootstrap/00-common.sh`)
- NVIDIA container runtime registered with Docker (`/etc/docker/daemon.json`), GPU-in-container verified.
- Data dirs created: `/data/models` (HF cache), `/data/comfyui`, `/opt/clio`.

### Fabric (`scripts/bootstrap/10-network.sh`) — the core networking change
- Wrote **`/etc/netplan/40-cx7.yaml`** (original saved to `*.clio-orig`), renderer **NetworkManager**.
- All **four** ConnectX-7 rails brought up on four /24s: `10.51.50–53.x` (clio `.13`, urania `.12`).
- **MTU raised 1500 → 9000**, which lifts RoCE `active_mtu` **1024 → 4096** (verified) — the main
  throughput unlock. Jumbo frames confirmed end-to-end. Persists across reboot.

### Inference stack (`scripts/bootstrap/20-vllm.sh`)
- Cloned [`eugr/spark-vllm-docker`](https://github.com/eugr/spark-vllm-docker) to `/opt/clio/spark-vllm-docker`.
- Built **`vllm-node:latest`** (~19 GB) on both nodes (compiles vLLM + FlashInfer for sm_121).
- Installed systemd units **`clio-vllm.service`** (both) and **`clio-vllm-shard.service`** (clio).
- ⚠️ **`vllm-node-mxfp4`** build **failed** (exit 255, mid-compile) → `gpt-oss-120b` profile is not yet
  runnable. Open item.

### Media stack (`scripts/bootstrap/30-comfyui.sh`)
- Cloned [`mmartial/ComfyUI-Nvidia-Docker`](https://github.com/mmartial/ComfyUI-Nvidia-Docker) to
  `/opt/clio/comfyui-docker`; installed **`clio-comfyui.service`** (left disabled — on-demand).
- Custom nodes are cloned into `basedir/custom_nodes/` per node. The image installs ComfyUI's and
  the Manager's requirements but **not** each custom node's — do that in the container venv, as
  uid 1000 (`docker exec` defaults to uid 1025, which cannot write the venv):
  `docker exec -u 1000 comfyui-nvidia /comfy/mnt/venv/bin/python -m pip install -r /basedir/custom_nodes/<node>/requirements.txt`.
  The venv is bind-mounted from `run/`, so this survives restarts.

### Shared model store (`scripts/bootstrap/35-comfyui-share.sh`)
- One copy of `basedir/{models,input}` on urania, exported NFSv4.2-over-RDMA to clio across the
  fabric (~1.5–1.8 GB/s, vs ~970 MB/s for TCP `nconnect=16`). Ordered: the store host must export
  before the other node mounts. Full detail + failure modes: [comfyui-shared-store.md](comfyui-shared-store.md).

### Gateway (`scripts/bootstrap/40-gateway.sh`, clio only)
- Pulled `ghcr.io/berriai/litellm:main-stable`; created `/opt/clio/gateway/gateway.env`
  (auto-generated master key; `ANTHROPIC_API_KEY` left blank — not needed, see spillover).
- Installed + enabled **`clio-gateway.service`** → listens on **`clio:4000`**.

### Headless
- `graphical.target` and `display-manager.service` (gdm) **masked**; default target **`multi-user.target`**.
  (Frees ~0 RAM on these boxes — they had no real desktop session — but correct hygiene for headless servers.)

### Currently serving
- Profile **`local-coder`** active on both nodes: `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8`, served as
  `local-coder`, `--gpu-memory-utilization 0.60`. Verified through the gateway end-to-end.

---

## 2. The tooling — what it is, and where it comes from

The stack is deliberately **thin custom glue over heavy upstream/vendor components**. Nothing
re-implements inference, routing, or GPU plumbing; we orchestrate proven projects.

| Layer | Component | Provenance | NVIDIA/DGX-specific? |
|---|---|---|---|
| **Control CLI** | `bin/clusterctl` | **Custom** (this repo). ~260 lines of POSIX-ish bash, runs on ceres, drives nodes over `ssh`/`rsync`/`curl`. No runtime deps. | No — generic bash. Assumes the two named hosts. |
| **Node setup** | `scripts/bootstrap/00–40` | **Custom** (this repo). Idempotent bash run as root on the nodes. | Partly — assumes Ubuntu/`apt`, `netplan`, the specific CX-7 interface names, GB10. |
| **Service mgmt** | `cluster/systemd/*.service` | **Custom unit files**, but **standard systemd** (Ubuntu). | No. |
| **Gateway** | **LiteLLM** (`ghcr.io/berriai/litellm`) | **Upstream OSS** ([BerriAI/litellm](https://github.com/BerriAI/litellm)). Provides the OpenAI **and** Anthropic-compatible endpoint + model routing/load-balancing/fallbacks. | No — provider-agnostic. We just configured it (`cluster/gateway/config.yaml`). |
| **Inference engine** | **vLLM** | **Upstream OSS** ([vllm-project/vllm](https://github.com/vllm-project/vllm)) — a major serving engine. | No (engine), but the *build* targets sm_121. |
| **vLLM build/run harness** | `build-and-copy.sh`, `launch-cluster.sh` | **Community third-party** ([eugr/spark-vllm-docker](https://github.com/eugr/spark-vllm-docker)). Compiles vLLM + FlashInfer for GB10, handles fastsafetensors, MXFP4, and Ray multi-node topology. | **Yes — DGX-Spark-specific.** Built for `sm_121`/GB10. |
| **Image/video** | **ComfyUI** | **Upstream OSS** ([comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI)). | No. |
| ↳ container | **ComfyUI-Nvidia-Docker** | **Community** ([mmartial](https://github.com/mmartial/ComfyUI-Nvidia-Docker)); has a `compose-dgx_spark.yaml` (SageAttention for sm_121). | **Yes — Spark-tuned.** |
| ↳ parallelism | **ComfyUI-Distributed** | **Community** ([robertvoy](https://github.com/robertvoy/ComfyUI-Distributed)). Data-parallel job spread across both nodes. | No. |
| **Containers** | **Docker CE** 29.x | Upstream Docker. | No. |
| ↳ GPU access | **NVIDIA Container Toolkit** | **NVIDIA.** Exposes the GPU to containers (`--runtime nvidia`). | **Yes — NVIDIA.** |
| **Fabric** | netplan + NetworkManager | Ubuntu. | No (config), but… |
| ↳ NIC / RDMA | **ConnectX-7**, **DOCA/MLNX-OFED**, RoCEv2 | **NVIDIA/Mellanox.** | **Yes — NVIDIA networking.** |
| ↳ collectives | **NCCL** (sharded mode only) | **NVIDIA.** | **Yes.** |
| **Platform** | **DGX OS** (Ubuntu 24.04 + `*-nvidia` kernel, GB10 driver, CUDA 13) | **NVIDIA.** | **Yes — the whole base image.** |

**Summary in one line:** the *brains* are upstream OSS (vLLM, LiteLLM, ComfyUI); the *GB10-specific
build magic* is a community project (eugr); the *GPU/NIC/OS substrate* is NVIDIA (DGX OS, CUDA,
Container Toolkit, ConnectX-7/OFED, NCCL); and the **`clusterctl` + bootstrap + systemd + profiles glue
that ties it into a one-command cluster is custom to this repo.** It is **not** a packaged NVIDIA
product — NVIDIA ships the playbooks/primitives, this repo is the opinionated automation on top.

---

## 3. How to operate the cluster (deep reference)

All commands run on **ceres** as `clusterctl <verb>` (installed via `ceres/install.sh` →
`~/.local/bin/clusterctl`). Each verb's under-the-hood mechanics and manual equivalent are given so you
can operate it even if `clusterctl` isn't handy.

### `clusterctl preflight [node]`
What `bootstrap` checks before it touches anything; safe to run any time. Two guards, both added
after this repo lost track of reality twice:

- **Destination safety.** `rsync_repo` syncs with `--delete`, and `/opt/clio` is a shared namespace:
  the bootstrap scripts `git clone` `comfyui-docker/` (the whole ComfyUI basedir — models, LoRAs,
  workflows) and `spark-vllm-docker/` into it, neither of which is in this repo. They are protected
  by the `RSYNC_PROTECT` list. The guard then refuses to sync if `/opt/clio` holds **any other**
  mountpoint or git checkout, so a future script that clones somewhere new is caught rather than
  silently wiped. This matters most on clio, whose `basedir/models` is an **NFS mount of urania** —
  rsync does not stop at filesystem boundaries, so deleting through it would destroy urania's data.
- **Dirty working tree.** `bootstrap` deploys the **working tree, not `HEAD`**. That is deliberate
  (it lets you test before committing) but it is how the gateway's split routing ran on the nodes
  for seven weeks while the repo described something else. `bootstrap` now lists uncommitted changes
  under `cluster/` and `scripts/` and asks before continuing; `CLIO_ALLOW_DIRTY=1` skips the prompt.

> **`--dry-run` is not a safety check on ceres.** macOS ships **openrsync**, which prints no
> `deleting` lines even with `-n -v` — a dry run there reports zero deletions while the real run
> removes the tree. Preview from a node (real rsync 3.x), or trust `preflight` instead.

### `clusterctl status`
Health of both nodes (GPU, services, fabric `active_mtu`) + gateway health.
*Under the hood:* SSHes each node for `nvidia-smi`, `systemctl is-active`, `ibv_devinfo`; curls the
gateway’s `/health/liveliness`.

### `clusterctl serve <profile>` — scale-out (default)
Runs the model from `cluster/profiles/<profile>.env` independently on **both** nodes; the gateway
load-balances. *Mechanics:* stops `clio-vllm-shard` on the head, writes the profile (+`VLLM_PORT`) to
`/opt/clio/active-profile.env` on each node, `systemctl restart clio-vllm`. That unit runs
`cluster/run/serve-node.sh`, which does a `docker run` of the eugr image (`--privileged --runtime nvidia
--network host --ipc=host`) executing `vllm serve <model> …`. First run downloads the model to
`/data/models/hf`.
*Manual:* `ssh clio 'sudo systemctl restart clio-vllm'` after editing `/opt/clio/active-profile.env`.

### `clusterctl shard <profile>` — scale-up (on demand)
Runs **one** model spanning both nodes via Ray, **pipeline-parallel (PP=2)**. Use only for models too
big for one 119 GB box. *Mechanics:* stops `clio-vllm` on both, writes `/opt/clio/shard-profile.env` on
the head, starts `clio-vllm-shard` → `cluster/run/serve-shard.sh` → eugr `launch-cluster.sh` (brings up
the worker on urania over the fabric; sources `cluster/nccl.env`). Mutually exclusive with scale-out.

### `clusterctl down`
Stops `clio-vllm` on both and `clio-vllm-shard` on the head. Gateway stays up.

### `clusterctl use <model>` — point Claude Code (ceres-side, instant)
Writes `~/.clio/claude-code.env`. Then `source ~/.clio/claude-code.env` in the shell you run `claude`.
- `clusterctl use local-coder` / `local-big` → sets `ANTHROPIC_BASE_URL=http://clio:4000`, the master
  key, and `ANTHROPIC_MODEL`.
- `clusterctl use claude` (or `max`) → **unsets** those, so Claude Code falls back to your **Max
  subscription** (no API key, no metered cost). This is the spillover path.

### `clusterctl mode <node> [llm|studio|private|off]` — swap a node's role
A node has **one** 119 GiB unified-memory pool shared by CPU and GPU, so it can host a vLLM model
server **or** ComfyUI, never both (an OOM here freezes the box). `mode` is the switch:
- `mode clio studio` → starts `clio-comfyui` as the **studio** profile; stops vLLM first. (`comfy`
  is an alias for `studio`.)
- `mode clio private` → the same container as the **private** profile, on `:8443`. See
  [comfyui-profiles.md](comfyui-profiles.md).
- `mode clio llm` → starts `clio-vllm` (model per `/opt/clio/active-profile.env`); stops ComfyUI.
- `mode clio off` → stops both, leaving the pool free.
- `mode clio` (no role) → prints the node's current role.

*Mechanics:* two layers, and the distinction matters.
- **`Conflicts=`** on the units (`clio-comfyui.service` ↔ the two vLLM units) guarantees the roles can
  never *stay* up together, including for someone running `systemctl` directly.
- **But `Conflicts=` implies no ordering** — on a bare `systemctl start` the incoming role begins
  while the outgoing one is still shutting down, and the two briefly overlap. vLLM checks free
  unified memory at startup, so a comfy→llm swap during that overlap can fail the check and
  crash-loop. `clusterctl mode` therefore does an explicit synchronous `systemctl stop` of the
  outgoing role (plus `reset-failed`, since interrupting a mid-startup vLLM leaves the unit failed)
  before starting the incoming one. Prefer `clusterctl mode` over raw `systemctl` for this reason.

Cost of a swap: ComfyUI is up in seconds; coming back to `llm` pays the model's cold start (~20 min
for GPT-OSS-120B, mostly JIT + CUDA-graph capture).

### `clusterctl comfyui up|down|sync`
Starts/stops `clio-comfyui` on both nodes, each in its current profile, or one with `comfyui up clio`.
`sync` pushes the profile files and rebuilds the studio's model list (after editing
`cluster/comfyui/studio-models.txt`) without a full repo sync. Same
`Conflicts=` applies — bringing ComfyUI up stops any vLLM server on that node. For parallel jobs
across both, add **ComfyUI-Distributed** via the in-app Manager (host on clio, urania as a Remote
worker) — see [comfyui.md](comfyui.md).

### `clusterctl nginx deploy|status [node]` — the HTTPS + mutual-TLS front door
`deploy` pushes the node's server certificate, the CA certificate (used only to *verify clients*)
and the site config, installs nginx if absent, reloads, and rebinds ComfyUI's published port to
`127.0.0.1`. Certificates come from `bin/clio-pki` on ceres; **the CA private key never leaves
ceres**. `status` reports nginx state, certificate expiry and the current 8188 binding.
*Gotcha:* nginx here is installed with `apt-get install -d` + explicit `dpkg -i` — a plain
`apt-get install` aborts on the wedged DOCA/OFED DKMS packages. Full model: [security.md](security.md).

### `clusterctl models`
Lists profiles and the gateway model names.

### `clusterctl nccl-test`
RoCE per-rail bandwidth check (`ib_write_bw`). Target ~190 Gb/s aggregate; ~22–24 GB/s per cable in
NCCL. Remember: **no GPUDirect RDMA on GB10** (by design) — `NET/IB` without `/GDRDMA` is normal.

### `clusterctl logs <vllm|shard|gateway|comfyui> [node]`
Tails the relevant `journalctl -u …` on the chosen node.

### Switching models — the mental model
A model is just a profile + a `systemctl restart`. The **gateway endpoint never changes**, so ceres /
Claude Code config is static. `serve` swaps the backend; `use` swaps which gateway model name Claude
Code asks for. Adding a model = drop a `cluster/profiles/<name>.env`, then `serve`/`shard` it; for a new
gateway-visible name add it to `cluster/gateway/config.yaml` and restart the gateway.

### Load-balanced vs. spanning — the two ways to use the cluster

There are two fundamentally different ways the two Sparks combine, and **which one is even available
depends on the workload (LLM vs ComfyUI).**

**(A) Load-balanced / "scale-out" — each box runs a full copy; work is distributed.**
- *What it is:* the model is loaded **independently on each Spark**; requests (LLM) or jobs (ComfyUI)
  are spread across them. Memory is **not** combined — the model must fit in **one** box (~110 GB usable).
- *LLMs:* `clusterctl serve <profile>`. The gateway load-balances `local-coder`/`local-big` across both.
- *ComfyUI:* run on both (`clusterctl comfyui up`) + **ComfyUI-Distributed** to auto-spread jobs.
- *You get:* ~2× **concurrent throughput** + **redundancy**. You do **not** get lower single-stream
  latency, and you do **not** get a bigger model than fits one box.

**(B) Spanning / "scale-up" — one model split across both boxes. LLM-ONLY.**
- *What it is:* a single model is **sharded across both nodes** over the 200 G fabric using vLLM + Ray
  with **pipeline parallelism (PP=2)**. This is the only way to **combine the two boxes' memory** (→ run
  ~235 B-class models that can't fit one box).
- *LLMs:* `clusterctl shard <profile>` (e.g. `shard-qwen3-235b`). Mutually exclusive with scale-out.
- *Tradeoff:* higher latency, more moving parts, ~10–15 tok/s on a 235 B-class model — **capacity, not speed.**
- *Only vLLM/LLMs can do this.* See `cluster/profiles/shard-qwen3-235b.env` and `cluster/nccl.env`.

**⚠️ ComfyUI cannot span (read this before planning very large media models).**
Core ComfyUI **cannot shard one generation across machines and does not pool VRAM** — there is no
ComfyUI equivalent of PP=2. So **the two boxes' memory does not combine for a single image/video
model**: every generation is bounded by **one Spark's ~119 GB unified memory**. The second Spark only
adds **throughput** (a second concurrent job), not a bigger single model. To run "very large" media
models, the levers are all **single-box** — see [comfyui.md → Very large models](comfyui.md#very-large-models-on-one-spark).

---

## 4. Lifecycle & maintenance

- **Reboot:** `clio-vllm` and `clio-gateway` are `enabled` → they come back; `clio-vllm` re-reads
  `active-profile.env` and resumes the last model. ComfyUI is on-demand (won’t auto-start). Fabric
  config (netplan) and headless default persist.
- **Update an image:** re-run `ssh <node> 'sudo BUILD_MXFP4=1 CLUSTER_DIR=/opt/clio bash
  /opt/clio/scripts/bootstrap/20-vllm.sh'` (it `git pull`s eugr and rebuilds), or just rebuild inside
  `/opt/clio/spark-vllm-docker` with `./build-and-copy.sh`.
- **Change repo files:** edit here on ceres, then `clusterctl bootstrap` re-syncs `/opt/clio` and re-runs
  the (idempotent) steps. (Or `rsync` `cluster/`+`scripts/` to `/opt/clio` yourself.)
- **Models on disk:** live under `/data/models/hf` per node (independent copies). 3.7 TB each.
- **Secrets:** `cluster/gateway/gateway.env` (gitignored) holds the gateway master key and (optional)
  `ANTHROPIC_API_KEY`.

---

## 5. Troubleshooting playbook

| Symptom | Cause / fix |
|---|---|
| ComfyUI runs several times slower than usual | Usually a longer video (frames). If the log also shows `loaded partially`, page cache: `clusterctl freemem` (~8%) ([details](comfyui-deployed.md#slow-generations-check-the-frame-count-first-then-page-cache)). |
| vLLM dies at startup: *"Free memory … less than desired"* | Unified-mem startup check (page cache can lower free). Lower `VLLM_GPU_UTIL` in the profile and re-`serve`. |
| `serve` says *"Unit clio-vllm.service not found"* | Units weren’t installed (e.g., a failed bootstrap step). Re-run `20-vllm.sh` or install `cluster/systemd/*.service` to `/etc/systemd/system` + `daemon-reload`. |
| Host `apt` hangs / dpkg errors | Known: DOCA/MLNX-OFED 25.10 DKMS is half-configured on kernel `6.17.0-1018-nvidia`. Doesn’t affect the cluster (RoCE uses the in-tree `mlx5` driver). Avoid host `apt`; proper fix needs the `-1014` kernel or NVIDIA’s OFED update. |
| Only one node gets traffic | LiteLLM `least-busy` sends serial requests to whichever node is idle; spread shows under concurrency. Switch to `simple-shuffle` in `config.yaml` for even round-robin. |
| `active_mtu` not 4096 | Run `10-network.sh` on **both** nodes (RoCE negotiates the min); verify with `ibv_devinfo`. |
| NCCL slow (~1–3 GB/s) in shard mode | TCP fallback. `NCCL_NET_PLUGIN=none` (set in `nccl.env`); confirm log says `NET/IB` not `NET/Socket`. |

---

## 6. Open items (as of 2026-06-18)
1. **`vllm-node-mxfp4` build failed** (exit 255) → `gpt-oss-120b` unavailable until rebuilt with full logging.
2. **OFED/`apt` wedged** (cosmetic for us; fix to restore host `apt`).
3. **ComfyUI** not yet runtime-tested; **sharded mode** not yet runtime-tested.
4. ~~urania management on WiFi~~ — **done**: wired 10Gbase-T, and the whole management LAN now
   runs MTU 9000 (see [networking.md](networking.md)).
