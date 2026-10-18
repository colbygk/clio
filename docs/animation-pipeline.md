# Animation pipeline — the three-eyed cat

> Started 2026-09-23. Status: **planning / experiments not yet run.** Only source asset so far is
> one head drawing (`Dropbox/Photos/avataria/colbygk.png`, 500², black ink on white).

## Decisions so far

| Question | Answer |
|---|---|
| Body plan | **Switches** — walks on four legs, stands up to act (hardest rig; mocap drives the biped mode) |
| Final look | **Undecided** — test flat 2D, 3D + ink shader, and AI-stylized, then choose |
| Hands-on effort | **Some** — Colby approves designs and cleans up keys; pipeline does the heavy lifting |
| Dialogue | **Yes, Colby's own voice** — mouth shapes (visemes) + audio-driven lip-sync from day one |
| Variants | Intro story starts with a **two-eyed** version of the same cat → the 2-eye / 3-eye split is a *variant* of one character, not two characters |
| Cast | **More characters** will be introduced → every stage must be multi-character from the start |
| Eye change | The 2→3-eye change **happens on screen, likely once**: the story opening is a coming-of-age arc (seeing the world as it is vs. as we want it to be). A hero moment — directed as its own shot, rig control `third_eye_open` 0→1 |
| Profile | In side views the third eye shows as an **artistic sliver** peeking over the forehead curve |
| Overlaps | **Thin white outline** separates black-on-black overlaps (limb over body, tail over leg). Line weight is part of the model sheet and fixed by the rig/shader, not left to AI |
| Control | Colby must be able to **direct**, not pick from AI outputs. AI never free-runs from text alone; every generation is conditioned on something Colby authored |

## The framing

The design is a flat black silhouette, three white eyes, no interior line work. That makes it:

- **Trivially rig-able** — a few flat shapes stay on-model in every frame. A rig is the only thing
  that *guarantees* three eyes and a clean outline.
- **Hostile to generative models** — they "correct" to two eyes, add fur/shading, and let the
  silhouette drift. A character LoRA reduces this; it doesn't eliminate it.
- **Overlap rule (decided):** a solid-black body hides limbs crossing in front of it, so
  overlapping black shapes get a **thin white outline**.

So: **the rig owns the character; AI is the support crew** (design exploration, boards,
backgrounds, in-betweens, optional restyle, upscale). Pure AI-from-mocap is tested anyway
because it is nearly free on this cluster.

Nothing on urania today fits this style — the installed checkpoints/LoRAs are photoreal. Cat work
gets its own models + `cat_*` workflows so the two never mix.

## Directing, not accepting — the control model

Colby's choices at each stage are the source of truth; AI fills gaps *under* those choices, never above them.

| Colby authors | Stored as | Drives |
|---|---|---|
| Script | Fountain (plain-text screenplay) in git | shot list |
| Shot list | per-shot manifest (YAML): characters+variant, set, camera, duration, lines, notes | everything downstream; re-renders are reproducible |
| Blocking + camera | Blender layout with proxy characters | animatic, pose/depth/line control maps |
| Performance | mocap + voice takes, chosen by Colby | rig animation, lip-sync |
| Look | model sheets, style frames, palette | LoRAs, shaders |
| Approvals | select + notes per version | what advances to the next stage |

Rules that follow from this:
- **Characters are packages**, not prompts: `characters/<name>/` = sheet, rig, LoRA, variants,
  rules. The cat is one package with a `2eye|3eye` variant switch (rig toggle; LoRA trained with
  explicit eye-count tags so the variants don't blend).
- **One character per AI pass.** Multi-character AI frames bleed identities; render each character
  as its own layer against the layout's control maps and composite.
- **Every AI output records** workflow, model hashes, seed, control inputs → any take regenerates
  exactly, or is re-directed by changing one input.

## Stages

1. **Character canon** — head → model sheet, *2-eye and 3-eye variants*: body (both stances), turnaround, expressions,
   visemes, third-eye behaviour. Generated with an image-edit model (Qwen-Image-Edit / FLUX Kontext
   class), then *curated and cleaned by hand*. The sheet is the source of truth for everything else.
2. **Character LoRA** — the fix for eye count + look drift. Trained on **Qwen-Image-Edit-2511**
   (the sheet's own model) with a trigger per variant. Dataset = the **approved** sheet images,
   cleaned to pure flat black/white (threshold + potrace vectorize), captions generated from
   character.toml. Also edit *pairs* the sheet already produces (3eye→2eye, neutral→expression) so
   the LoRA learns those exact edits. Trainer: ai-toolkit (Qwen-Image-Edit LoRA support; aarch64
   port risk) in a `clusterctl mode <node> train` role. Iterate: v1 from ~20–40 approvals → better
   candidates → more approvals → v2. Measured before a LoRA (2026-09-23, draft mode): bodies 17/17
   three-eyed; expressions 0/12 three-eyed until the variant brief stated the count, then 16/16 —
   but the third eye doesn't emote and noses/mouths drift, which is what the LoRA is for.
3. **Animatic** — script → boards (LoRA) → timing + scratch voice track.
4. **Performance capture** — phone video. Body: monocular markerless (GVHMR/WHAM class) → skeleton.
   Face: iPhone ARKit blendshapes or MediaPipe. Voice → visemes (Rhubarb-style) for the mouth.
   Retarget: human → biped-cat directly; quadruped mode is keyed/procedural, with mocap supplying
   head/face/timing.
5. **Animation** — Blender (Grease Pencil cut-out *or* 3D toon-shaded, per the look test), driven
   by the capture, hand-cleaned. Optional AI restyle pass (Wan VACE / Animate + LoRA).
6. **Finishing** — frame interpolation (installed), SeedVR2 upscale (installed), BGs, comp, edit.

## Model sheet tool (built)

`bin/sheet` + `pipeline/` (stdlib Python 3.11 on ceres, SSH-tunnels to a node's ComfyUI).
`projects/cat/characters/cat/character.toml` is the brief; `sheet run` → candidates (eye count
auto-checked) → `sheet approve` → approved images unblock the views that reference them.
`sheet export-ui` puts the same graph in both nodes' Workflows menu as `cat_model_sheet`.
Models (shared store on urania, visible on clio over NFS): qwen_image_edit_2511_fp8mixed,
qwen_2.5_vl_7b_fp8_scaled, qwen_image_vae, Lightning 4/8-step LoRAs, 2509 Multiple-angles LoRA.
Draft ≈ 14 s/image on urania; `--final` = 40 steps, cfg 4 (not yet timed).

## 3D build + ink look test (built, first pass)

`bin/char3d build cat` → `bin/char3d turnaround cat`. The edit model strips tail + whiskers from
the approved front and side views; Blender (5.2 on ceres, no Linux-ARM build exists) builds a
**visual hull** body with elliptical cross-sections plus a NURBS-curve tail from the silhouettes,
then renders flat-black + sticker-outline (inverted hulls) with the eyes/nose projected from the
sheet. Deterministic: the approved views define the shape.
- **Hunyuan3D-2mv doesn't work for this design** (2026-09-23): 7 runs, every one a card, plaque or
  box — a flat unshaded silhouette gives image-to-3D models no depth cues. Model left on the store.
- Known gaps in pass 1: planar projection smears eyes at 45°; arms merge into the torso; tail is
  lumpier than the drawing; whiskers not built. Next: modelled eye objects (rig controls, incl.
  `third_eye_open`), separate arm parts, whisker curves, then a rig.

## Storage layout (proposed)

In the **studio** ComfyUI profile ([comfyui-profiles.md](comfyui-profiles.md)), never `basedir/`.
Shared inputs go in `studio/input/cat/` (NFS-shared, both nodes see it); renders land in each node's
`studio/output/cat/`. Proposed tree under those:
`bible/` (sheet, LoRA dataset) · `ep<NN>/sh<NNN>/{capture,anim,render,final}` · `audio/`.

## Phase 1 experiments (decide the look + path)

1. **Model sheet** — generate body/turnaround/expression candidates from the head.
2. **Wan 2.2 Animate** — phone clip of Colby driving the cat head; measure drift.
3. **Blender rig** — minimal head rig (three-eye blinks, ears, visemes) driven by face capture + voice.
