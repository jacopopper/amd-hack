"""Blender-side MPFB2 entry point; receives only a reviewed, minimized profile."""
import importlib
import json
import math
import sys

import bpy
from mathutils import Vector


def mpfb_symbol(module_suffix, symbol):
    for loaded in tuple(sys.modules):
        if loaded.endswith(module_suffix):
            module = importlib.import_module(loaded)
            if hasattr(module, symbol):
                return getattr(module, symbol)
    raise RuntimeError("MPFB2 is not enabled in this Blender profile")


def segment(name, start, end, radius):
    a, b = Vector(start), Vector(end)
    direction = b - a
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=radius, depth=direction.length, location=(a + b) / 2)
    item = bpy.context.object
    item.name = name
    item.rotation_mode = "QUATERNION"
    item.rotation_quaternion = direction.to_track_quat("Z", "Y")
    return item


def add_walker(human):
    # Proportions are schematic and follow the character bounding box; they do not claim measurements.
    corners = [human.matrix_world @ Vector(corner) for corner in human.bound_box]
    low = min(v.z for v in corners)
    high = max(v.z for v in corners)
    center_x = (min(v.x for v in corners) + max(v.x for v in corners)) / 2
    height = max(high - low, 1.0)
    width, depth = height * .38, height * .30
    front_y = min(v.y for v in corners) - depth * .42
    handle_z = low + height * .57
    lower_z = low + height * .06
    left, right = center_x - width / 2, center_x + width / 2
    back_y, front_y = front_y + depth, front_y
    radius = height * .012
    created = []
    for x in (left, right):
        for y in (back_y, front_y):
            created.append(segment("walker_frame", (x, y, lower_z), (x, y, handle_z), radius))
    for z in (lower_z, handle_z):
        for y in (back_y, front_y):
            created.append(segment("walker_frame", (left, y, z), (right, y, z), radius))
    for x in (left, right):
        created.append(segment("walker_side", (x, back_y, handle_z), (x, front_y, handle_z), radius))
    material = simple_material("Walker frame", (0.08, 0.22, 0.28, 1.0))
    for item in created:
        item.data.materials.append(material)
    return created


def add_asset(HumanService, AssetService, human, category, filename, asset_type):
    path = AssetService.find_asset_absolute_path(filename, asset_subdir=category)
    if path is None:
        raise RuntimeError("The pinned MakeHuman system assets pack is missing a required avatar asset")
    HumanService.add_mhclo_asset(path, human, asset_type=asset_type)


def simple_material(name, color):
    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = material.diffuse_color
    shader.inputs["Roughness"].default_value = 0.82
    return material


def set_schematic_body_material(human):
    material = simple_material("Functional avatar - neutral clay", (0.52, 0.55, 0.59, 1.0))
    human.data.materials.clear()
    human.data.materials.append(material)


def add_review_callout(human, callout_id):
    corners = [human.matrix_world @ Vector(corner) for corner in human.bound_box]
    low, high = min(v.z for v in corners), max(v.z for v in corners)
    right = max(v.x for v in corners)
    center_y = (min(v.y for v in corners) + max(v.y for v in corners)) / 2
    height = max(high - low, 1.0)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8,
                                         radius=max(height * .02, .02),
                                         location=(right + height * .12, center_y, high + height * .08))
    marker = bpy.context.object
    marker.name = callout_id
    marker["callout_id"] = callout_id
    marker.data.materials.append(simple_material("Review callout", (1.0, 0.48, 0.08, 1.0)))
    return marker


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    profile_path, output_path = args
    profile = json.loads(open(profile_path, encoding="utf-8").read())
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    traits = profile.get("display_traits", [])
    current = {fact["field"]: fact["value"] for fact in traits if fact.get("status") == "current"}
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
    # Only explicit, reviewed fields affect the figure. Missing traits stay at neutral defaults.
    if current.get("age_group") in {"older_adult", "anziano", "anziana"}:
        macro["age"] = .85
    human = HumanService.create_human(macro_detail_dict=macro, feet_on_ground=True)
    HumanService.add_builtin_rig(human, "default")
    set_schematic_body_material(human)
    for category, filename, asset_type in (
        ("eyes", "low-poly.mhclo", "Eyes"), ("eyebrows", "eyebrow001.mhclo", "Eyebrows"),
        ("eyelashes", "eyelashes01.mhclo", "Eyelashes"), ("tongue", "tongue01.mhclo", "Tongue"),
        ("teeth", "teeth_base.mhclo", "Teeth"), ("hair", "short01.mhclo", "Hair"),
        ("clothes", "male_casualsuit01.mhclo", "Clothes"), ("clothes", "shoes01.mhclo", "Clothes"),
    ):
        add_asset(HumanService, AssetService, human, category, filename, asset_type)
    export_root = ExportService.create_character_copy(human, name_suffix="_export")
    export_body = ObjectService.find_object_of_type_amongst_nearest_relatives(export_root, "Basemesh")
    if export_body is None:
        raise RuntimeError("MPFB could not create the isolated avatar export copy")
    ExportService.bake_modifiers_remove_helpers(export_body, bake_masks=True, bake_subdiv=True,
                                                remove_helpers=True, also_proxy=True)
    export_objects = [export_root, *ObjectService.get_list_of_children(export_root)]
    aid = current.get("mobility_aid")
    if isinstance(aid, str) and aid.casefold().strip() in {"walker", "rollator", "deambulatore"}:
        export_objects.extend(add_walker(export_body))
    for callout in profile.get("callouts", []):
        export_objects.append(add_review_callout(export_body, callout["id"]))
    bpy.ops.object.select_all(action="DESELECT")
    for item in export_objects:
        if item and item.type in {"MESH", "ARMATURE"}:
            item.select_set(True)
    bpy.ops.export_scene.gltf(filepath=output_path, export_format="GLB", use_selection=True, export_extras=True)


main()
