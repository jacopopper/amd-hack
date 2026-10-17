"""Blender-side MPFB2 entry point; receives only a reviewed, minimized profile."""
import importlib
import importlib.util
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

PREVIEW_SIZE = (640, 960)


def load_style():
    spec = importlib.util.spec_from_file_location("step_wise_avatar_style", Path(__file__).with_name("avatar_style.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mpfb_symbol(module_suffix, symbol):
    for loaded in tuple(sys.modules):
        if loaded.endswith(module_suffix):
            module = importlib.import_module(loaded)
            if hasattr(module, symbol):
                return getattr(module, symbol)
    raise RuntimeError("MPFB2 is not enabled in this Blender profile")


def linear(hex_color):
    channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    return tuple(c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels) + (1.0,)


def simple_material(name, hex_color, roughness=.82, metallic=0.0):
    material = bpy.data.materials.new(name)
    material.diffuse_color = linear(hex_color)
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = material.diffuse_color
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    return material


def evaluated_corners(item):
    evaluated = item.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return [item.matrix_world @ Vector(corner) for corner in evaluated.bound_box]


def bounds(items):
    corners = [corner for item in items if item.type == "MESH" for corner in evaluated_corners(item)]
    return (Vector((min(c[i] for c in corners) for i in range(3))), Vector((max(c[i] for c in corners) for i in range(3))))


def add_asset(HumanService, AssetService, human, category, filename, asset_type):
    path = AssetService.find_asset_absolute_path(filename, asset_subdir=category)
    if path is None:
        raise RuntimeError("The pinned MakeHuman system assets pack is missing a required avatar asset")
    HumanService.add_mhclo_asset(path, human, asset_type=asset_type)


def face_islands(bm):
    seen, islands = set(), []
    for face in bm.faces:
        if face.index in seen:
            continue
        stack, island = [face], []
        seen.add(face.index)
        while stack:
            current = stack.pop()
            island.append(current)
            for edge in current.edges:
                for neighbour in edge.link_faces:
                    if neighbour.index not in seen:
                        seen.add(neighbour.index)
                        stack.append(neighbour)
        islands.append(island)
    return islands


def split_by_height(item, upper, lower, threshold_z, lower_flare=1.0):
    """Two-tone garments: whole shells are classified by height when the asset has several, faces otherwise.

    lower_flare widens the lower shell horizontally; the pack's skirt is cut for a neutral body and lets thighs
    poke through once the figure is aged, which flat colours (no alpha textures) would expose as skin-coloured slits.
    """
    mesh = item.data
    mesh.materials.clear()
    mesh.materials.append(upper)
    mesh.materials.append(lower)
    matrix = item.matrix_world
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    height = lambda face: (matrix @ face.calc_center_median()).z
    islands = face_islands(bm)
    groups = islands if len(islands) > 1 else [[face] for face in bm.faces]
    for group in groups:
        index = 0 if sum(height(face) for face in group) / len(group) > threshold_z else 1
        for face in group:
            face.material_index = index
    if lower_flare != 1.0 and len(islands) > 1:
        verts = {vert for face in bm.faces if face.material_index == 1 for vert in face.verts}
        if verts:
            cx = sum(v.co.x for v in verts) / len(verts)
            cy = sum(v.co.y for v in verts) / len(verts)
            for vert in verts:
                vert.co.x = cx + (vert.co.x - cx) * lower_flare
                vert.co.y = cy + (vert.co.y - cy) * lower_flare
    bm.to_mesh(mesh)
    bm.free()


def shrink_images(limit=512):
    for image in bpy.data.images:
        if image.type == "IMAGE" and image.size[0] and max(image.size) > limit:
            scale = limit / max(image.size)
            image.scale(max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale)))


def paint(items, style, body_low, body_height):
    palette = style["palette"]
    skin = simple_material("Avatar skin", palette["skin"], .6)
    hair = simple_material("Avatar hair", palette["hair"], .65)
    top = simple_material("Avatar garment top", palette["top"])
    bottom = simple_material("Avatar garment bottom", palette["bottom"])
    shoe = simple_material("Avatar shoes", palette["shoes"], .55)
    sock = simple_material("Avatar socks", palette["sock"])
    # Mouth interior is never visible; its photographic textures were most of the GLB's weight.
    mouth = {"teeth_base": simple_material("Avatar teeth", palette["teeth"], .4),
             "tongue01": simple_material("Avatar tongue", palette["tongue"], .5)}
    for item in items:
        if item.type != "MESH":
            continue
        flat_mouth = next((material for stem, material in mouth.items() if stem in item.name), None)
        if flat_mouth:
            item.data.materials.clear()
            item.data.materials.append(flat_mouth)
        elif item.name.startswith("Human_export"):
            item.data.materials.clear()
            item.data.materials.append(skin)
        elif style["hair"] in item.name:
            item.data.materials.clear()
            item.data.materials.append(hair)
        elif style["garment"] in item.name:
            split_by_height(item, top, bottom, body_low + body_height * .53, style["lower_flare"])
        elif style["shoes"] in item.name:
            split_by_height(item, sock, shoe, body_low + body_height * .055)


def aim(armature, name, direction):
    """Rotate one pose bone about its head so it points along direction (world axes)."""
    bone = armature.pose.bones[name]
    head = bone.head.copy()
    wanted = (armature.matrix_world.to_3x3().inverted() @ Vector(direction)).normalized()
    turn = (bone.tail - bone.head).normalized().rotation_difference(wanted).to_matrix().to_4x4()
    bone.matrix = Matrix.Translation(head) @ turn @ Matrix.Translation(-head) @ bone.matrix
    bpy.context.view_layer.update()


def spin(armature, name, axis, degrees):
    bone = armature.pose.bones[name]
    head = bone.head.copy()
    local_axis = armature.matrix_world.to_3x3().inverted() @ Vector(axis)
    turn = Matrix.Rotation(math.radians(degrees), 4, local_axis.normalized())
    bone.matrix = Matrix.Translation(head) @ turn @ Matrix.Translation(-head) @ bone.matrix
    bpy.context.view_layer.update()


def pose_arms(armature, holding):
    """Relaxed arms; with a walker the forearms reach forward and the fingers close on the handles."""
    knuckles = []
    for side, suffix in ((1, ".L"), (-1, ".R")):
        if holding:
            aim(armature, "upperarm01" + suffix, (.14 * side, -.30, -.94))
            aim(armature, "lowerarm01" + suffix, (.04 * side, -.82, -.57))
            aim(armature, "wrist" + suffix, (0, -.95, -.30))
            spin(armature, "wrist" + suffix, (0, -.82, -.57), 70 * side)
            for finger in (2, 3, 4, 5):
                for phalanx in (1, 2, 3):
                    spin(armature, f"finger{finger}-{phalanx}{suffix}", (1, 0, 0), 38)
            head = armature.matrix_world @ armature.pose.bones["finger3-1" + suffix].head
            knuckles.append(head)
        else:
            aim(armature, "upperarm01" + suffix, (.10 * side, -.04, -.99))
            aim(armature, "lowerarm01" + suffix, (.05 * side, -.22, -.97))
            aim(armature, "wrist" + suffix, (.03 * side, -.10, -.99))
    return knuckles


def segment(name, start, end, radius, material):
    a, b = Vector(start), Vector(end)
    direction = b - a
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius, depth=direction.length, location=(a + b) / 2)
    item = bpy.context.object
    item.name = name
    item.rotation_mode = "QUATERNION"
    item.rotation_quaternion = direction.to_track_quat("Z", "Y")
    item.data.materials.append(material)
    return item


def joint(location, radius, material):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=10, radius=radius, location=location)
    item = bpy.context.object
    item.data.materials.append(material)
    return item


def add_walker(knuckles, palette):
    """Frame proportions follow the posed hands; they are schematic and claim no measurement."""
    metal = simple_material("Walker frame", palette["metal"], .38, .3)
    grip = simple_material("Walker grips", palette["grip"], .7)
    radius = .0125
    left, right = knuckles
    hand_z = (left.z + right.z) / 2 - .035
    rear_y = (left.y + right.y) / 2 + .02
    front_y = rear_y - .44
    tip = .045
    low = tip + (hand_z - tip) * .42
    parts = []
    for x in (left.x, right.x):
        parts += [segment("walker", (x, rear_y, tip), (x, rear_y, hand_z), radius, metal),
                  segment("walker", (x, front_y, tip), (x, front_y, hand_z), radius, metal),
                  segment("walker", (x, front_y, hand_z), (x, rear_y + .09, hand_z), radius, metal),
                  segment("walker", (x, front_y, low), (x, rear_y, low), radius * .8, metal),
                  segment("walker", (x, rear_y - .075, hand_z), (x, rear_y + .075, hand_z), radius * 1.55, grip),
                  segment("walker", (x, rear_y, 0), (x, rear_y, tip), radius * 1.3, grip),
                  segment("walker", (x, front_y, 0), (x, front_y, tip), radius * 1.3, grip)]
        parts += [joint((x, front_y, hand_z), radius * 1.1, metal), joint((x, rear_y + .09, hand_z), radius * 1.1, metal)]
    for z in (hand_z - .02, low + .12):
        parts.append(segment("walker", (left.x, front_y, z), (right.x, front_y, z), radius * .9, metal))
    bpy.ops.object.select_all(action="DESELECT")
    for item in parts:
        item.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    walker = bpy.context.object
    walker.name = "walker"
    bpy.ops.object.shade_smooth()
    return walker


def add_review_callout(items, callout_id, palette):
    low, high = bounds(items)
    height = max(high.z - low.z, 1.0)
    center_y = (low.y + high.y) / 2
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=max(height * .02, .02),
                                         location=(high.x + height * .06, center_y, high.z + height * .08))
    marker = bpy.context.object
    marker.name = callout_id
    marker["callout_id"] = callout_id
    marker.data.materials.append(simple_material("Review callout", palette["callout"]))
    return marker


def render_preview(path, items, hidden):
    """Transparent 3/4 portrait for UIs that should not need WebGL; callouts and helpers stay out of frame."""
    scene = bpy.context.scene
    for item in hidden:
        item.hide_render = True
    low, high = bounds(items)
    centre = (low + high) / 2
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = PREVIEW_SIZE
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    world = bpy.data.worlds.new("Avatar preview world")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs[0].default_value = (1, 1, 1, 1)
    background.inputs[1].default_value = .75
    scene.world = world
    camera_data = bpy.data.cameras.new("Avatar preview camera")
    camera_data.lens = 70
    camera = bpy.data.objects.new("Avatar preview camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    distance = (high.z - low.z) / 2 / math.tan(math.atan(18 / camera_data.lens)) * 1.24
    azimuth = math.radians(30)
    camera.location = centre + Vector((math.sin(azimuth), -math.cos(azimuth), 0)) * distance
    camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
    sun = bpy.data.objects.new("Avatar preview key", bpy.data.lights.new("Avatar preview key", "SUN"))
    sun.data.energy = 3.2
    sun.data.angle = math.radians(18)
    sun.rotation_euler = Vector((-.45, .6, -.7)).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(sun)
    bpy.ops.mesh.primitive_plane_add(size=8, location=(centre.x, centre.y, low.z))
    bpy.context.object.is_shadow_catcher = True
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    profile_path, output_path = args[:2]
    preview_path = args[2] if len(args) > 2 else None
    profile = json.loads(open(profile_path, encoding="utf-8").read())
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    style = load_style().select_style(profile.get("display_traits", []))
    HumanService = mpfb_symbol("mpfb.services.humanservice", "HumanService")
    TargetService = mpfb_symbol("mpfb.services.targetservice", "TargetService")
    AssetService = mpfb_symbol("mpfb.services.assetservice", "AssetService")
    ExportService = mpfb_symbol("mpfb.services.exportservice", "ExportService")
    ObjectService = mpfb_symbol("mpfb.services.objectservice", "ObjectService")
    MpfbVersion = mpfb_symbol("mpfb", "VERSION")
    print("STEP_WISE_MPFB_VERSION=" + ".".join(map(str, MpfbVersion)))
    if not AssetService.system_assets_pack_is_installed():
        raise RuntimeError("Install the pinned makehuman_system_assets pack in the isolated MPFB profile")
    macro = TargetService.get_default_macro_info_dict()
    macro["age"], macro["gender"] = style["age"], style["gender"]
    human = HumanService.create_human(macro_detail_dict=macro, feet_on_ground=True)
    HumanService.add_builtin_rig(human, "default")
    for category, stem, asset_type in style["assets"]:
        add_asset(HumanService, AssetService, human, category, stem + ".mhclo", asset_type)
    originals = [item for item in bpy.data.objects]
    export_root = ExportService.create_character_copy(human, name_suffix="_export")
    export_body = ObjectService.find_object_of_type_amongst_nearest_relatives(export_root, "Basemesh")
    if export_body is None:
        raise RuntimeError("MPFB could not create the isolated avatar export copy")
    ExportService.bake_modifiers_remove_helpers(export_body, bake_masks=True, bake_subdiv=True,
                                                remove_helpers=True, also_proxy=True)
    export_objects = [export_root, *ObjectService.get_list_of_children(export_root)]
    low, high = bounds(export_objects)
    paint(export_objects, style, low.z, high.z - low.z)
    shrink_images()
    knuckles = pose_arms(export_root, style["walker"])
    if style["walker"]:
        export_objects.append(add_walker(knuckles, style["palette"]))
    callouts = [add_review_callout(export_objects, callout["id"], style["palette"]) for callout in profile.get("callouts", [])]
    export_objects.extend(callouts)
    bpy.ops.object.select_all(action="DESELECT")
    for item in export_objects:
        if item and item.type in {"MESH", "ARMATURE"}:
            item.select_set(True)
    bpy.ops.export_scene.gltf(filepath=output_path, export_format="GLB", use_selection=True, export_extras=True,
                              export_rest_position_armature=False)
    if preview_path:
        shown = [item for item in export_objects if item not in callouts]
        render_preview(preview_path, shown, [*originals, *callouts])
        print("STEP_WISE_PREVIEW=ok")


main()
