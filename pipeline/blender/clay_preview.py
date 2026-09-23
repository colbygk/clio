"""Clay preview of a mesh from N angles, for judging geometry before any look is applied.

    blender -b -P clay_preview.py -- <in.glb> <out_prefix> [angles=0,90,180,270]
Writes <out_prefix>_<angle>.png. Angle 0 looks at the model's front (glTF +Z forward).
"""
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]
angles = [int(a) for a in (argv[2] if len(argv) > 2 else "0,90,180,270").split(",")]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]

# frame the model: bounding box of all meshes in world space
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center, size = (lo + hi) / 2, max(hi - lo)

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "SINGLE"
scene.render.resolution_x = scene.render.resolution_y = 512
scene.world = bpy.data.worlds.new("w")
scene.world.color = (1, 1, 1)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = size * 1.15
scene.collection.objects.link(cam)
scene.camera = cam

for a in angles:
    # Blender is Z-up after glTF import; the glTF front (+Z) becomes -Y. Orbit around Z.
    r = math.radians(a)
    d = size * 3
    cam.location = center + Vector((d * math.sin(r), -d * math.cos(r), 0))
    cam.rotation_euler = (math.radians(90), 0, r)
    scene.render.filepath = f"{out}_{a:03d}.png"
    bpy.ops.render.render(write_still=True)
