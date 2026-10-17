"""Map raw room coordinates into calibrated component coordinates in meters."""
import math

import numpy as np


def metric_scale(scene):
    calibration = scene.get("calibration") or {}
    scale = scene.get("scale_factor")
    if (scene.get("status") != "ready" or scene.get("provenance") not in {"live", "replay"}
            or scene.get("raw_coordinate_units") != "relative" or scene.get("scale_state") != "metric_calibrated"
            or not calibration.get("measurement_id") or isinstance(scale, bool)
            or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0):
        return None
    calibrated_scale = calibration.get("scale_factor_m_per_unit")
    if (isinstance(calibrated_scale, bool) or not isinstance(calibrated_scale, (int, float))
            or not math.isclose(scale, calibrated_scale, rel_tol=1e-9)):
        return None
    return float(scale)


def bind_metric_link(scenes, from_scene_id, to_scene_id, value):
    """A reviewer confirms the rigid transform after both raw scenes are scaled."""
    bound = {**value, "registration_version": 2}
    for endpoint, scene_id in (("from", from_scene_id), ("to", to_scene_id)):
        scene = scenes[scene_id]
        scale = metric_scale(scene)
        if scale is None:
            raise ValueError("Room links require ready scenes with validated metric calibrations")
        bound[f"{endpoint}_calibration_id"] = scene["calibration"]["measurement_id"]
        bound[f"{endpoint}_scale_factor_m_per_unit"] = scale
    return bound


def link_state(link, scenes):
    if link["from_scene_id"] not in scenes or link["to_scene_id"] not in scenes:
        return "inactive_room"
    if (link.get("registration_version") != 2 or link.get("coordinate_space") != "metric_scene_coordinates"
            or link.get("residual_unit") != "m"):
        return "requires_metric_review"  # Never reinterpret legacy relative translations as meters.
    for endpoint in ("from", "to"):
        scene = scenes[link[f"{endpoint}_scene_id"]]
        scale = metric_scale(scene)
        if scale is None:
            return "scale_unavailable"
        if (link.get(f"{endpoint}_calibration_id") != scene["calibration"]["measurement_id"]
                or link.get(f"{endpoint}_scale_factor_m_per_unit") != scale):
            return "calibration_changed"
    return "ready"


def compose_rooms(scenes, links):
    adjacency = {scene_id: [] for scene_id in scenes}
    for link in links:
        if link_state(link, scenes) != "ready":
            continue
        a, b = link["from_scene_id"], link["to_scene_id"]
        transform = np.asarray(link["transform_from_to"], dtype=float)
        adjacency[a].append((b, np.linalg.inv(transform)))
        adjacency[b].append((a, transform))
    components, placements, visited = [], [], set()
    for reference in scenes:
        if reference in visited:
            continue
        transforms = {reference: np.eye(4)}
        queue = [reference]
        for scene_id in queue:
            for neighbor, neighbor_to_scene in adjacency[scene_id]:
                neighbor_to_reference = transforms[scene_id] @ neighbor_to_scene
                if neighbor in transforms:
                    if not np.allclose(transforms[neighbor], neighbor_to_reference, atol=1e-4, rtol=1e-4):
                        raise ValueError("Room links form an inconsistent transform cycle")
                else:
                    transforms[neighbor] = neighbor_to_reference
                    queue.append(neighbor)
        components.append(queue)
        visited.update(queue)
        for scene_id in queue:
            scale = metric_scale(scenes[scene_id])
            raw_to_metric = np.diag([scale, scale, scale, 1.]) if scale is not None else np.eye(4)
            placements.append({"scene_id": scene_id, "reference_scene_id": reference,
                               "coordinate_space": "component_metric_coordinates" if scale is not None else "raw_scene_relative",
                               "coordinate_units": "m" if scale is not None else "relative",
                               "scale_factor_m_per_unit": scale,
                               "transform_to_reference": (transforms[scene_id] @ raw_to_metric).tolist()})
    return components, placements
