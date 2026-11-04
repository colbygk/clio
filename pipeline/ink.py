"""ink.py — Codubee's sticker outline, drawn by rule instead of by the model.

The models keep losing the outline (a thin light line on a white page is the first detail a 4-step
sampler drops), so it is never asked of them any more. Codubee is a flat solid-black shape and the
sets are thin ink lines, which makes him separable by morphology alone:

    black pixels -> open with a disk (thin ink lines vanish, solid shapes stay) -> drop small
    components -> fill holes (the eyes) = his silhouette
    a ring round it cleared to white (removes any outline the model drew itself), then
    silhouette grown by `band` px, painted white; grown by band+edge px, painted black underneath

Every frame gets the same band at the same width, so the outline cannot flicker or vanish.
ImageMagick only, like the rest of the Mac-side tooling. Friend (white, line-drawn) and the sets
are untouched; a solid black thing that is not Codubee (a cast shadow) is outlined too, which is
why a shot can set `outline = false`.
"""
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# by framing, in px at 1280 wide: (band, edge, open). A close-up's outline is drawn bigger, and its
# ink lines (whiskers) are thicker, so the disk that removes lines from the mask grows with it.
WIDTH = {"wide": (4, 2, 5), "medium": (6, 2, 6), "close": (10, 3, 11), "extreme_close": (14, 4, 14)}
MIN_AREA = 4000      # px at 1280 wide; smaller solid blobs (grass clumps, hatching) are not a character


def _magick(*args):
    subprocess.run(["magick", *map(str, args)], check=True)


def silhouette(src, dest, radius=6, scale=1.0):
    """White-on-black mask of the solid black shapes in src, holes filled."""
    _magick(src, "-colorspace", "gray", "-threshold", "25%", "-negate",
            "-morphology", "Open", f"Disk:{radius}",
            "-define", f"connected-components:area-threshold={int(MIN_AREA * scale * scale)}",
            "-define", "connected-components:mean-color=true", "-connected-components", "8",
            "-morphology", "Close", "Disk:3",
            # fill holes: flood the background from a border pixel, then everything unflooded is solid
            "-bordercolor", "black", "-border", "1", "-fill", "gray50", "-draw", "color 0,0 floodfill",
            "-shave", "1x1", "-fill", "white", "+opaque", "gray50", "-fill", "black", "-opaque", "gray50",
            dest)


def outline(src, dest, camera="medium", clear=4):
    """Write src with the sticker outline drawn round every solid black shape. `clear` (px at 1280)
    is how far past the new outline a model-drawn one is wiped: wide on a plain white sheet, narrow in
    a scene, where the ring would also wipe the set's linework near the character."""
    size = subprocess.run(["magick", "identify", "-format", "%wx%h", str(src)],
                          capture_output=True, text=True, check=True).stdout
    k = int(size.split("x")[0]) / 1280          # widths are tuned at 1280; clips render at 832
    band, edge, radius = (max(1, round(v * k)) for v in WIDTH.get(camera, WIDTH["medium"]))
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        silhouette(src, t / "solid.png", radius, k)
        _magick(t / "solid.png", "-morphology", "Dilate", f"Disk:{band}", t / "band.png")
        _magick(t / "solid.png", "-morphology", "Dilate", f"Disk:{band + edge}", t / "edge.png")
        # a model-drawn outline (copied from the sheet art) sits a few px out and would show as a
        # second line: clear a wider ring to paper white first (theirs reach ~16 px out at 1024)
        _magick(t / "solid.png", "-morphology", "Dilate", f"Disk:{band + edge + max(1, round(clear * k))}", t / "clear.png")
        _magick(src,
                "(", "-size", size, "xc:white", t / "clear.png", "-alpha", "off", "-compose", "CopyOpacity", "-composite", ")",
                "-compose", "Over", "-composite",
                "(", "-size", size, "xc:black", t / "edge.png", "-alpha", "off", "-compose", "CopyOpacity", "-composite", ")",
                "-compose", "Over", "-composite",
                "(", "-size", size, "xc:white", t / "band.png", "-alpha", "off", "-compose", "CopyOpacity", "-composite", ")",
                "-compose", "Over", "-composite",
                "(", src, t / "solid.png", "-alpha", "off", "-compose", "CopyOpacity", "-composite", ")",
                "-compose", "Over", "-composite", dest)
    return dest


def outline_video(src, dest, camera="medium", fps=None):
    """Every frame of a clip through outline(), in parallel; same frame rate unless fps is given."""
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "in").mkdir(), (t / "out").mkdir()
        rate = fps or subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
             "-of", "csv=p=0", str(src)], capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), str(t / "in" / "%05d.png")], check=True)
        frames = sorted((t / "in").glob("*.png"))
        with ThreadPoolExecutor(os.cpu_count() or 4) as pool:
            list(pool.map(lambda f: outline(f, t / "out" / f.name, camera), frames))
        tmp = t / "v.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(rate), "-i", str(t / "out" / "%05d.png"),
                        "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(tmp)], check=True)
        shutil.move(tmp, dest)
    return dest
