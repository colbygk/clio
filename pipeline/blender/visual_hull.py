"""Build the character in 3D from approved sheet views — no AI guessing the shape.

    blender -b -P visual_hull.py -- <front> <front_notail> <side> <side_notail> <out.blend>

Inputs are sheet images (black figure on white; the side view faces image-left). The *_notail
images are the same views with the tail (and whiskers) edited out, which separates the parts:

  body  a visual hull of the tail-less views with ELLIPTICAL cross-sections: each z-slice of the
        front silhouette is swept against the side silhouette's depth, rounded, and thin limbs
        (arms/legs narrower than half the slice) get round sections instead of full body depth.
  tail  the difference between each view and its tail-less twin, traced root->tip in both views
        and combined into one 3D bezier curve with a bevel: a bendable, rig-able tail.

Why not image-to-3D: Hunyuan3D-2mv read this flat black silhouette as a card or a box every time
(2026-09-23) — a design with no shading gives those models no depth cues. Here the approved views,
not a model's guess, define the shape.
"""
import sys
from collections import deque

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
front_p, front_nt_p, side_p, side_nt_p, out = argv[:5]
H = 256          # voxels from feet to ear tips
HEIGHT = 2.0     # metres; the figure is 2 m tall in the scene


def load(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[::-1]
    return px[..., :3].mean(axis=2)


def filled(gray):
    """Figure mask: everything not reachable from the border through light pixels."""
    light = gray > 0.6
    h, w = light.shape
    outside = np.zeros_like(light)
    q = deque((y, x) for y in range(h) for x in (0, w - 1) if light[y, x])
    q.extend((y, x) for x in range(w) for y in (0, h - 1) if light[y, x])
    for y, x in q:
        outside[y, x] = True
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and light[ny, nx] and not outside[ny, nx]:
                outside[ny, nx] = True
                q.append((ny, nx))
    return ~outside


def dilate(m, r):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def largest_component(m):
    lab = np.zeros(m.shape, np.int32)
    best, best_n, cur = 0, 0, 0
    for y, x in zip(*np.nonzero(m)):
        if lab[y, x]:
            continue
        cur += 1
        lab[y, x] = cur
        q, n = deque([(y, x)]), 0
        while q:
            cy, cx = q.popleft()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < m.shape[0] and 0 <= nx < m.shape[1] and m[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = cur
                    q.append((ny, nx))
        if n > best_n:
            best, best_n = cur, n
    return lab == best


def features(gray, fig):
    """The drawn marks inside the silhouette (eyes, nose, sliver) as white-on-black: light
    regions inside the figure that don't touch its outer edge, so the sticker band is excluded."""
    light = (gray > 0.6) & fig
    edge = dilate(~fig, 3)
    keep = np.zeros_like(light)
    seen = np.zeros_like(light)
    for y, x in zip(*np.nonzero(light)):
        if seen[y, x]:
            continue
        comp, q, touches = [], deque([(y, x)]), False
        seen[y, x] = True
        while q:
            cy, cx = q.popleft()
            comp.append((cy, cx))
            touches |= edge[cy, cx]
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx_ = cy + dy, cx + dx
                if 0 <= ny < light.shape[0] and 0 <= nx_ < light.shape[1] and light[ny, nx_] and not seen[ny, nx_]:
                    seen[ny, nx_] = True
                    q.append((ny, nx_))
        if not touches:
            keep[tuple(np.array(comp).T)] = True
    return keep


def save_mask(m, path):
    h, w = m.shape
    img = bpy.data.images.new(path.rsplit("/", 1)[-1], w, h)
    rgba = np.repeat(m[::-1, :, None].astype(np.float32), 4, axis=2)
    rgba[..., 3] = 1
    img.pixels[:] = rgba.ravel()
    img.filepath_raw, img.file_format = path, "PNG"
    img.save()


def view(full_p, nt_p, tag):
    """-> (body mask, tail mask) cropped to the figure and resampled to H rows, plus the
    projection that maps scene (horizontal, z) back onto the features image of this view."""
    # marks come from the approved view itself: the tail-less edit is reframed by a few pixels
    gray = load(full_p)
    full, nt = filled(gray), filled(load(nt_p))
    tail = largest_component(full & ~dilate(nt, 4))
    ys, xs = np.nonzero(full)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    k = H / (y1 - y0)
    rows = (y0 + np.arange(H) / k).astype(int)
    wn = round((x1 - x0) * k)
    cols = (x0 + np.arange(wn) / k).astype(int)
    crop = lambda m: m[rows][:, cols]
    fpath = out.replace(".blend", f"_features_{tag}.png")
    save_mask(features(gray, full), fpath)
    ih, iw = full.shape
    proj = dict(path=fpath, x0=float(x0), y0=float(y0), k=float(k), w=iw, h=ih, n=wn)
    return crop(nt & full), crop(tail), proj


FB, FT, PF = view(front_p, front_nt_p, "front")    # [z row from top, x]
SB, ST, PS = view(side_p, side_nt_p, "side")       # [z row from top, y]; image-left = the front = -Y
print(f"front {FB.shape}, side {SB.shape}, tail px front {FT.sum()} side {ST.sum()}")

# ---- body: elliptical visual hull --------------------------------------------------------------
nx, ny = FB.shape[1], SB.shape[1]
occ = np.zeros((H, nx, ny), bool)
X, Y = np.meshgrid(np.arange(nx) + 0.5, np.arange(ny) + 0.5, indexing="ij")


def runs(row):
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


for z in range(H):
    xr, yr = runs(FB[z]), runs(SB[z])
    if not xr or not yr:
        continue
    y0, y1 = yr[0][0], yr[-1][1]
    cy, depth = (y0 + y1) / 2, (y1 - y0) / 2
    widest = max(b - a for a, b in xr)
    for a, b in xr:
        cx, half = (a + b) / 2, (b - a) / 2
        dep = depth if (b - a) >= 0.5 * widest else min(depth, half)
        occ[z] |= ((X - cx) / half) ** 2 + ((Y - cy) / max(dep, 0.5)) ** 2 <= 1

s = HEIGHT / H
bpy.ops.wm.read_factory_settings(use_empty=True)

# voxels -> points -> mesh via a Volume: build a blocky mesh of the surface faces, then remesh
pad = np.pad(occ[::-1], 1)                    # z index 0 = feet
verts, faces, vid = [], [], {}


def v(key):
    if key not in vid:
        vid[key] = len(verts)
        verts.append(key)
    return vid[key]


for axis in range(3):
    d = np.diff(pad.astype(np.int8), axis=axis)
    for sign in (1, -1):
        for z, x, y in zip(*np.nonzero(d == sign)):
            if axis == 0:
                quad = [v((a, b, z)) for a, b in ((x - 1, y - 1), (x, y - 1), (x, y), (x - 1, y))]
            elif axis == 1:
                quad = [v((x, b, a)) for a, b in ((z - 1, y - 1), (z, y - 1), (z, y), (z - 1, y))]
            else:
                quad = [v((b, y, a)) for a, b in ((z - 1, x - 1), (z, x - 1), (z, x), (z - 1, x))]
            faces.append(quad if sign == 1 else quad[::-1])

me = bpy.data.meshes.new("body")
me.from_pydata([((x - nx / 2) * s, (y - ny / 2) * s, z * s) for x, y, z in verts], [], faces)
me.validate()
body = bpy.data.objects.new("body", me)
bpy.context.scene.collection.objects.link(body)
rm = body.modifiers.new("round", "REMESH")
rm.mode, rm.voxel_size = "VOXEL", s * 1.2
sm = body.modifiers.new("smooth", "CORRECTIVE_SMOOTH")
sm.iterations, sm.use_only_smooth = 20, True
bpy.context.view_layer.objects.active = body
for m in list(body.modifiers):
    bpy.ops.object.modifier_apply(modifier=m.name)
bpy.ops.object.shade_smooth()

# ---- tail: trace root->tip in each view, combine into one 3D curve -----------------------------


def centerline(tail, body, n=24):
    """Ordered (col, row, half-width) samples from the tail's root (next to the body) to its tip."""
    pts = set(zip(*np.nonzero(tail)))
    near = dilate(body, 2)
    roots = [p for p in pts if near[p]]
    dist = {p: 0 for p in roots}
    q = deque(roots)
    while q:
        p = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nb = (p[0] + dy, p[1] + dx)
            if nb in pts and nb not in dist:
                dist[nb] = dist[p] + 1
                q.append(nb)
    far = max(dist.values())
    out = []
    for i in range(n):
        lo, hi = far * i / n, far * (i + 1) / n
        band = [p for p, d in dist.items() if lo <= d < hi or (i == n - 1 and d == far)]
        rows, cols = zip(*band)
        out.append((np.mean(cols), np.mean(rows), len(band) / max(hi - lo, 1) / 2))
    return out


ft, st = centerline(FT, FB), centerline(ST, SB)
cu = bpy.data.curves.new("tail", "CURVE")
cu.dimensions = "3D"
cu.bevel_depth = 1.0
cu.bevel_resolution = 6
cu.use_fill_caps = True
sp = cu.splines.new("NURBS")
sp.points.add(len(ft) - 1)
for p, (fx, fz, fr), (sy, sz, sr) in zip(sp.points, ft, st):
    z = H - (fz + sz) / 2                          # rows are from the top
    p.co = ((fx - nx / 2) * s, (sy - ny / 2) * s, z * s, 1)
    p.radius = min(fr, sr) * s
sp.order_u, sp.use_endpoint_u = 4, True
tail = bpy.data.objects.new("tail", cu)
bpy.context.scene.collection.objects.link(tail)

# how the look pass projects the drawn features back on: image uv = A * scene coord + B
for tag, P in (("front", PF), ("side", PS)):
    # col = x0 + (c / s + n / 2) / k ; row = y0 + (H - z / s) / k ; u = col / w ; v = 1 - row / h
    bpy.context.scene[f"proj_{tag}"] = dict(
        path=P["path"], Au=1 / (s * P["k"] * P["w"]), Bu=(P["x0"] + P["n"] / 2 / P["k"]) / P["w"],
        Av=1 / (s * P["k"] * P["h"]), Bv=1 - (P["y0"] + H / P["k"]) / P["h"])
print(f"body {len(body.data.vertices)} verts; tail {len(ft)} points")
bpy.ops.wm.save_as_mainfile(filepath=out)
bpy.ops.export_scene.gltf(filepath=out.replace(".blend", ".glb"))
