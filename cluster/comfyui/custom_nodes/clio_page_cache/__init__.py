"""Free Page Cache — evict the model files from the kernel's page cache, from inside a workflow.

ComfyUI sizes each model load from torch.cuda.mem_get_info, which on the GB10's unified memory
counts page cache as used. After a session of model reads (~50 GB of cache), ComfyUI sees a few GB
free and loads the model "partially", streaming some weights every step (~8% slower on a 149-frame
Wan run). See docs/comfyui-deployed.md, "Slow generations".

`clusterctl freemem` drops the whole cache, but needs root. This node runs as the container user,
so it uses posix_fadvise(DONTNEED) on the model files instead — they are nearly all of the cache.
Pages a process still has mapped are left alone, so nothing loaded is disturbed.

Wire it inline just before the sampler (MODEL -> Free Page Cache -> KSampler): the free-memory
check happens when the sampler loads the model, and the passthrough fixes the order. It re-runs
on every queue, unlike most nodes, since the cache refills between runs.
"""
import logging
import os

import folder_paths


class AnyType(str):
    """Matches any socket type, so the passthrough fits MODEL, CLIP, LATENT, IMAGE, ..."""
    def __ne__(self, other):
        return False


ANY = AnyType("*")


def _mem_mb(key):
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1]) // 1024
    return 0


def _model_files():
    """Every file under every model folder ComfyUI knows, symlinks resolved, each once."""
    seen = set()
    for paths, _exts in folder_paths.folder_names_and_paths.values():
        for root in paths:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, names in os.walk(root, followlinks=True):
                for n in names:
                    p = os.path.realpath(os.path.join(dirpath, n))
                    if p not in seen:
                        seen.add(p)
                        yield p


class FreePageCache:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "optional": {"passthrough": (ANY,)}}

    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("passthrough",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "utils/memory"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # never equal to itself, so ComfyUI never serves this from cache

    def run(self, passthrough=None):
        cached, free = _mem_mb("Cached"), _mem_mb("MemFree")
        n = 0
        for p in _model_files():
            try:
                fd = os.open(p, os.O_RDONLY)
            except OSError:
                continue
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                n += 1
            except OSError:
                pass
            finally:
                os.close(fd)
        logging.info("Free Page Cache: %d model files; cache %.1f -> %.1f GB, free %.1f -> %.1f GB",
                     n, cached / 1024, _mem_mb("Cached") / 1024, free / 1024, _mem_mb("MemFree") / 1024)
        return (passthrough,)


NODE_CLASS_MAPPINGS = {"ClioFreePageCache": FreePageCache}
NODE_DISPLAY_NAME_MAPPINGS = {"ClioFreePageCache": "Free Page Cache"}
