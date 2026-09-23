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
