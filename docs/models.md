# Model catalog — coding LLMs on the clio/urania cluster

> Synthesized from current (mid-2026) community + NVIDIA sources. Re-verify tok/s with
> your own `vllm bench serve` — this ecosystem moves weekly. Sources at the bottom.

## The one fact that drives every choice

GB10 decode speed is **memory-bandwidth-bound (~273 GB/s)**, not compute-bound. So:

- **Decode tok/s ≈ bandwidth ÷ bytes-read-per-token.** MoE models with few *active* params
  (GPT-OSS, Qwen3-A3B) are far faster than dense models of the same total size.
- Any "1,000+ tok/s" figure you read is almost always **prefill** (compute-bound). The number
  that governs interactive typing is **decode**, which lands ~20–60 tok/s for the good models.
- **Sharding across both Sparks does NOT speed up single-stream typing.** Each node still reads
  weights at ~273 GB/s, and there's no GPUDirect RDMA on GB10 (unsupported by design). Spanning
  two nodes raises the *model-size ceiling* and *concurrent throughput*, not one developer's
  latency. And when you do span two nodes, use **pipeline parallelism (PP=2), not tensor
  parallelism** — measured ~2.2× faster here because the 200G link can't sustain TP's per-token
  all-reduce (StorageReview, 2026-05-11). → see docs/networking.md and docs/architecture.md.

## Recommended models

| Role | Model | Quant | Decode tok/s (single) | Footprint | Why |
|---|---|---|---|---|---|
| **Daily driver** | **Qwen3-Coder-30B-A3B** | FP8 or AWQ-INT4 | ~30–48 | ~30–60 GB | Coding-tuned MoE (~3B active), prefix caching works, big headroom. Fits twice over → ideal for scale-out. |
| **Capability** | **GPT-OSS-120B** | MXFP4 (native) | ~38–56 | ~120 GB | Best reasoning that fits one box. 128 experts / 4 active. **MXFP4 caveat below.** |
| Large MoE (tuned) | Qwen3.5-122B-A10B | INT4+FP8 hybrid +MTP | ~51 (tuned) / 28 base | ~70 GB | Best-documented big-MoE tuning case; needs patched vLLM. |
| Lightweight/fast | Qwen3-Coder smaller / Llama 3.1 8B | FP8/FP4 | high | small | Autocomplete / cheap tasks; can co-reside. |

### Models to avoid or treat as untested on a single Spark
- **Qwen3-Coder-480B-A35B** — 35B active, **does not fit usefully** on one 119 GB box. Needs sharding (scale-up) and even then is heavy.
- **Qwen3-Coder-*-Next* hybrid** — vLLM prefix caching is broken (0% hit) on it. Avoid for agentic work.
- **DeepSeek-V3.2 / GLM-5.1 (sparse-MLA/DSA)** — no attention backend selectable on sm_121 (vLLM #45317, open 2026-06). Non-sparse models on the same box are fine.
- **GLM-4.5-Air, Codestral/Devstral, dense Qwen3-Coder-32B, DeepSeek-R1 distills** — community-liked but **no clean single-Spark decode benchmark exists**. Try, but measure.

## Quantization on sm_121 (GB10) — what's safe vs what needs patches

| Format | Status on GB10 | Notes |
|---|---|---|
| **AWQ (INT4)** | ✅ Safest on **stock** vLLM | NVIDIA Dec-2025 PSA: ~32% faster than (then-broken) NVFP4. Use this if unsure. |
| **FP8** | ✅ Works well | Used by the winning 122B hybrid. Good default for Qwen3-Coder-30B. |
| **MXFP4** | ⚠️ Native for GPT-OSS, but **wrong output on stock vLLM** | sm_121 has no native FP4 path in the selector → falls back to Marlin and emits broken logits (vLLM #37030, open). Use the **`vllm-node-mxfp4` image** (built with `--exp-mxfp4`) and serve with `--quantization mxfp4 --mxfp4-backend CUTLASS`, or run MXFP4 under **SGLang/llama.cpp**. |
| **NVFP4** | ⚠️ HW-supported, needs patched stack | Vanilla vLLM hit missing PTX (ran at 1.1 tok/s). Patched (Avarok) → ~42 tok/s. This is the *official* direction long-term. |
| GPTQ | ✅ works, rarely recommended | Little Spark data. |

**Bottom line:** on the stock engine, prefer **AWQ/FP8**. For **MXFP4 (GPT-OSS-120B)** rely on
the eugr image's MXFP4 path (that's what our `gpt-oss-120b` profile uses).

## Engine / image choice
- **Workhorse:** [`eugr/spark-vllm-docker`](https://github.com/eugr/spark-vllm-docker) — actively
  maintained (→ Jun 2026), built for sm_121. Ships `build-and-copy.sh` (builds the image and copies
  it to the other Spark) and `launch-cluster.sh` (auto-detects fabric topology; `--solo` for one
  node, cluster mode for Ray multi-node). Has the working MXFP4 path. The profiles + systemd units
  drive these scripts. Image tags: `vllm-node` (default), `vllm-node-mxfp4` (built with `--exp-mxfp4`).
- **Stable fallback:** official `nvcr.io/nvidia/vllm` **arm64** image (tag ~`26.05.post1-py3`).
  (The generic Docker Hub `vllm/vllm-openai:latest` is amd64 → "exec format error". Use the
  `cu130-nightly` arm64 track if you go upstream.)

## Standard serving flags (baked into profiles)
```
--max-model-len 131072 --gpu-memory-utilization 0.85 --max-num-seqs 4
--enable-prefix-caching --load-format fastsafetensors      # fastsafetensors = eugr fast loader
# CUDA graphs on by default.
# Do NOT use --kv-cache-dtype fp8 (hurts perf on Spark).
# Fallbacks when kernels misbehave: --enforce-eager, TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
# Sharded (PP=2) mode adds: --pipeline-parallel-size 2 --tensor-parallel-size 1 --distributed-executor-backend ray
```

## Unified-memory safety (important)
128 GB is one shared CPU+GPU pool. **OOM freezes the whole machine** (not a clean CUDA OOM).
Keep `--gpu-memory-utilization ≤ 0.85`, conservative `--max-num-seqs`, swap disabled, and note
memory scales **super-linearly** with batch size (8→16 ≈ +34 GB). First request JIT cold-start
is ~25 s — profiles pre-warm.

## Sources (fetched 2026-06-17)
- vLLM DGX Spark blog (2026-06-01) https://vllm-project.github.io/2026/06/01/vllm-dgx-spark.html
- eugr/spark-vllm-docker (active →2026-06-10) https://github.com/eugr/spark-vllm-docker
- NVIDIA dgx-spark-playbooks /nvidia/vllm (2026-06-12)
- GPT-OSS-120B practical guide (2026-05-31) https://corti.com/running-gpt-oss-120b-on-a-single-nvidia-dgx-spark-a-practical-guide/
- Qwen3.5-122B 51 tok/s tuning (forum 2026-04-05); concurrency benchmark (2026-04-22) https://dendro-logic.com/engineering/nvidia-dgx-spark-concurrency-benchmark/
- Open bugs to recheck: vLLM #37030 (MXFP4 wrong output), #45317 (sparse-MLA), #43906 (MXFP8), #37431 (Mamba-2)
