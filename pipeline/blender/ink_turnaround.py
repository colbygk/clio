"""The 3D-with-ink-shader look test: render the hull as the flat 2D character from any angle.

    blender -b -P ink_turnaround.py -- <hull.blend> <out_prefix> [angles=0,45,...,315]

- Body and tail: flat black emission, no lighting — the design has no shading.
- Sticker outline (the approved look): two inverted-hull shells, a thin white band and a fine
  black edge outside it. Where the tail passes in front of the body, the tail's band separates
  them, which is the overlap rule for free.
- Drawn marks (eyes, nose, profile sliver) are the approved sheet's own pixels, projected from
  the front view onto front-facing surfaces and from the side view onto side-facing ones.
- Standard view transform, so white is white (Blender's default AgX greys it).
"""
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]
angles = [int(a) for a in (argv[2] if len(argv) > 2 else "0,45,90,135,180,225,270,315").split(",")]
BAND, EDGE = 0.014, 0.006          # metres, on a 2 m figure

bpy.ops.wm.open_mainfile(filepath=src)
scene = bpy.context.scene
body, tail = bpy.data.objects["body"], bpy.data.objects["tail"]


def emission(name, color=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    outn = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], outn.inputs[0])
    if color:
        em.inputs["Color"].default_value = (*color, 1)
    return m, nt, em


def shell(name, color, thickness):
    m, _, _ = emission(name, color)
    m.use_backface_culling = True
    return m, thickness


# ---- the character material: black, plus the projected drawn marks ----------------------------
ink, nt, em = emission("ink")
geo = nt.nodes.new("ShaderNodeNewGeometry")
sep_p = nt.nodes.new("ShaderNodeSeparateXYZ")
sep_n = nt.nodes.new("ShaderNodeSeparateXYZ")
nt.links.new(geo.outputs["Position"], sep_p.inputs[0])
nt.links.new(geo.outputs["Normal"], sep_n.inputs[0])


def math_node(op, a, b=None):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs[0]


def projected(tag, horiz):
    """Sample this view's features image at the projected (horiz, z) position."""
    p = scene[f"proj_{tag}"]
    u = math_node("ADD", math_node("MULTIPLY", horiz, p["Au"]), p["Bu"])
    v = math_node("ADD", math_node("MULTIPLY", sep_p.outputs["Z"], p["Av"]), p["Bv"])
    comb = nt.nodes.new("ShaderNodeCombineXYZ")
    nt.links.new(u, comb.inputs[0])
    nt.links.new(v, comb.inputs[1])
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(p["path"])
    tex.interpolation = "Closest"
    tex.extension = "CLIP"
    nt.links.new(comb.outputs[0], tex.inputs["Vector"])
    return tex.outputs["Color"]


front = projected("front", sep_p.outputs["X"])
side = projected("side", sep_p.outputs["Y"])
# weights: front marks on surfaces facing -Y (the front), side marks on surfaces facing +-X
w_front = math_node("GREATER_THAN", math_node("MULTIPLY", sep_n.outputs["Y"], -1.0), 0.55)
w_side = math_node("GREATER_THAN", math_node("ABSOLUTE", sep_n.outputs["X"]), 0.75)
def luminance(color):
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    nt.links.new(color, bw.inputs[0])
    return bw.outputs[0]


mark_f = math_node("MULTIPLY", luminance(front), w_front)
mark_s = math_node("MULTIPLY", luminance(side), w_side)
mark = math_node("GREATER_THAN", math_node("MAXIMUM", mark_f, mark_s), 0.5)
nt.links.new(mark, em.inputs["Color"])

for ob in (body, tail):
    ob.data.materials.clear()
    ob.data.materials.append(ink)
tail.data.materials.clear()
flat, _, _ = emission("black", (0, 0, 0))
tail.data.materials.append(flat)

# ---- sticker outline: inverted hulls ----------------------------------------------------------
white_m, _ = shell("band", (1, 1, 1), BAND)
edge_m, _ = shell("edge", (0, 0, 0), BAND + EDGE)
for ob in (body, tail):
    for mat, t in ((white_m, BAND), (edge_m, BAND + EDGE)):
        ob.data.materials.append(mat)
        mod = ob.modifiers.new(mat.name, "SOLIDIFY")
        mod.thickness, mod.offset = t, 1
        mod.use_flip_normals, mod.use_rim = True, False
        mod.material_offset = len(ob.data.materials) - 1

# ---- render setup ------------------------------------------------------------------------------
scene.render.engine = "BLENDER_EEVEE"
scene.view_settings.view_transform = "Standard"
scene.render.resolution_x, scene.render.resolution_y = 896, 1152
scene.world = bpy.data.worlds.new("paper")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = 2.35
scene.collection.objects.link(cam)
scene.camera = cam
center = Vector((0, 0, 1.0))
for a in angles:
    r = math.radians(a)          # 0 = front (camera on -Y), 90 = the character's left (+X)
    cam.location = center + Vector((6 * math.sin(r), -6 * math.cos(r), 0))
    cam.rotation_euler = (math.radians(90), 0, r)
    scene.render.filepath = f"{out}_{a:03d}.png"
    bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=out + "_scene.blend")
