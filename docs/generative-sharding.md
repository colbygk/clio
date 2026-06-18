# Sharding image/video generation across the clio cluster

How to handle **large image and video diffusion models** on two DGX Sparks — when to keep it on one
box, when (if ever) to split a model across both, and how each plugs into a ComfyUI workflow.
Grounded in a 2026-06-18 research pass; sources at the bottom. Companion to [comfyui.md](comfyui.md).

> **Scope:** this is about **diffusion (image/video)** only. LLM cross-node sharding is a different,
> already-wired path (`clusterctl shard`, vLLM pipeline-parallel) — see [operations.md](operations.md).

---

## TL;DR / verdict

1. **You almost never need to span for diffusion.** Every open image/video model in mid-2026 fits in
   one 119 GiB Spark — most at full BF16, all once quantized. The single biggest (HunyuanImage-3.0,
   80B MoE, ~169 GB BF16) drops to ~45 GB at INT4. There is **no open image/video model that fails to
   fit one node even quantized.**
2. **Cross-node diffusion sharding is real but ill-suited to this link.** The only Ethernet/RoCE-tolerant
   weight-sharding method is **PipeFusion** (in **xDiT**). On our ~23 GB/s RoCE link (no NVLink, no
   GPUDirect RDMA), the best published 2-node-over-Ethernet diffusion benchmark is **~1.16× at 1024px
   and *no* gain at 2048px** — communication-dominated. It buys capacity you don't need and speed you
   won't get. No one has publicly demonstrated a single video job split across two DGX Sparks.
3. **Recommended:** run the big model on **one** Spark with **quantization + tiling/offload**; use the
   **second** Spark for **parallel throughput** (render farm) or large-LLM work — the job it's built for.

The rest of this doc: why diffusion differs from LLMs (§1), the decision tree (§2), the recommended
single-node toolkit + fit table (§3), and — because you asked — the **experimental cross-node path**
and its ComfyUI integration (§4), plus how to actually use the second box (§5) and Spark build caveats (§6).

---

## 1. Why diffusion is different from LLMs (this is the crux)

For **LLMs**, *weights* are the binding memory constraint, so splitting weights across nodes
(pipeline/tensor parallel) yields real capacity — that's why `clusterctl shard` exists and works.

For **diffusion**, two things flip:
- **Weights are small.** The largest open DiT is FLUX.2 (32B, ~90 GB BF16); most are <40 GB. They fit
  one box trivially. So "split because weights don't fit" describes a capability that exists but a
  problem that, for open models on 119 GB, basically doesn't.
- **The memory blowup is *activations*, not weights** — video latents grow with resolution × frames.
  (xDiT's own example: CogVideoX needs ~12 GB of weights but OOMs a 40 GB A100 on a 10 s clip.) The fix
  for that is **tiled VAE + sequence handling on one node** (which *replicates* weights), **not**
  cross-node weight-sharding.

Key distinction used throughout:
- **Capacity-sharding** (run a model bigger than one GPU): FSDP, **PipeFusion**, tensor-parallel.
- **Speed-parallel** (model replicated, work split — faster only): sequence/context parallel
  (Ulysses/Ring/USP), CFG-parallel, data-parallel. *These don't raise the memory ceiling.*

---

## 2. Decision tree

```
Does the model fit one Spark at BF16?  ── yes ──► run single-node. Done.
        │ no / tight
        ▼
Quantize: GGUF (Q8→Q4_K_M) or NVFP4/FP8     ── fits ──► run single-node. (≈always lands here.)
        │ still tight on a long/high-res VIDEO?
        ▼
Tiled VAE + block-swap (activation memory)  ── fits ──► run single-node.
        │ STILL doesn't fit (essentially never for open models)
        ▼
Experimental cross-node span via xDiT/PipeFusion (§4) — accept comms-bound, sublinear, unproven on Spark.
```

---

## 3. Recommended path — single-node capacity toolkit

119 GB unified is huge for diffusion; it runs models that OOM any consumer GPU. The catch is **speed**,
not fit: GB10 memory bandwidth is ~273 GB/s, so denoising is bandwidth-bound — models **fit but run
~2–2.6× slower than a 5090**. Quantization here mostly helps you *fit/headroom*, sometimes speed.

**Fit on one 119 GiB node (BF16 unless noted):**

| Model | Full pipeline | One node? |
|---|---|---|
| Wan 2.1 14B / Wan 2.2 A14B (both experts) | ~40 / ~68 GB | yes, headroom |
| HunyuanVideo 13B | ~50 GB | yes |
| LTX-2 (19B/22B), CogVideoX 5B, Mochi-1 10B | ≤~29 GB | yes |
| FLUX.1-dev 12B | ~34 GB (peak ~95 GB on Spark) | yes |
| FLUX.2-dev 32B | ~112 GB BF16 (Mistral-24B encoder dominates) | yes (tight; Q4 removes doubt) |
| Qwen-Image ~20B | ~57 GB (quant-sensitive) | yes |
| HunyuanImage-3.0 80B MoE | ~169 GB BF16 / ~45 GB INT4 | **only quantized** |

**Tools to use on the node (all single-box):**
- **Quantization:** NVFP4 / FP8 (Blackwell-native; GB10 supports NVFP4) and **GGUF via
  [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)** (Wan/FLUX/Hunyuan; Q4_K_M ≈ 31% of BF16).
  [nunchaku SVDQuant INT4](https://github.com/nunchaku-ai/nunchaku) for fastest FLUX (speedup measured
  on a 4090, not Spark — flag).
- **Activation headroom (long video):** **tiled VAE decode** (the big one) + **block-swap** via
  [kijai WanVideoWrapper](https://github.com/kijai/ComfyUI-WanVideoWrapper).
- **Local capacity borrow (one node, multiple "devices"):**
  [ComfyUI-MultiGPU / DisTorch2 (pollockjj)](https://github.com/pollockjj/ComfyUI-MultiGPU) spreads one
  model's layers across GPU + CPU RAM. On unified memory this is largely moot for *fitting* (it's all
  one pool) but useful for explicit placement. Single-node only.
- **Don't** rely on `--lowvram` (near-no-op since ComfyUI's 2026-03-25 dynamic-VRAM allocator).

---

## 4. Experimental: spanning one diffusion model across both Sparks

Only attempt this if a model genuinely won't fit even quantized (rare) **or** you want to experiment.
Expect it to be **communication-bound and sublinear** on our link, and **unproven on DGX Spark**.

**The only realistic engine:** **[xDiT/xfuser](https://github.com/xdit-project/xDiT)** using
**PipeFusion** (weight-sharding, layer-count-independent comms → the one method that tolerates a slow
link). Inside ComfyUI, **[raylight (komikndr)](https://github.com/komikndr/raylight)** is the only node
that wraps xDiT USP+FSDP for true single-model splitting (multi-node via Ray is technically possible but
undocumented/unstable as of v1.6.0, 2026-06-17).

**Transport / NCCL.** Reuse `cluster/nccl.env` (multi-rail, `NCCL_NET_PLUGIN=none`, NVLS off). Two GB10
specifics are mandatory or it hangs/falls back:
- `NCCL_NET_GDR_LEVEL=0` — GB10 has **no GPUDirect RDMA**; without this NCCL init can hang.
- confirm the data path is `NET/IB` (not `NET/Socket`) with `NCCL_DEBUG=INFO`; `/GDRDMA` will be absent
  (normal here).

**Bare-engine multi-node sketch (xfuser, PipeFusion degree 2 across the two nodes).** *Untested on Spark
— a starting point to experiment, not a guaranteed recipe.* On each node, in the xfuser venv/container,
source the NCCL env then:
```bash
# clio (rank 0 / head), over the fabric:
source /opt/clio/nccl.env; export NCCL_NET_GDR_LEVEL=0
torchrun --nnodes=2 --node_rank=0 --nproc_per_node=1 \
  --master_addr=10.51.50.13 --master_port=29500 \
  examples/<model>_example.py \
  --pipefusion_parallel_degree 2 --height 2048 --width 2048 \
  --model <hf_repo> --prompt "..."

# urania (rank 1), same command with --node_rank=1
source /opt/clio/nccl.env; export NCCL_NET_GDR_LEVEL=0
torchrun --nnodes=2 --node_rank=1 --nproc_per_node=1 \
  --master_addr=10.51.50.13 --master_port=29500 examples/<model>_example.py ...
```
- `--pipefusion_parallel_degree 2` = **capacity** (shards weights ~½ per node). Use `--ulysses_degree 2`
  instead for **speed** (replicates weights — only if the model already fits one node).
- Video tolerates the link better than images (bigger tensors, higher compute-to-comms), but still
  sublinear.

**ComfyUI integration patterns (in order of effort):**
1. **raylight node** — closest to turnkey; drives xDiT inside ComfyUI. Start single-node to validate the
   model, then try its Ray multi-node mode (expect rough edges).
2. **xDiT-as-a-service (A1 pattern, most robust if you must span):** run the xfuser multi-node pipeline
   above as a small persistent HTTP service on clio (it owns both ranks via torchrun), and add a thin
   **ComfyUI custom node** that POSTs the prompt/params and returns the image/latent. This keeps
   ComfyUI's single-process graph intact and isolates the distributed mess in the service. This is the
   sane version of "add spanning to ComfyUI."
3. **Writing the parallelism yourself — don't.** You'd be re-implementing xDiT for one user.

**Honest expectation:** ~1.16× at 1024px, ~none at 2048px, ~1.9× at 4096px (xDiT Flux, 2-node Ethernet);
video maybe ~2× across *three* nodes (AMD/RoCE). Plus sm_121 build friction (§6). For capacity it's
unnecessary; for speed it underperforms a single quantized run for typical sizes.

---

## 4b. Multinode for *speed* (not size) — sequence / CFG parallel

Distinct from §4: here the model is **replicated on both nodes** (it already fits one box) and the
**work of one generation** is split → a faster single image/video. All of this lives in
**[xDiT/xfuser](https://github.com/xdit-project/xDiT)**.

| Method | What it splits | Comms pattern | Link-friendly on RoCE? | Best for |
|---|---|---|---|---|
| **CFG-parallel** (degree 2) | the cond/uncond halves of the batch | one tiny exchange/step | **best** | any model using CFG — the natural 2-Spark win |
| **Ring-Attention** (sequence) | the latent sequence, P2P ring | point-to-point | good | long video / high-res |
| **Ulysses** (sequence) | the latent sequence, all-to-all | all-to-all (bandwidth-heavy) | poor | only when tensors are huge (video) |
| **USP** (Ulysses×Ring) | both | mixed | medium | tuned video |

**The hard ceiling of a 2-node / 1-GPU-each setup:** `world_size = 2`, and all parallel degrees must
multiply to 2 — so you get **exactly one** degree-2 axis (CFG **or** ring **or** pipefusion), never two
stacked. That caps the theoretical speedup at 2× before comms losses.

**What to expect on *this* link** (no NVLink, ~23 GB/s, no GDR) — sublinear, and only when per-step
compute is big enough to hide comms:
- Low-res images (≤1024px): **not worth it** (~1.16× or none — communication-dominated).
- High-res images (~4096px): **~1.9×** on 2 nodes (xDiT Flux benchmark).
- **Video: the best case here** (large tensors, high compute-to-comms) — AMD measured ~2× going 1→3
  nodes for HunyuanVideo over RoCE; expect a meaningful-but-sublinear gain on two Sparks.

**Recommended 2-Spark speed recipe:** start with **CFG-parallel (degree 2)** — cheapest comms, nearly
free if the model uses classifier-free guidance; switch to **ring** sequence-parallel for long video.
Avoid pure **Ulysses** (all-to-all) on this link unless the tensors are very large.

Launch = the same multinode `torchrun` as §4, but swap the capacity flag for a speed axis:
```bash
# both nodes; source /opt/clio/nccl.env first (incl. NCCL_NET_GDR_LEVEL=0)
torchrun --nnodes=2 --node_rank={0|1} --nproc_per_node=1 \
  --master_addr=10.51.50.13 --master_port=29500 examples/<model>_example.py \
  --use_cfg_parallel                 # degree-2 CFG split (best 2-Spark default)
  # OR for long video, instead: --ring_degree 2
  # (NOT both — they'd multiply to 4 > world_size 2)
```
**ComfyUI:** **[raylight](https://github.com/komikndr/raylight)** exposes xDiT **USP** (speed) — validate
single-node first; its Ray multinode mode is the experimental frontier. Otherwise use the
xDiT-as-a-service pattern from §4.

## 5. What the second Spark is actually good for (the real win)

- **Parallel render farm (throughput):** `clusterctl comfyui up`, then add urania as a **Remote worker**
  in **[ComfyUI-Distributed](https://github.com/robertvoy/ComfyUI-Distributed)** (or **SwarmUI**). Each
  Spark renders different seeds/clips simultaneously — a genuine ~2× for "make many videos." Each box
  holds its own full copy of the model (no spanning).
- **Large-LLM inference:** `clusterctl shard` (vLLM PP=2) for 200B+ models — the link's intended use.

---

## 6. DGX Spark (sm_121) build/runtime caveats for any of this

- Install Torch from the **cu130** aarch64 index; CUDA-12 wheels fail (`libcudart.so.12`). ARM64-native
  containers only (x86 → `exec format error`).
- **flash-attention does not support sm_121** (build fails). Use PyTorch **SDPA** or FlashInfer; compile
  **SageAttention** on-device. **Triton lacks sm_121** → `torch.compile` gives ~no speedup (often disabled).
- NVFP4 is hardware-native (good). Spark-tuned ComfyUI builds exist (SparkyUI, comfyui-dgx-spark) to
  absorb the attention/build pain — see [comfyui.md](comfyui.md).

---

## Sources (fetched 2026-06-18)
- NVIDIA porting guide — GPUDirect RDMA unsupported on GB10: https://docs.nvidia.com/dgx/dgx-spark-porting-guide/porting/cuda.html (2026-05-24)
- NVIDIA forum — "scaling diffusion across multiple DGX Spark" (throughput, not faster single render): thread #356373 (2026-01-03)
- xDiT / xfuser + PipeFusion (shards weights; Ethernet-tolerant): https://github.com/xdit-project/xDiT ; paper https://arxiv.org/abs/2405.14430 (NeurIPS 2025); Flux scaling docs (1.16×/none/1.9×)
- raylight (ComfyUI xDiT USP+FSDP node): https://github.com/komikndr/raylight (v1.6.0, 2026-06-17)
- ComfyUI-MultiGPU/DisTorch2: https://github.com/pollockjj/ComfyUI-MultiGPU ; ComfyUI-Distributed: https://github.com/robertvoy/ComfyUI-Distributed (v1.4.0, 2026-02-28)
- ComfyUI-GGUF: https://github.com/city96/ComfyUI-GGUF ; kijai WanVideoWrapper (block-swap): https://github.com/kijai/ComfyUI-WanVideoWrapper
- AMD multinode HunyuanVideo over RoCE (~2× on 3 nodes): https://rocm.blogs.amd.com/software-tools-optimization/multinode-hunyuanvideo-xdit/README.html (2026-01-09)
- HunyuanImage-3.0 80B: https://github.com/Tencent-Hunyuan/HunyuanImage-3.0 ; FLUX.2: https://github.com/black-forest-labs/flux2 (2025-11-25)
- PyTorch Blackwell quant (NVFP4/MXFP8 diffusers): https://pytorch.org/blog/faster-diffusion-on-blackwell-mxfp8-and-nvfp4-with-diffusers-and-torchao/ (2026-04-08)
- ComfyUI dynamic VRAM (lowvram now ~no-op): https://blog.comfy.org/p/dynamic-vram-in-comfyui-saving-local (2026-03-25)

**Open evidence gap:** no public demonstration of one video diffusion job sharded across two DGX Sparks
as of 2026-06-18. Treat §4 as experimental.
