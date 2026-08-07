# ComfyUI — as deployed (urania + clio)

The concrete setup running on **urania** (`http://urania:8188`) and, since 2026-08-07, **clio**
(`http://clio:8188`). Both nodes are configured identically; the paths below are the same on each.
Concepts + model fit are in [comfyui.md](comfyui.md); large-model strategy in
[generative-sharding.md](generative-sharding.md). This is the operational reality. Last updated 2026-08-07.

> **Access is now via HTTPS + a client certificate** — `https://clio.cgk.sh/` and
> `https://urania.cgk.sh/`. Port 8188 is loopback-only on both nodes, so the URLs below are
> historical. See [security.md](security.md). Never port-forward any of this: ComfyUI custom nodes
> run arbitrary code, and an Apr-2026 botnet hit exposed instances.

> **After the switch to HTTPS, your workflow tabs will look empty.** Nothing is lost. Saved
> workflows live on the server (`basedir/user/default/workflows/`, `"storage": "server"`), but the
> *open tab* state is browser `localStorage`, which is **per-origin** — and
> `https://urania.cgk.sh` is a different origin from the old `http://10.10.10.155:8188`. Open the
> Workflows sidebar and they are all there. Confirm from outside the browser with:
> `curl --cacert ~/.clio/ca/ca.crt --cert ~/.clio/ca/certs/<name>.crt --key ~/.clio/ca/private/<name>.key`
> `  'https://urania.cgk.sh/api/userdata?dir=workflows&recurse=true&split=false&full_info=true'`

## Which node runs it
- **urania** is ComfyUI-only — nothing competes for its memory. It also **owns the model store**,
  which clio mounts ([comfyui-shared-store.md](comfyui-shared-store.md)); if urania is down,
  clio's ComfyUI will not start.
- **clio** is dual-role: ComfyUI **or** GPT-OSS-120B, never both (one 119 GiB unified pool; an OOM
  freezes the box). The units declare `Conflicts=` on each other, so the swap is atomic:
  `clusterctl mode clio comfy` / `clusterctl mode clio llm`. See [state.md](state.md).
- Running both nodes at once is what makes ComfyUI-Distributed worthwhile — but it costs you
  `local-big` for the duration.

## Container & paths
- Image: `mmartial/comfyui-nvidia-docker:ubuntu24_cuda13.1-dgx-latest` (systemd `clio-comfyui.service`, on demand: `clusterctl comfyui up|down [clio|urania]`).
- Launched with **`--base-directory /basedir`**, so all data lives under the host path
  **`/opt/clio/comfyui-docker/basedir/`** → `models/<type>/`, `custom_nodes/`, `user/`, `input/`, `output/`.
  (The ComfyUI *code* is at `/comfy/mnt/ComfyUI`; the **venv is `/comfy/mnt/venv`**, not under ComfyUI.)
- ComfyUI-Manager is **built in** (`--enable-manager`); use it to install workflow-required nodes safely.
- The image's `--use-sage-attention` flag is **stripped** in bootstrap (SageAttention isn't built for
  sm_121) → it uses **SDPA**, which is fine/faster on Blackwell.

## Where to put files (host paths — same on both nodes, and the same *bytes*: `models/` is a shared NFS mount)
| Type | Folder |
|---|---|
| Checkpoints | `/opt/clio/comfyui-docker/basedir/models/checkpoints/` |
| LoRAs | `…/basedir/models/loras/` |
| VAE / text encoders / diffusion (Wan) | `…/basedir/models/{vae,text_encoders,diffusion_models}/` |
| Upscalers | `…/basedir/models/upscale_models/` |
| YOLO detectors (FaceDetailer) | `…/basedir/models/ultralytics/bbox/` |
| Custom nodes | `…/basedir/custom_nodes/` |

## Installed models
- **Image checkpoints:** `lustifySDXLNSFW_apexV8` (SDXL, realistic adult), `RealVisXL_V5.0_fp16` (SDXL, general realism), `cyberrealisticPony_v180Coreshift` (**Pony** base, for the Pony LoRA ecosystem).
- **Pony LoRAs:** `top-down_doggystyle_v0.2-pony` (pose), `cinematic photography detailed pony xl v5.1` (skin/detail), `volumetric lighting style pony v1` (lighting).
- **Video (Wan 2.2 TI2V-5B i2v, fast/draft):** `wan2.2_ti2v_5B_fp16` (diffusion_models), `umt5_xxl_fp8_e4m3fn_scaled` (text_encoders), `wan2.2_vae` (vae). 24 fps.
- **Video (Wan 2.2 I2V-A14B, strong motion):** `Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf` + `…-LowNoise-…gguf` (diffusion_models, GGUF), shares the umt5 encoder, uses **`wan_2.1_vae`** (NOT wan2.2_vae). 16 fps. Two-expert MoE.
- **Support:** `face_yolov8m.pt` + `hand_yolov8n.pt` (detectors), `4x-UltraSharp.pth` (upscaler).

> **The model list above is stale.** It describes the 2026-06-19 build. urania has since accumulated
> a much larger set (as of 2026-08-07: 3 checkpoints, ~87 LoRAs, 9 GGUF unets, 5 upscalers — mostly
> Wan 2.2 I2V high/low-noise LoRA pairs). Treat the loader dropdowns in the UI as the real inventory;
> the entries below are just the ones the six documented workflows reference.

### Node parity (2026-08-07)
There is **one copy of the models**, on urania, NFS-mounted onto clio over the 200G fabric — so both
nodes see the identical set and nothing needs copying. See
[comfyui-shared-store.md](comfyui-shared-store.md). Node-local pieces that still need syncing:

```bash
ssh clio.cgk.sh 'sudo bash /opt/clio/scripts/sync-comfyui-from-urania.sh'   # workflows + node drift report
```

## Custom nodes installed
ComfyUI-Impact-Pack **+ Impact-Subpack** (FaceDetailer / UltralyticsDetectorProvider), ComfyUI_UltimateSDUpscale, rgthree-comfy, ComfyUI-GGUF. (Plus built-in Manager.)

## The 6 workflows (Workflow → Open)
| Workflow | Does | Notes |
|---|---|---|
| `clio_lustify_portrait` | SDXL txt2img → FaceDetailer → 2× UltimateSDUpscale | best-quality stills; swap checkpoint to RealVis if wanted |
| `clio_pony_lora_doggystyle` | CyberRealistic Pony **+ doggystyle LoRA** → image | LoRA example; prompt starts with `score_9, score_8_up, score_7_up,` + the LoRA trigger |
| `clio_wan_image_to_video` | Load Image (your still) → Wan 2.2 → MP4 | upload a still into Load Image; 832×480, 49 frames (~2 s) |
| `clio_text_to_image_to_video` | one-click: LUSTIFY still → straight into Wan → MP4 | two prompt areas: **image prompt** (off checkpoint) + **motion prompt** (off Wan CLIPLoader) |
| `clio_t2v_lora_stack` | Pony + **3 stacked LoRAs** (pose+skin+lighting) → Wan video | shows LoRA stacking via chained LoraLoaders |
| `clio_wan_a14b_image_to_video` | Load Image → Wan 2.2 **I2V-A14B** (two-expert) → MP4 | **strong-motion** tier; 16 fps, 81 frames (~5 s); many knobs |

Real runs: SDXL image ~15 s; video several minutes (the 15–24 s seen during build was low-step validation).

### Tuning motion (Wan, esp. A14B)
Motion is driven by **prompt + CFG + (A14B) the expert split** — there is no motion-strength slider.
- **Prompt** with explicit action verbs (what the subject *does*); put `static, still, motionless, slow motion` in the negative.
- **CFG** 6→7 = stronger adherence to the motion text.
- **A14B high/low-noise split** is the #1 lever: the *high-noise* expert creates motion/structure, the *low-noise* refines. Default split is 10 of 20 steps (the high `KSamplerAdvanced` `end_at_step` and the low one's `start_at_step`). Push both to ~13 for **more** motion; pull to ~8 for more refinement.
- **Shorter clip** reads punchier — 5 s dilutes motion across frames. (length must be 4n+1.)
- For more still: add a **Wan-base motion LoRA** via `LoraLoaderModelOnly` on each expert.

## LoRAs — usage & the one rule
- **Base must match the checkpoint:** Pony LoRA → Pony checkpoint; SDXL LoRA → LUSTIFY/RealVis. A mismatch produces garbage. (Most Civitai pose LoRAs are SD1.5/Pony, *not* SDXL — that's why the LoRA workflows use the Pony checkpoint.)
- **Wiring:** insert a `LoraLoader` between Load Checkpoint and the sampler/encoders (MODEL+CLIP in/out); chain more for a stack. Put each LoRA's **trigger word** (on its Civitai page) in the positive prompt; strength ~0.6–0.9.
- **Pony quirk:** Pony checkpoints need `score_9, score_8_up, score_7_up,` at the front of the positive prompt.

## Adding a model or LoRA from Civitai (download-only API token in `cgk_dns_sync.env`-style header)
```bash
# get the filename, then download (curl handles the CDN redirect; needs a browser User-Agent)
UA='Mozilla/5.0'; TOK=<civitai token>
curl -fsSL -A "$UA" -H "Authorization: Bearer $TOK" -o /opt/clio/comfyui-docker/basedir/models/<type>/<name> \
  "https://civitai.com/api/download/models/<versionId>"
# then in ComfyUI: refresh (or restart clio-comfyui) and pick it in the loader.
```
HuggingFace files: plain `curl -fsSL -o <dest> <resolve-url>` (no token for open repos). Prefer `.safetensors`.

## Generating workflows programmatically
The 5 workflows were built with an **object_info-driven builder** (fetch `/object_info`, classify each
input as a connection vs a widget — note base-node combos are typed `"COMBO"`, custom-node combos are a
list; insert `control_after_generate` after any `seed`/`noise_seed`), **validate by POSTing to `/prompt`
and running it**, then emit the litegraph JSON into `…/basedir/user/default/workflows/`. That pattern is
the reliable way to add more (ask and I can build one).

## sm_121 / GB10 gotchas (operational)
- Attention: flash-attn/SageAttention2/xformers unsupported → **SDPA**. `torch.compile`/Triton weak on sm_121.
- Quantization for big models: **GGUF** (ComfyUI-GGUF) is the reliable route; NVFP4/FP8-fast are dicey.
- Unified memory: an OOM can freeze the box — cap utilization; use **tiled VAE** for video/large images.
- Speed: capacity-rich, bandwidth-poor (~273 GB/s). SDXL snappy, Flux workable, **video = minutes/clip**.
- Memory coexistence: ComfyUI + a vLLM model on the same box compete for the one pool — for heavy gen, keep the box dedicated (currently urania is ComfyUI-only; the coder is parked).
