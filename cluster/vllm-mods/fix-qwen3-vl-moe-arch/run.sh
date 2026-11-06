#!/bin/bash
# Qwen3-VL-MoE fails to load on this vLLM build (0.23.1rc1.dev129) with transformers 5.x:
#   ValidationError ... No model architectures are specified
# qwen3_vl_moe.py builds its language model from config.text_config, whose model_type
# (qwen3_vl_moe_text) is not in transformers' causal-LM mapping, so with_hf_config() leaves
# architectures empty. Name the text architecture explicitly. This is upstream's own fix, vLLM PR
# #43272 (merged 2026-09-10); our vllm-node image (built 2026-06-17) predates it. Delete this mod
# once the image is rebuilt from a newer vLLM.
# Applied in every container (head and worker) by launch-cluster.sh --apply-mod. Idempotent.
set -e
f=/usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/qwen3_vl_moe.py
if grep -q 'with_hf_config(config.text_config)' "$f"; then
  sed -i 's/with_hf_config(config.text_config)/with_hf_config(config.text_config, architectures=["Qwen3MoeForCausalLM"])/' "$f"
  echo "fix-qwen3-vl-moe-arch: patched $f"
else
  echo "fix-qwen3-vl-moe-arch: already patched or upstream changed; skipping"
fi
