"""graphs.py — the ComfyUI graphs the pipeline runs, as API-format dicts.

Each builder returns (graph, titles). The same graph is submitted by scripts and exported to the
Workflows menu (comfy.to_ui), so a hand-tweaked run and a scripted one never diverge.
"""

QWEN_EDIT = {
    "unet": "qwen_image_edit_2511_fp8mixed.safetensors",
    "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
    "lightning": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
}

# draft = Lightning LoRA, 4 steps, cfg 1 (fast exploration); final = the model's own 40 / cfg 4
QUALITY = {"draft": dict(steps=4, cfg=1.0, lightning=True),
           "final": dict(steps=40, cfg=4.0, lightning=False)}


def qwen_edit(prompt, refs, seed, width, height, prefix, quality="draft", loras=(), negative=""):
    """Instruction edit with up to three reference images (image 1 = refs[0] ...).

    The latent is empty at width x height rather than an encoding of image 1, so a new view can
    change framing (a head reference -> a full-body portrait) instead of inheriting its canvas.
    """
    if not 1 <= len(refs) <= 3:
        raise ValueError("qwen_edit takes 1-3 reference images")
    q = QUALITY[quality]
    g, t = {}, {}

    def add(nid, cls, title=None, **inputs):
        g[nid] = {"class_type": cls, "inputs": inputs}
        if title:
            t[nid] = title
        return [nid, 0]

    model = add("unet", "UNETLoader", unet_name=QWEN_EDIT["unet"], weight_dtype="default")
    for i, (name, strength) in enumerate(loras):
        model = add(f"lora{i}", "LoraLoaderModelOnly", f"LoRA: {name}", model=model, lora_name=name,
                    strength_model=strength)
    if q["lightning"]:
        model = add("lightning", "LoraLoaderModelOnly", "Lightning 4-step (draft only)", model=model,
                    lora_name=QWEN_EDIT["lightning"], strength_model=1.0)
    model = add("shift", "ModelSamplingAuraFlow", model=model, shift=3.1)
    model = add("cfgnorm", "CFGNorm", model=model, strength=1.0)
    clip = add("clip", "CLIPLoader", clip_name=QWEN_EDIT["clip"], type="qwen_image", device="default")
    vae = add("vae", "VAELoader", vae_name=QWEN_EDIT["vae"])

    imgs = {}
    for i, ref in enumerate(refs, 1):
        raw = add(f"ref{i}", "LoadImage", f"image {i}", image=ref)
        imgs[f"image{i}"] = add(f"scale{i}", "FluxKontextImageScale", image=raw)

    def encode(nid, text, title):
        c = add(nid, "TextEncodeQwenImageEditPlus", title, clip=clip, prompt=text, vae=vae, **imgs)
        return add(nid + "_ref", "FluxKontextMultiReferenceLatentMethod", conditioning=c,
                   reference_latents_method="index_timestep_zero")

    pos = encode("pos", prompt, "Direction (positive)")
    neg = encode("neg", negative, "Negative")
    latent = add("latent", "EmptySD3LatentImage", width=width, height=height, batch_size=1)
    samples = add("sampler", "KSampler", model=model, positive=pos, negative=neg, latent_image=latent,
                  seed=seed, steps=q["steps"], cfg=q["cfg"], sampler_name="euler", scheduler="simple",
                  denoise=1.0)
    image = add("decode", "VAEDecode", samples=samples, vae=vae)
    add("save", "SaveImage", filename_prefix=prefix, images=image)
    return g, t


HUNYUAN3D_MV = "hunyuan3d-dit-v2-mv_fp16.safetensors"


def hunyuan3d_mv(views, seed, prefix, steps=30, cfg=7.5, octree=384):
    """Mesh from up to four views, e.g. {"front": name, "left": name}. Shape only, no texture:
    the look is added downstream (Blender), where the eyes also become rig-able objects."""
    g, t = {}, {}

    def add(nid, cls, title=None, **inputs):
        g[nid] = {"class_type": cls, "inputs": inputs}
        if title:
            t[nid] = title
        return [nid, 0]

    add("ckpt", "ImageOnlyCheckpointLoader", ckpt_name=HUNYUAN3D_MV)
    model, clipv, vae = ["ckpt", 0], ["ckpt", 1], ["ckpt", 2]
    cond = {}
    for side, name in views.items():
        img = add(f"img_{side}", "LoadImage", f"{side} view", image=name)
        cond[side] = add(f"enc_{side}", "CLIPVisionEncode", clip_vision=clipv, image=img, crop="none")
    add("cond", "Hunyuan3Dv2ConditioningMultiView", **cond)
    model = add("shift", "ModelSamplingAuraFlow", model=model, shift=1.0)
    latent = add("latent", "EmptyLatentHunyuan3Dv2", resolution=3072, batch_size=1)
    samples = add("sampler", "KSampler", model=model, positive=["cond", 0], negative=["cond", 1],
                  latent_image=latent, seed=seed, steps=steps, cfg=cfg, sampler_name="euler",
                  scheduler="normal", denoise=1.0)
    voxel = add("decode", "VAEDecodeHunyuan3D", samples=samples, vae=vae, num_chunks=8000,
                octree_resolution=octree)
    mesh = add("mesh", "VoxelToMesh", voxel=voxel, algorithm="surface net", threshold=0.6)
    add("save", "SaveGLB", mesh=mesh, filename_prefix=prefix)
    return g, t


# ---- video: motion between two key frames (bin/tween) ----
# Wan 2.2 I2V-A14B is two experts: the high-noise one lays down motion, the low-noise one refines.
# The lightx2v 4-step LoRAs cut 20+ steps at cfg ~3.5 to 4 steps at cfg 1 (2 per expert).
WAN = {
    "high": "Wan2.2-I2V-A14B-HighNoise-Q8_0.gguf",
    "low": "Wan2.2-I2V-A14B-LowNoise-Q8_0.gguf",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
    "lightning_high": "Wan_2_2_I2V_A14B_HIGH_lightx2v_4step_lora_260412_rank_64_fp16.safetensors",
    "lightning_low": "Wan_2_2_I2V_A14B_LOW_lightx2v_4step_lora_260412_rank_64_fp16.safetensors",
}
WAN_FPS = 16


def wan_frames(seconds):
    """Wan wants 4k+1 frames; the smallest such count that covers `seconds` at 16 fps."""
    n = max(1, round(seconds * WAN_FPS))
    return ((n + 3) // 4) * 4 + 1


def wan_tween(prompt, first, last, seed, width, height, length, prefix, negative="", steps=4, split=2):
    """first/last are LoadImage names. last=None animates forward from `first` alone (one-key shots,
    or a tail after the last key)."""
    g, t = {}, {}

    def add(nid, cls, title=None, **inputs):
        g[nid] = {"class_type": cls, "inputs": inputs}
        if title:
            t[nid] = title
        return [nid, 0]

    models = {}
    for k in ("high", "low"):
        m = add(f"unet_{k}", "UnetLoaderGGUF", f"Wan A14B {k}-noise expert", unet_name=WAN[k])
        m = add(f"light_{k}", "LoraLoaderModelOnly", f"Lightning 4-step ({k})", model=m,
                lora_name=WAN[f"lightning_{k}"], strength_model=1.0)
        models[k] = add(f"shift_{k}", "ModelSamplingSD3", model=m, shift=5.0)
    clip = add("clip", "CLIPLoader", clip_name=WAN["clip"], type="wan", device="default")
    vae = add("vae", "VAELoader", vae_name=WAN["vae"])
    pos = add("pos", "CLIPTextEncode", "Direction (positive)", clip=clip, text=prompt)
    neg = add("neg", "CLIPTextEncode", "Negative", clip=clip, text=negative)
    start = add("first", "LoadImage", "first key", image=first)
    if last:
        end = add("last", "LoadImage", "last key", image=last)
        add("cond", "WanFirstLastFrameToVideo", positive=pos, negative=neg, vae=vae, width=width,
            height=height, length=length, batch_size=1, start_image=start, end_image=end)
    else:
        add("cond", "WanImageToVideo", positive=pos, negative=neg, vae=vae, width=width, height=height,
            length=length, batch_size=1, start_image=start)
    cpos, cneg, latent = ["cond", 0], ["cond", 1], ["cond", 2]
    lat = add("k_high", "KSamplerAdvanced", "high-noise steps", model=models["high"], add_noise="enable",
              noise_seed=seed, steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple",
              positive=cpos, negative=cneg, latent_image=latent, start_at_step=0, end_at_step=split,
              return_with_leftover_noise="enable")
    lat = add("k_low", "KSamplerAdvanced", "low-noise steps", model=models["low"], add_noise="disable",
              noise_seed=seed, steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple",
              positive=cpos, negative=cneg, latent_image=lat, start_at_step=split, end_at_step=10000,
              return_with_leftover_noise="disable")
    frames = add("decode", "VAEDecode", samples=lat, vae=vae)
    video = add("video", "CreateVideo", images=frames, fps=float(WAN_FPS))
    add("save", "SaveVideo", video=video, filename_prefix=prefix, format="mp4", codec="h264")
    return g, t


# ---- music (bin/sound) ----
ACE = "ace_step_1.5_turbo_aio.safetensors"


def ace_music(tags, seconds, seed, prefix, bpm=90, keyscale="A minor", timesignature="4", steps=8):
    """Instrumental cue from a text description (ACE-Step 1.5 turbo: few steps, cfg 1)."""
    g, t = {}, {}

    def add(nid, cls, title=None, **inputs):
        g[nid] = {"class_type": cls, "inputs": inputs}
        if title:
            t[nid] = title
        return [nid, 0]

    add("ckpt", "CheckpointLoaderSimple", ckpt_name=ACE)
    model, clip, vae = ["ckpt", 0], ["ckpt", 1], ["ckpt", 2]
    pos = add("pos", "TextEncodeAceStepAudio1.5", "Music direction", clip=clip, tags=tags,
              lyrics="[instrumental]", seed=seed, bpm=bpm, duration=float(seconds),
              timesignature=timesignature, language="en", keyscale=keyscale, generate_audio_codes=True,
              cfg_scale=2.0, temperature=0.85, top_p=0.9, top_k=0, min_p=0.0)
    neg = add("neg", "ConditioningZeroOut", conditioning=pos)
    latent = add("latent", "EmptyAceStep1.5LatentAudio", seconds=float(seconds), batch_size=1)
    model = add("shift", "ModelSamplingAuraFlow", model=model, shift=3.0)
    samples = add("sampler", "KSampler", model=model, positive=pos, negative=neg, latent_image=latent,
                  seed=seed, steps=steps, cfg=1.0, sampler_name="euler", scheduler="simple", denoise=1.0)
    audio = add("decode", "VAEDecodeAudio", samples=samples, vae=vae)
    add("save", "SaveAudio", audio=audio, filename_prefix=prefix)
    return g, t


# ---- character LoRA training (bin/lora), ComfyUI's own trainer ----
def qwen_train_lora(folder, prefix, steps=1500, rank=16, learning_rate=1e-4, size=768, seed=0, offloading=True):
    """Train a LoRA for Qwen-Image-Edit on input/<folder>/ (image + same-named .txt caption pairs).
    Trains on top of the fp8 weights the key frames use (quantized_backward), without the
    Lightning LoRA, at the same ModelSamplingAuraFlow shift. Saves output/<prefix>_<steps>_steps
    .safetensors and a loss graph. 1024 px without offloading took all of clio's unified memory and
    hung the box (2026-10-22), hence 768 px and offloading by default."""
    g, t = {}, {}

    def add(nid, cls, title=None, **inputs):
        g[nid] = {"class_type": cls, "inputs": inputs}
        if title:
            t[nid] = title
        return [nid, 0]

    model = add("unet", "UNETLoader", unet_name=QWEN_EDIT["unet"], weight_dtype="default")
    model = add("shift", "ModelSamplingAuraFlow", model=model, shift=3.1)
    clip = add("clip", "CLIPLoader", clip_name=QWEN_EDIT["clip"], type="qwen_image", device="default")
    vae = add("vae", "VAELoader", vae_name=QWEN_EDIT["vae"])
    add("data", "LoadImageTextDataSetFromFolder", "Dataset (input/" + folder + ")", folder=folder)
    imgs = add("resize", "ResizeImagesByLongerEdge", images=["data", 0], longer_edge=size)
    add("encode", "MakeTrainingDataset", images=imgs, vae=vae, clip=clip, texts=["data", 1])
    add("train", "TrainLoraNode", "Train LoRA", model=model, latents=["encode", 0], positive=["encode", 1],
        batch_size=1, grad_accumulation_steps=1, steps=steps, learning_rate=learning_rate, rank=rank,
        optimizer="AdamW", loss_function="MSE", seed=seed, training_dtype="bf16", lora_dtype="bf16",
        quantized_backward=True, algorithm="LoRA", gradient_checkpointing=True, checkpoint_depth=1,
        offloading=offloading, existing_lora="[None]", bucket_mode=False, bypass_mode=False)
    add("save", "SaveLoRA", lora=["train", 0], prefix=prefix, steps=["train", 2])
    add("loss", "LossGraphNode", loss=["train", 1], filename_prefix=prefix + "_loss")
    return g, t
