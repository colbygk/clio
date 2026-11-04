"""check.py — a local vision model looks at a frame and reports each cat's anatomy.

ep01 v1 let through cats standing like people, a six-limbed "centaur", and frames with the wrong
eye count, because the only check was a person scanning contact sheets. This asks Qwen3.5-9B (a
vision-language model ComfyUI runs natively through TextGenerate) the same questions about every
candidate, and compares the answers with what the shot's cast should look like:

    with Comfy("urania") as c:
        report = check(c, Path("cand.png"), ["cat:2eye", "friend:teen"])
        report["ok"], report["problems"]    # False, ["Codubee is upright on its hind legs"]

A flag for the reviewer and for `keys auto`, not a verdict: the model miscounts now and then.
"""
import json
import re
from pathlib import Path

VLM = "qwen3.5_9b_bf16.safetensors"

QUESTION = (
    "Look carefully at this black-and-white cartoon frame. List every cat in it. For each cat give: "
    "\"colour\": \"black\" (a solid black shape) or \"white\" (white, drawn only with black lines); "
    "\"upright\": true if it stands or walks on its hind legs like a person, false if it is on all "
    "fours, sitting, lying, leaping or crouching like a real cat; "
    "\"limbs\": how many legs and arms the cat has in total (count the ones drawn, hidden ones don't count); "
    "\"eyes\": how many eyes are visible on its face, including any eye on the forehead. "
    "Answer with only a JSON list of objects, e.g. "
    "[{\"colour\": \"black\", \"upright\": false, \"limbs\": 4, \"eyes\": 2}]."
)


def ask(c, image_name, question=QUESTION, max_length=300):
    """Run the question against an uploaded image; return the model's raw answer text."""
    g = {
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": VLM, "type": "qwen_image", "device": "default"}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "gen": {"class_type": "TextGenerate", "inputs": {"clip": ["clip", 0], "prompt": question,
                                                         "max_length": max_length, "sampling_mode": "off",
                                                         "image": ["img", 0]}},
        "show": {"class_type": "PreviewAny", "inputs": {"source": ["gen", 0]}},
    }
    pid = c.submit(g)
    c.wait(pid)
    out = c.get(f"/history/{pid}")[pid]["outputs"]["show"]
    text = out.get("text") or out.get("string") or next(iter(out.values()))
    return text[0] if isinstance(text, list) else text


def expected(cast):
    """What each cast member should look like: [(name, colour, eyes)]. Eyes None = don't check."""
    want = []
    for m in cast:
        char, _, variant = m.partition(":")
        if char == "cat":
            want.append(("Codubee", "black", 3 if variant.endswith("3eye") else 2))
        elif char == "friend":
            want.append(("Friend", "white", None))
    return want


def ask_safely(c, image_name):
    """ask(), surviving the GB10's intermittent "CUDA error: operation not permitted" when the
    vision model loads next to the image model: unload everything and try once more. None if the
    second try fails too: a check that can't run must not stop a production run."""
    for attempt in (1, 2):
        try:
            return ask(c, image_name)
        except RuntimeError as e:
            print(f"    (check failed, attempt {attempt}: {str(e)[:120]}…)")
            c.post("/free", {"unload_models": True, "free_memory": True})
    return None


def check(c, path: Path, cast, subfolder="cat/check", camera="", side_on=True):
    """camera close/extreme_close: no body in frame, so posture is not asked about (a head-and-
    shoulders shot reads as "upright" to the model)."""
    name = c.upload(path, subfolder=subfolder, name=f"{path.parent.name}_{path.name}")
    raw = ask_safely(c, name)
    if raw is None:
        return {"ok": False, "problems": ["check unavailable"], "cats": [], "raw": ""}
    m = re.search(r"\[.*\]", raw, re.S)
    try:
        cats = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        cats = []
    problems = []
    if not cats and cast:
        problems.append("no cats recognised")
    if cats and not cast:      # an empty shot: v2's s030 grew a cartoon creature
        problems.append(f"{len(cats)} character(s) in a shot that should be empty")
    for who, colour, eyes in expected(cast):
        found = [x for x in cats if str(x.get("colour", "")).lower() == colour]
        if not found:
            problems.append(f"{who} missing")
            continue
        if len(found) > 1:
            problems.append(f"{len(found)} {colour} cats (one {who} expected)")
        x = found[0]
        if x.get("upright") is True and camera not in ("close", "extreme_close"):
            problems.append(f"{who} upright on hind legs")
        if isinstance(x.get("limbs"), int) and x["limbs"] > 4:
            problems.append(f"{who} has {x['limbs']} limbs")
        # 1 eye is a profile view, fine side-on; facing the camera it is a lost eye (v2 s020)
        if eyes and isinstance(x.get("eyes"), int) and x["eyes"] != eyes and not (x["eyes"] == 1 and side_on):
            problems.append(f"{who} has {x['eyes']} eyes (want {eyes})")
    return {"ok": not problems, "problems": problems, "cats": cats, "raw": raw}
