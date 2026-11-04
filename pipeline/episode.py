"""episode.py — one episode (script + shot list) as the tools see it.

    ep = Episode("ep01")
    for shot in ep.shots: ...
    ep.ref_for("cat:2eye", "trotting along a fence")   # -> the approved sheet image to condition on

bin/keys, bin/tween, bin/sound and bin/cut all read shots.toml through this, so they agree on
timing (shot start times), file locations, and which approved character art stands for a cast
member in a given pose.
"""
import json
import re
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = REPO / "projects/cat"


class Episode:
    def __init__(self, eid):
        self.id = eid
        self.dir = PROJECT / "episodes" / eid
        self.spec = tomllib.loads((self.dir / "shots.toml").read_text())
        self.meta = self.spec.get("meta", {})
        self.sets = self.spec.get("sets", {})
        self.music = self.spec.get("music", {})
        self.shots = self.spec["shots"]
        t = 0.0
        for s in self.shots:          # running timeline: where each shot starts in the cut
            s["start"] = t
            t += s["seconds"]
        self.length = t

    def shot(self, sid):
        for s in self.shots:
            if s["id"] == sid:
                return s
        raise KeyError(f"no shot {sid} in {self.id}")

    def select(self, ids):
        return [self.shot(i) for i in ids.split(",")] if ids else self.shots

    # ---- timing shared by tween (what to animate), sound (music spans) and cut (assembly) ----
    TAIL_MIN = 0.75     # leftover time after the last key shorter than this is a held frame

    def segments(self, shot):
        """[(first key index, last key index or None, seconds)]: one clip per pair of keys, plus a
        forward-only tail when the shot runs on past its last key."""
        keys = shot.get("keys", [])
        segs = [(i, i + 1, keys[i + 1]["at"] - keys[i]["at"]) for i in range(len(keys) - 1)]
        tail = shot["seconds"] - keys[-1]["at"]
        if tail >= self.TAIL_MIN:
            segs.append((len(keys) - 1, None, tail))
        return segs

    def music_spans(self):
        """[(cue, start seconds, length)]: a cue runs until the next shot that names one."""
        spans, cur = [], None
        for s in self.shots:
            if "music" in s:
                if cur:
                    spans.append((cur[0], cur[1], s["start"] - cur[1]))
                cur = (s["music"], s["start"])
        if cur:
            spans.append((cur[0], cur[1], self.length - cur[1]))
        return [x for x in spans if x[0] != "silence"]

    # ---- generated media lives here (gitignored); recipes go to runs.jsonl (committed) ----
    def path(self, *parts):
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def log(self, **rec):
        with (self.dir / "runs.jsonl").open("a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}) + "\n")

    # ---- cast -> approved character art ----
    @staticmethod
    def parse_cast(c):
        char, _, variant = c.partition(":")
        return char, variant

    def ref_for(self, cast, pose, camera=""):
        """Pick the approved sheet image that best matches what this key asks the character to do.
        Keyword-based on purpose: it is a starting reference, and the pose text still directs."""
        # The episode's own [refs] win: a drawing Colby picked from this episode as the design to
        # hold to (2026-10-29: Friend from s040 k1, the teen's head from s120 k0)
        if cast in self.spec.get("refs", {}):
            return self.dir / self.spec["refs"][cast], "episode_ref"
        char, variant = self.parse_cast(cast)
        pkg = PROJECT / "characters" / char
        approved = json.loads((pkg / "approved.json").read_text()) if (pkg / "approved.json").exists() else {}
        have = approved.get(variant, {})
        spec = tomllib.loads((pkg / "character.toml").read_text())
        p = pose.lower()
        wants = []
        if char == "friend" and re.search(r"\b(grin|sly|teas)", p):
            wants.append("expr_sly")
        if char == "friend" and re.search(r"satisf", p):
            wants.append("expr_satisfied")
        if camera in ("close", "extreme_close") or re.search(r"\b(face|eyes?|forehead|tear)\b", p):
            wants.append("head_front")
        if re.search(r"\b(sit|sitting|sits|crouch|licking|panting|watching|alone|dropping)", p):
            wants.append("body_sit_front")
        wants += ["body_quadruped_side", "body_sit_front", "head_front"]
        scene = spec["character"].get("scene_ref")
        for view in wants:
            if scene and view.startswith("body_"):
                return pkg / scene, "scene_ref"
            if view in have:
                return pkg / have[view], view
            # the base variant's head is Colby's original drawing, never recorded in approved.json
            base = "derive_from" not in spec["variants"].get(variant, {})
            if view == "head_front" and base and spec["character"].get("source"):
                return pkg / spec["character"]["source"], view
        raise LookupError(f"{cast}: nothing approved yet")

    def describe(self, cast, body=True):
        """How a cast member is named and pinned down in a key-frame direction. Codubee's outline is
        not asked for: pipeline/ink.py draws it on every frame by rule."""
        # body=False for face close-ups: the anatomy rule ("stands, walks ... on all fours") made the
        # model pull back to a full figure (v2 s160/s170)
        return self._describe(*self.parse_cast(cast))[0], self.describe_static(*self.parse_cast(cast), body=body)

    @classmethod
    def describe_static(cls, char, variant, body=True):
        """The description alone, with the character's anatomy rule; bin/lora captions use it too."""
        desc = cls._describe(char, variant)[1]
        anatomy = cls.character(char)["character"].get("anatomy")
        return f"{desc}, {anatomy}" if anatomy and body else desc

    def lora_for(self, cast):
        """(LoRA file, strength, trigger) for a cast member, once its character.toml [lora] names a
        trained file in `use`; None until then."""
        char, _ = self.parse_cast(cast)
        lo = self.character(char).get("lora", {})
        return (lo["use"], lo.get("strength", 1.0), lo["trigger"]) if lo.get("use") else None

    @staticmethod
    def character(char):
        return tomllib.loads((PROJECT / "characters" / char / "character.toml").read_text())

    @staticmethod
    def _describe(char, variant):
        if char == "cat":
            eyes = "THREE eyes (two side by side and a third centred on the forehead)" if variant.endswith("3eye") \
                else "exactly TWO eyes and no eye on the forehead"
            age = "a small black kitten" if variant.startswith("kitten") else "a lanky teenage black cat"
            return "Codubee", f"Codubee, {age} with {eyes}, drawn as a flat solid black shape"
        if char == "friend":
            return "Friend", "Friend, a lanky white cat drawn only in loose black brush-pen lines, with a minimal face"
        return char, char
