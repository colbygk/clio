# ComfyUI on the clio/urania cluster — image & video generation

> Synthesized from current (mid-2026) sources; see bottom. Re-verify nightly wheel suffixes
> and per-node custom-node builds at install time.

## The honest framing

A DGX Spark is a **capacity** machine, not a **speed** machine for diffusion. ~273 GB/s
bandwidth is ~6.6× below an RTX 5090, and diffusion re-reads all weights every denoise step.

- **Image gen:** ~2–2.5× slower than a 5090, but it *runs models that OOM any consumer GPU*
  (e.g. Qwen-Image 20B). FLUX.1-schnell (FP4/TensorRT) ≈ 23 img/min; SD3.5-Large 50-step ≈ 82 s; FLUX.1-dev 50-step ≈ ~100 s.
- **Video gen:** the real reason to care about 128 GB. Wan 2.2 14B FP16 (81 frames @640²) ≈ 6 min warm;
  LTX-2 720p 10s clip ≈ ~4.3 min; Cosmos Predict2 14B ≈ 32 min. Minutes-per-clip is normal.

## Parallelism: data-parallel only (matches your goal)

Core ComfyUI **cannot shard one generation across machines, and cannot even run parallel jobs
in one instance**. So "use both Sparks" = run more jobs at once, not faster single jobs. None of
these pool VRAM — each box must independently fit the model (fine for independent jobs).

**Chosen approach: [ComfyUI-Distributed](https://github.com/robertvoy/ComfyUI-Distributed)**
(robertvoy, v1.4.0 2026-02-28, active). Host on one Spark, add the other as a **Remote worker**;
it auto-load-balances jobs across both. Turnkey, lives inside ComfyUI.

Alternatives if you outgrow it:
- **SwarmUI** — polished front-end that manages multiple ComfyUI backends (built at Stability for clusters).
- **ComfyPool** (TS SDK) — programmatic round-robin / least-loaded across backend URLs if you drive via API.
- Avoid `ComfyUI_NetDist` (city96, unmaintained since 2024) and `ComfyUI_Cluster` (experimental).

Your 200 GbE fabric is far more than enough to shuttle job payloads/results between the boxes.

## Install: Docker (recommended) over bare-metal

The official NVIDIA playbook (`NVIDIA/dgx-spark-playbooks/nvidia/comfy-ui`, 2025-11-10) is a
**bare-metal venv** and works (PyTorch ships `cu130` aarch64 wheels). But for two reproducible
machines, **Docker wins** because of the sm_121 native-build pain. Base on NGC
`nvcr.io/nvidia/pytorch:25.10-py3`+ (torch 2.9 / CUDA 13 / native sm_121); 24.10 and earlier do
NOT support GB10. Maintained community image with a ready compose:
[ComfyUI-Nvidia-Docker](https://github.com/mmartial/ComfyUI-Nvidia-Docker) has a
`compose-dgx_spark.yaml` (CUDA 13.1, SageAttention sm_121 auto-select).

### sm_121 / arm64 gotchas (the actual work)
- **PyTorch:** use the `cu130` aarch64 index. A "compute capability outside supported range"
  warning is harmless (sm_120/sm_121 are binary-compatible). Nightly suffix drifts — verify.
- **SageAttention:** must be **built from source** for sm_121 (`TORCH_CUDA_ARCH_LIST="12.1"`,
  use the `2-git` version). This is the attention backend that works.
- **Flash-Attention:** **omit it** — pulls CUDA-12 libs (`libcudart.so.12` errors) and is slower
  than SDPA on Blackwell anyway. Use SDPA (cuDNN).
- **xformers / Triton:** weak on arm64; you may need `TORCHDYNAMO_DISABLE=1`.
- **Custom nodes:** strip `torch*` from each node's `requirements.txt` before install, or they
  clobber the GB10-capable PyTorch (`sed -i '/^torch/d;/^torchvision/d;/^torchaudio/d'`).
- **Unified memory:** cap GPU mem utilization (~0.7–0.85); OOM can **freeze the box**. Some setups
  add `--disable-pinned-memory`. (LTX early runs even crash-rebooted from power spikes → cap clocks.)

## Models worth installing
- **Image:** FLUX.1 (use the **FP4** path for speed), **Qwen-Image 20B** (the Spark standout —
  ~63 GB full stack, OOMs consumer cards), SD3.5-Large for lighter jobs.
- **Video:** **LTX-2** as primary (best practical fit — VRAM-light, RAM-hungry), **Wan 2.2 14B**
  and **Hunyuan Video 13B** for capacity jobs you can't run elsewhere. Prefer NVFP4/NVFP8/distilled
  variants over FP16 for usable speed.

## Very large models on one Spark

**The hard rule: a single ComfyUI generation cannot span both boxes.** Core ComfyUI doesn't shard a
workflow across machines and doesn't pool VRAM, so **a single image/video model is bounded by one
Spark's ~119 GB unified memory** — you cannot add clio's + urania's memory together for one model the
way vLLM's PP=2 does for LLMs. The second Spark buys **throughput** (a second concurrent job via
ComfyUI-Distributed), never a bigger single model.

The good news: **119 GB unified is already enormous for diffusion** — it runs models that OOM every
consumer GPU. Most "very large" media models fit on one Spark as-is:

| Model | Approx footprint | Fits one Spark? |
|---|---|---|
| FLUX.1-dev / SD3.5-Large | ~17–33 GB | easily |
| Qwen-Image 20B | ~63 GB full stack | yes (the Spark standout) |
| Wan 2.2 14B (video) | ~61 GB @ 81 frames | yes |
| Hunyuan Video 13B | ~47–58 GB FP16 | yes |
| LTX-2 (video) | VRAM-light, RAM-hungry | yes (best practical fit) |

If a model genuinely exceeds the ~110 GB usable ceiling, **reduce the footprint — don't try to span**:
- **Quantize the weights:** FP8 / NVFP4 / **GGUF** quantized checkpoints (e.g. via ComfyUI-GGUF) cut
  the diffusion model 2–4×. This is the single biggest lever.
- **Offload / block-swap:** ComfyUI's `--lowvram`/weight-streaming and block-swap nodes stream layers
  between the (same unified) RAM and the GPU compute path; sequential/tiled **VAE** cuts the decode peak.
- **Shrink the job:** lower resolution, fewer video frames, smaller batch — diffusion memory scales with
  all three.

Use the **second Spark for parallelism, not size**: load a different model on it, or run a second job,
and let **ComfyUI-Distributed** balance jobs across both. (Niche exceptions only split *tiles* of one
image — e.g. Ultimate SD Upscale Distributed — they still don't pool VRAM for the base model.)

> If you have a specific model in mind that won't fit even quantized on 119 GB, the realistic options
> are a smaller/distilled variant or a different model — not the cluster. Tell me the model and I'll
> work out whether it fits and the exact flags.

## Reproducibility across both machines
- **Custom nodes:** `comfy node save-snapshot` → commit the JSON → `restore-snapshot` on node 2.
  (Snapshot support is incomplete for non-git nodes — note any manual ones.)
- **Models:** a `models.yaml` manifest (`repo_id → dir`) with **pinned HF revisions**; provision
  via `snapshot_download(revision=<SHA>)` to prevent drift.
- **Storage:** keep the library as a single source of truth, but **local-SSD cache hot/active
  models** (esp. video weights). Network-mounted weights are throttled by small-block I/O, not link
  bandwidth — benchmark your mount with large `rsize`/readahead before relying on it. Share the HF
  cache via `HF_HOME` on the mount; copy caches between hosts with `rsync -aH` (preserves blob symlinks).

## Sources (fetched 2026-06-17)
- NVIDIA ComfyUI playbook (2025-11-10) https://github.com/NVIDIA/dgx-spark-playbooks/tree/main/nvidia/comfy-ui
- ComfyUI blog: ComfyUI on DGX Spark (2025-10-13) https://blog.comfy.org/p/comfyui-on-nvidia-dgx-spark
- NVIDIA image benchmarks (2025-10-24); Cosmos Predict2 matrix (2026-05-01)
- ComfyUI-Distributed v1.4.0 (2026-02-28) https://github.com/robertvoy/ComfyUI-Distributed
- ComfyUI-Nvidia-Docker (forum 2026-03-13) https://github.com/mmartial/ComfyUI-Nvidia-Docker
- comfy-cli v1.10.5 (2026-06-16); ComfyUI multi-GPU discussion #4139 (still unresolved)
