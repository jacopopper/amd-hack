"""Blender source for an illustrative, non-surveyed studio presentation.

Run with Blender --background --python this_file -- --output /absolute/room.glb.
This is artist-created demo geometry, not a reconstruction or a measurement.
"""
import argparse
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
rng = np.random.default_rng(12)


def material(name, color, roughness=.6, metallic=0, texture=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    if texture:
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = texture
        mat.node_tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
    return mat


def image(name, rgb):
    h, w = rgb.shape[:2]
    result = bpy.data.images.new(name, width=w, height=h, alpha=True)
    rgba = np.ones((h, w, 4), np.float32)
    rgba[..., :3] = np.clip(rgb, 0, 1)
    result.pixels.foreach_set(rgba.ravel())
    result.pack()
    return result


y, x = np.mgrid[:512, :512] / 512
grain = (.035 * np.sin(180 * x + 3 * np.sin(8 * y)) + .02 * np.sin(440 * x + 8 * y)
         + .01 * rng.normal(size=x.shape) + .025 * np.sin(27 * x + np.sin(y * 8)))
oak_image = image("Warm oak grain", np.array([.36, .18, .075]) + grain.T[..., None] * .55)
walnut_image = image("Walnut grain", np.array([.22, .105, .045]) + grain[..., None] * .35)
weave = .014 * np.sin(x * 512 * math.pi) + .01 * np.sin(y * 512 * math.pi) + rng.normal(0, .006, x.shape)
fabric_image = image("Sage woven upholstery", np.array([.24, .32, .24]) + weave[..., None])
white = material("Warm plaster", (.82, .80, .74), .95)
ivory = material("Ivory painted joinery", (.83, .82, .76), .48)
wood = material("Walnut cabinet", (.3, .16, .08), .42, texture=walnut_image)
oak = [material(f"Oak plank {i}", (.5, .28, .12), .48, texture=oak_image) for i in range(4)]
green = material("Sage woven linen", (.25, .32, .22), .95, texture=fabric_image)
dark = material("Soft charcoal", (.026, .031, .031), .65)
screen = material("Dark display glass", (.025, .043, .052), .2, .2)
chrome = material("Brushed steel", (.54, .57, .58), .27, .85)
brass = material("Aged brass", (.52, .34, .12), .32, .7)
stone = material("Cream stone", (.7, .67, .58), .5)
linen = material("Natural linen", (.77, .76, .68), 1)
clothing = material("White cotton", (.85, .85, .81), 1)
glass = material("Textured glass block", (.55, .67, .66), .22, .18)
blue = material("Blue grey", (.17, .24, .29), .75)
brick = [material(f"Sand brick {i}", (.56 + i * .025, .37 + i * .022, .22 + i * .018), .9) for i in range(4)]


def box(name, pos, size, mat, bevel=.015):
    bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    # Stable physical grain directions, rather than Blender's cube-atlas UVs.
    uv = obj.data.uv_layers.active
    for polygon in obj.data.polygons:
        for loop_index in polygon.loop_indices:
            co = obj.data.vertices[obj.data.loops[loop_index].vertex_index].co
            if abs(polygon.normal.z) > .9:
                value = (co.x / size[0] + .5, co.y / size[1] + .5)
            elif abs(polygon.normal.y) > .9:
                value = (co.x / size[0] + .5, co.z / size[2] + .5)
            else:
                value = (co.y / size[1] + .5, co.z / size[2] + .5)
            uv.data[loop_index].uv = value
    if bevel:
        mod = obj.modifiers.new("Soft furniture edges", "BEVEL")
        mod.width = min(bevel, min(size) * .35)
        mod.segments = 3
        bpy.ops.object.modifier_apply(modifier=mod.name)
        mod = obj.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return obj


def rod(name, a, b, radius, mat):
    a, b = Vector(a), Vector(b)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=radius, depth=(b - a).length, location=(a + b) / 2)
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = (b - a).to_track_quat("Z", "Y").to_euler()
    obj.data.materials.append(mat)
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def cable(name, coordinates, radius=.007, mat=dark):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 3
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(coordinates) - 1)
    for point, co in zip(spline.bezier_points, coordinates):
        point.co = co
        point.handle_left_type = point.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def wall(name, vertices):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(white)
    obj["cutaway_wall"] = True
    return obj


# A complete, deliberately approximate shell. Interior-facing walls disappear
# naturally from the outside; there is no roof to obscure the inspection view.
box("Floating dark plinth", (0, 0, -.13), (4.8, 5.4, .2), dark, .04)
for row in range(25):
    yy = -2.5 + row * .208
    offset = (row % 3) * .32
    for col in range(5):
        left, right = max(-2.3, -3.2 + col * 1.25 + offset), min(2.3, -1.95 + col * 1.25 + offset)
        if right > left:
            box("Oak floor", ((left + right) / 2, yy, -.012), (right - left - .003, .204, .035), oak[row % 4], .002)
wall("Kitchen wall", [(-2.35, 2.65, 0), (2.35, 2.65, 0), (2.35, 2.65, 2.8), (-2.35, 2.65, 2.8)])
wall("Storage wall", [(-2.35, -2.65, 0), (-2.35, 2.65, 0), (-2.35, 2.65, 2.8), (-2.35, -2.65, 2.8)])
wall("Window wall", [(2.35, 2.65, 0), (2.35, -2.65, 0), (2.35, -2.65, 2.8), (2.35, 2.65, 2.8)])
for pos, size in [((0, 2.61, .055), (4.65, .035, .11)), ((-2.31, 0, .055), (.035, 5.2, .11)), ((2.31, 0, .055), (.035, 5.2, .11))]:
    box("Skirting", pos, size, ivory, .003)

# Walnut kitchenette, raised-panel doors, oven, stone counter and tiled splashback.
for cx in [-1.82, -1.24, -.66, -.08]:
    box("Kitchen base cabinet", (cx, 2.22, .45), (.57, .66, .88), wood)
    box("Base door inset", (cx, 1.875, .45), (.44, .035, .65), wood, .006)
    rod("Cabinet pull", (cx + .18, 1.84, .4), (cx + .18, 1.84, .57), .008, brass)
    box("Upper cabinet", (cx, 2.39, 1.94), (.57, .42, .78), wood)
    box("Upper door inset", (cx, 2.165, 1.94), (.44, .025, .62), wood, .005)
    rod("Upper pull", (cx + .18, 2.135, 1.72), (cx + .18, 2.135, 1.9), .008, brass)
box("Stone countertop", (-.95, 2.2, .92), (2.45, .72, .055), stone)
for row in range(5):
    for col in range(12):
        box("Backsplash brick", (-2.08 + col * .205, 2.615, 1.01 + row * .115), (.199, .025, .106), brick[(row + col) % 4], .002)
box("Oven fascia", (-1.25, 1.854, .46), (.52, .025, .68), chrome, .008)
box("Oven window", (-1.25, 1.833, .42), (.44, .012, .46), screen)
rod("Oven handle", (-1.47, 1.8, .72), (-1.03, 1.8, .72), .013, chrome)
box("Gas hob", (-1.25, 2.16, .96), (.53, .48, .025), dark)
for xx in [-1.39, -1.11]:
    for yy in [2.02, 2.28]:
        bpy.ops.mesh.primitive_torus_add(major_radius=.065, minor_radius=.008, major_segments=24, minor_segments=8, location=(xx, yy, .99))
        bpy.context.object.data.materials.append(chrome)
        for axis in [-1, 1]:
            rod("Burner support", (xx - .075, yy + axis * .055, 1), (xx + .075, yy + axis * .055, 1), .006, dark)
box("Sink rim", (-.29, 2.2, .955), (.48, .43, .018), chrome)
box("Sink basin", (-.29, 2.19, .966), (.39, .33, .012), screen, .035)
cable("Swan neck tap", [(-.28, 2.43, .96), (-.28, 2.43, 1.22), (-.28, 2.3, 1.26), (-.28, 2.26, 1.17)], .015, chrome)
box("Under cabinet light", (-.95, 2.28, 1.54), (2.2, .025, .018), ivory, .002)
box("Tall walnut cupboard", (-2.01, 1.59, 1.14), (.55, .62, 2.26), wood)
rod("Tall cupboard pull", (-1.78, 1.265, 1.02), (-1.78, 1.265, 1.25), .009, brass)
for row in range(11):
    for col in range(3):
        box("Breakfast partition brick", (.68, 1.22 + col * .215, .055 + row * .11), (.14, .209, .103), brick[(row + col) % 4], .003)
box("Breakfast bar top", (.68, 1.43, 1.29), (.27, .91, .065), wood)

# Sage daybed, soft upholstery, blanket folds and the glass-block feature.
box("Daybed frame", (1.69, .18, .19), (1.1, 2.15, .25), green, .05)
box("Daybed mattress", (1.66, .18, .41), (1.06, 2.1, .25), green, .1)
box("Daybed backrest", (2.18, .18, .65), (.18, 2.12, .62), green, .06)
for yy in [-.78, 1.12]:
    box("Daybed arm", (1.68, yy, .61), (1.05, .17, .49), green, .07)
for yy in [-.35, .36, .91]:
    cushion = box("Loose linen cushion", (1.93, yy, .74), (.29, .55, .3), green, .09)
    cushion.rotation_euler.y = -.24
for xx in [1.26, 2.08]:
    for yy in [-.66, 1]:
        rod("Daybed timber foot", (xx, yy, .01), (xx, yy, .2), .035, wood)
sill = box("Glass block sill", (2.26, .08, .94), (.2, 2.9, .07), stone)
sill["cutaway_normal"] = [-1, 0, 0]
sill["cutaway_origin"] = [2.315, 0, 0]
for row in range(7):
    for col in range(12):
        block = box("Glass block", (2.315, -1.24 + col * .24, 1.1 + row * .232), (.07, .225, .219), glass, .018)
        box("Glass ripple", (2.268, -1.24 + col * .24, 1.1 + row * .232), (.008, .16, .15), glass, .018)
box("Laptop on daybed", (1.56, .12, .553), (.33, .24, .015), chrome, .008)

# Linen-covered desk opposite the daybed, monitor, keyboard and chairs.
box("Desk top", (-.72, -.63, .78), (1.47, .78, .045), ivory)
box("Linen table cover", (-.72, -.63, .81), (1.49, .8, .015), linen, .006)
box("Linen hanging edge", (-.72, -1.025, .66), (1.49, .018, .3), linen, .01)
for xx in [-1.32, -.12]:
    for yy in [-.94, -.32]:
        rod("Desk leg", (xx, yy, .025), (xx, yy, .77), .025, dark)
box("Monitor foot", (-.7, -.38, .84), (.34, .2, .025), dark)
rod("Monitor pedestal", (-.7, -.36, .85), (-.7, -.36, 1.08), .025, chrome)
box("Monitor frame", (-.7, -.34, 1.23), (.65, .045, .38), dark)
box("Monitor glass", (-.7, -.367, 1.23), (.6, .008, .33), screen, .005)
box("Keyboard", (-.7, -.76, .835), (.42, .15, .025), ivory, .008)
for row in range(4):
    for col in range(13):
        box("Keyboard key", (-.89 + col * .031, -.81 + row * .031, .852), (.025, .025, .006), ivory, .001)


def chair(cx, cy, angle=0):
    # Rotating the whole collection preserves the chair's upholstery and fine legs.
    before = set(bpy.data.objects)
    box("Ivory chair seat", (cx, cy, .46), (.43, .43, .07), ivory, .03)
    box("Upholstered chair back", (cx, cy - .2, .73), (.43, .06, .53), ivory, .025)
    for dx in [-.17, .17]:
        for dy in [-.16, .16]:
            rod("Chair leg", (cx + dx * 1.1, cy + dy * 1.1, .02), (cx + dx, cy + dy, .45), .015, chrome)
    for obj in set(bpy.data.objects) - before:
        point = obj.location - Vector((cx, cy, 0))
        obj.location.x = cx + point.x * math.cos(angle) - point.y * math.sin(angle)
        obj.location.y = cy + point.x * math.sin(angle) + point.y * math.cos(angle)
        obj.rotation_euler.z += angle


chair(-.6, -1.37, -.12)
chair(.3, -.6, math.pi / 2)

# Compact storage with books and a microwave. No private labels or photo textures.
for zz in [.12, .85, 1.44, 2.05]:
    box("Storage shelf", (-1.99, -.64, zz), (.64, 1.35, .045), ivory)
for yy in [-1.34, .06]:
    box("Storage side", (-2.03, yy, 1.09), (.61, .035, 2.02), ivory)
box("Lower storage doors", (-1.69, -.64, .48), (.035, 1.35, .68), ivory)
for i in range(9):
    box("Books", (-2.03, -.99 + i * .09, 1.67), (.32, .065, .39 - .012 * (i % 3)), [linen, blue, wood][i % 3], .003)
box("Microwave", (-1.98, -.57, 1.05), (.52, .7, .32), chrome)
box("Microwave door", (-1.704, -.62, 1.05), (.018, .49, .23), screen)
rod("Microwave handle", (-1.675, -.27, .97), (-1.675, -.27, 1.13), .01, chrome)
box("Storage electronics", (-1.95, -.93, .895), (.23, .22, .045), dark)
box("Air conditioner", (-2.23, -.65, 2.48), (.18, .95, .28), ivory, .03)
box("Air conditioner vent", (-2.127, -.65, 2.38), (.012, .82, .035), dark, .002)

# Entry, freestanding coat rack, side table and intentional demo hazards.
door = box("Entry door", (.99, -2.59, 1.1), (.88, .06, 2.18), wood)
handle = rod("Door handle", (1.23, -2.54, 1), (1.38, -2.54, 1), .012, brass)
for obj in [door, handle]:
    obj["cutaway_normal"] = [0, 0, -1]
    obj["cutaway_origin"] = [0, 0, 2.59]
rod("Coat rack stem", (1.71, -1.98, .04), (1.71, -1.98, 1.78), .019, ivory)
for angle in [0, 2.1, 4.2]:
    rod("Coat rack foot", (1.71, -1.98, .16), (1.71 + .32 * math.cos(angle), -1.98 + .32 * math.sin(angle), .02), .015, ivory)
    rod("Coat rack hook", (1.71, -1.98, 1.55), (1.71 + .22 * math.cos(angle), -1.98 + .22 * math.sin(angle), 1.75), .014, ivory)
box("Hanging navy coat", (1.71, -1.98, 1.1), (.39, .2, .94), dark, .08)
box("White side table top", (1.22, -1.31, .53), (.56, .56, .045), ivory)
for xx in [.99, 1.45]:
    for yy in [-1.54, -1.08]:
        box("Side table leg", (xx, yy, .26), (.04, .04, .51), ivory, .005)
box("Side table speaker", (1.23, -1.31, .7), (.14, .1, .3), dark, .025)
rod("Floor lamp", (2.12, .97, .02), (2.12, .97, 1.85), .014, chrome)
box("Floor lamp shade", (2.06, .92, 2.01), (.31, .26, .35), linen, .015)
cable("Cable crossing walkway", [(-.65, -.35, .86), (-.38, -.85, .4), (-.13, -1.18, .025), (.25, -1.65, .024), (.7, -1.87, .024), (.82, -1.35, .025), (.58, -.83, .025), (.3, -.65, .04)], .008)
cable("Sofa side cable", [(1.23, -1.3, .57), (1.36, -1.49, .4), (1.65, -1.47, .03), (1.72, -1.08, .024), (1.53, -.94, .024)], .006)
cable("Storage trailing cable", [(-1.7, -.97, 1.04), (-1.54, -.8, .83), (-1.48, -.56, .06), (-1.28, -.28, .024), (-1.55, -.16, .024)], .006)
# Rumpled white cloth is modeled explicitly rather than painted into a pin.
vertices, faces = [], []
for row in range(15):
    for col in range(15):
        xx, yy = col / 14, row / 14
        vertices.append((.84 + xx * .45, -1.36 + yy * .43,
                         .026 + .025 * math.sin(xx * 18 + yy * 5) ** 2 + .022 * math.sin(yy * 21)))
for row in range(14):
    for col in range(14):
        a = row * 15 + col
        faces.append((a, a + 1, a + 16, a + 15))
mesh = bpy.data.meshes.new("Rumpled cotton")
mesh.from_pydata(vertices, [], faces)
cloth = bpy.data.objects.new("Cloth on floor", mesh)
bpy.context.collection.objects.link(cloth)
cloth.data.materials.append(clothing)
for poly in mesh.polygons:
    poly.use_smooth = True
for xx, yy, angle in [(1.12, -1.79, .2), (1.33, -1.94, -.3)]:
    shoe = box("Slipper", (xx, yy, .045), (.13, .27, .065), blue, .035)
    shoe.rotation_euler.z = angle

# Apply curves and merge furniture by material to keep the interactive asset small.
for obj in list(bpy.context.scene.objects):
    if obj.type == "CURVE":
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.convert(target="MESH")
groups = {}
for obj in list(bpy.context.scene.objects):
    if obj.type == "MESH" and not obj.get("cutaway_wall"):
        groups.setdefault((obj.data.materials[0].name, tuple(obj.get("cutaway_normal", []))), []).append(obj)
for (name, _), objects in groups.items():
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    objects[0].name = name
    if name == "Textured glass block":
        objects[0]["cutaway_normal"] = [-1, 0, 0]
        objects[0]["cutaway_origin"] = [2.315, 0, 0]
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.gltf(filepath=str(args.output), export_format="GLB", export_extras=True,
                         export_cameras=False, export_lights=False, export_yup=True)
print(f"Illustrative studio exported: {args.output} ({args.output.stat().st_size} bytes)")
