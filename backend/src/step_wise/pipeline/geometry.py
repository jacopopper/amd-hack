import numpy as np
from step_wise import db


def box_overlap(a, b):
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / (area(a) + area(b) - intersection)


def unproject(pixels, depth, intrinsics, world_to_camera):
    """Camera z depth, column vectors, world-to-camera [R|t]."""
    pixels, depth = np.asarray(pixels, float), np.asarray(depth, float)
    k, extrinsic = np.asarray(intrinsics, float), np.asarray(world_to_camera, float)
    if k.shape != (3, 3) or extrinsic.shape != (3, 4) or pixels.ndim != 2 or pixels.shape[1] != 2 or depth.shape != (len(pixels),):
        raise ValueError("Invalid camera/depth dimensions")
    if not all(np.isfinite(v).all() for v in (pixels, depth, k, extrinsic)) or np.any(depth <= 0):
        raise ValueError("Invalid camera/depth values")
    rotation = extrinsic[:, :3]
    if not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-4) or not np.isclose(np.linalg.det(rotation), 1, atol=1e-4):
        raise ValueError("Invalid camera rotation")
    camera = (np.linalg.inv(k) @ np.column_stack([pixels, np.ones(len(pixels))]).T).T * depth[:, None]
    return (camera - extrinsic[:, 3]) @ rotation


def supported_anchor(points, confidence, min_points=20, camera_center=None):
    """Abstain when a foreground region has insufficient support or mixed depths."""
    points, confidence = np.asarray(points, float), np.asarray(confidence, float)
    selected = points[np.isfinite(points).all(axis=1) & np.isfinite(confidence) & (confidence >= .8)]
    if len(selected) < min_points:
        return None
    center = np.median(selected, axis=0)
    distances = np.linalg.norm(selected - center, axis=1)
    origin = np.zeros(3) if camera_center is None else np.asarray(camera_center)
    if np.quantile(distances, .9) > .2 * max(np.linalg.norm(center - origin), 1e-6):
        return None
    return center.tolist()


def foreground_anchor(points, confidence, background, background_confidence, camera_center):
    """A box alone cannot distinguish a thin feature from its supporting surface."""
    center = supported_anchor(points, confidence, camera_center=camera_center)
    background = np.asarray(background, float)
    selected = background[np.isfinite(background).all(axis=1) & (np.asarray(background_confidence) >= .8)]
    if center is None or len(selected) < 20:
        return None
    selected = selected[np.linspace(0, len(selected) - 1, min(1024, len(selected)), dtype=int)]
    background_center = np.median(selected, axis=0)
    _, singular, directions = np.linalg.svd(selected - background_center, full_matrices=False)
    if singular[1] <= 1e-8:
        return None
    normal = directions[-1]
    residual = np.quantile(np.abs((selected - background_center) @ normal), .9)
    distance = np.linalg.norm(np.asarray(center) - camera_center)
    separation = abs(np.dot(np.asarray(center) - background_center, normal))
    if residual > .02 * distance or separation <= max(.03 * distance, 3 * residual):
        return None
    return center


def orient_plane(normal, offset, up):
    if np.dot(normal, up) < 0:
        return -normal, -offset
    return normal, offset


def estimate_floor(world_points, confidence, world_to_camera, confidence_threshold=.5):
    """Conservative floor estimate in raw relative coordinates; never changes display orientation."""
    points = np.asarray(world_points, dtype=float).reshape(-1, 3)
    confidence = np.asarray(confidence, dtype=float).reshape(-1)
    cameras = np.asarray(world_to_camera, dtype=float)
    unknown = {"floor_state": "unknown", "gravity_state": "unknown", "floor_plane": None}
    if len(points) != len(confidence) or cameras.ndim != 3 or cameras.shape[1:] != (3, 4) or len(cameras) < 2:
        return unknown
    valid = np.isfinite(points).all(axis=1) & np.isfinite(confidence) & (confidence >= confidence_threshold)
    points = points[valid]
    if len(points) < 300 or not np.isfinite(cameras).all():
        return unknown

    rotations = cameras[:, :, :3]
    camera_up = np.einsum("nji,j->ni", rotations, np.array([0., -1., 0.]))
    lengths = np.linalg.norm(camera_up, axis=1)
    camera_up = camera_up[lengths > 1e-6] / lengths[lengths > 1e-6, None]
    if len(camera_up) < 2:
        return unknown
    up_mean = camera_up.mean(axis=0)
    coherence = float(np.linalg.norm(up_mean))
    if coherence < .90:
        return unknown
    up = up_mean / np.linalg.norm(up_mean)
    coherent_views = np.dot(camera_up, up) >= np.cos(np.deg2rad(25))
    if np.count_nonzero(coherent_views) < max(2, int(np.ceil(.8 * len(camera_up)))):
        return unknown

    low, high = np.quantile(points, [.005, .995], axis=0)
    diagonal = float(np.linalg.norm(high - low))
    if not np.isfinite(diagonal) or diagonal <= 1e-6:
        return unknown
    # A fixed, evenly spaced sample makes plane search bounded and reproducible.
    sample = points[np.linspace(0, len(points) - 1, min(4000, len(points)), dtype=int)]
    tolerance = max(.004 * diagonal, 1e-5)
    min_support = max(100, int(np.ceil(.025 * len(sample))))
    vertical = points @ up
    low_height, high_height = np.quantile(vertical, [.10, .95])
    vertical_span = max(float(high_height - low_height), 1e-6)
    if vertical_span < .12 * diagonal:
        return unknown  # A lone horizontal surface could be a table, not a floor.
    lower_limit = float(low_height + .12 * vertical_span)
    camera_centers = -np.einsum("nji,nj->ni", rotations, cameras[:, :, 3])
    camera_heights = camera_centers @ up
    rng = np.random.default_rng(0)
    best = None
    cosine_limit = np.cos(np.deg2rad(18))
    for _ in range(256):
        indices = rng.choice(len(sample), 3, replace=False)
        a, b, c = sample[indices]
        normal = np.cross(b - a, c - a)
        norm = float(np.linalg.norm(normal))
        if norm <= 1e-8 * diagonal * diagonal:
            continue
        normal /= norm
        normal, offset = orient_plane(normal, -float(np.dot(normal, a)), up)
        if np.dot(normal, up) < cosine_limit:
            continue
        distances = np.abs(sample @ normal + offset)
        inliers = distances <= tolerance
        support = int(np.count_nonzero(inliers))
        if support < min_support:
            continue
        plane_height = -offset / max(float(np.dot(normal, up)), 1e-8)
        if plane_height > lower_limit or np.mean(camera_heights > plane_height + .03 * diagonal) < .8:
            continue
        plane_points = sample[inliers]
        relative = plane_points - np.outer(plane_points @ normal + offset, normal)
        # Reject a narrow shelf or an accidental line of points: a floor should span area.
        axis_a = np.cross(normal, np.array([1., 0., 0.]))
        if np.linalg.norm(axis_a) < .1:
            axis_a = np.cross(normal, np.array([0., 0., 1.]))
        axis_a /= np.linalg.norm(axis_a)
        axis_b = np.cross(normal, axis_a)
        uv = np.column_stack((relative @ axis_a, relative @ axis_b))
        spans = np.quantile(uv, .95, axis=0) - np.quantile(uv, .05, axis=0)
        if np.min(spans) < .18 * diagonal:
            continue
        grid = np.floor((uv - uv.min(axis=0)) / np.maximum(spans, 1e-8) * 4).clip(0, 3).astype(int)
        if len(np.unique(grid[:, 0] * 4 + grid[:, 1])) < 5:
            continue
        score = support
        if best is None or score > best[0]:
            residual = distances[inliers]
            best = (score, normal.copy(), offset, plane_height, float(np.quantile(residual, .95)),
                    spans.tolist(), camera_heights.tolist())
    if best is None:
        return unknown
    support, normal, offset, height, residual, spans, camera_heights = best
    return {"floor_state": "estimated_relative", "gravity_state": "estimated_from_consistent_camera_up",
            "gravity_up": up.tolist(),
            "floor_plane": {"normal": normal.tolist(), "offset": offset, "height_along_gravity": height,
                            "support_points": support, "sample_points": len(sample), "residual_p95": residual,
                            "span": spans, "camera_heights": camera_heights,
                            "units": "raw_relative_scene_units"}}


def geometry(context):
    scenes = context["outputs"]["reconstruct"]["scenes"]
    anchors, abstentions, evidence_markers = [], [], []
    for scene in scenes:
        scene.setdefault("floor_state", "unknown")
        scene.setdefault("gravity_state", "unknown")
        scene.setdefault("floor_plane", None)
        if scene["status"] != "ready" or scene["provenance"] not in {"live", "replay"}:
            continue
        try:
            path = db.asset_path(context["settings"], scene["analysis_path"])
            if db.digest(path.read_bytes()) != scene["analysis_sha256"]:
                raise ValueError("Reconstruction analysis checksum mismatch")
            with np.load(path, allow_pickle=False) as arrays:
                points, depth, confidence = arrays["world_points"], arrays["depth"][..., 0], arrays["confidence"]
                intrinsics, cameras = arrays["intrinsics"], arrays["extrinsics"]
        except (OSError, ValueError, KeyError):
            scene.update({"status": "failed", "floor_state": "unknown", "gravity_state": "unknown", "floor_plane": None,
                          "reason": "Room analysis artifact failed integrity checks; photo evidence remains available."})
            for key in ("analysis_path", "ply_path"):
                scene.pop(key, None)
            continue
        observed = np.zeros_like(confidence, dtype=bool)
        for image in scene["images"]:
            l, t, r, b = image["transform"]["content_box"]
            observed[image["tensor_index"], t:b, l:r] = True
        # The padded model input is not room evidence, even when its depth is confident.
        floor_confidence = np.where(observed & (depth > 0), confidence, -np.inf)
        floor = estimate_floor(points, floor_confidence, cameras, scene["confidence_threshold"]) if scene.get("reconstruction_kind") == "multiview" else {
            "floor_state": "unknown", "gravity_state": "unknown", "floor_plane": None}
        scene.update(floor)
        by_photo = {p["photo_id"]: p for p in scene["images"]}
        proposals = [c for c in context["outputs"]["jev"]["candidates"]
                     if c["presence"] == "present" and c["evidence"] and c["evidence"].get("box")]
        proposals.extend({"photo_id": evidence["photo_id"], "category": hazard["category"], "presence": "present", "evidence": evidence}
                         for hazard in context["outputs"]["gemma"]["hazards"] for evidence in hazard["evidence"] if evidence.get("box"))
        for candidate in proposals:
            if candidate["photo_id"] not in by_photo:
                continue
            image = by_photo[candidate["photo_id"]]
            index = image["tensor_index"]
            transform = image["transform"]
            sx, sy = transform["scale_xy"]
            ox, oy = transform["offset_xy"]
            l, t, r, b = candidate["evidence"]["box"]
            l, t, r, b = l * sx + ox, t * sy + oy, r * sx + ox, b * sy + oy
            full_box = (l, t, r, b)
            # A substantial interior region, never a single box-center depth sample.
            dx, dy = (r - l) * .2, (b - t) * .2
            l, t, r, b = max(0, int(l + dx)), max(0, int(t + dy)), min(518, int(r - dx)), min(518, int(b - dy))
            region = points[index, t:b, l:r].reshape(-1, 3)
            support = (confidence[index, t:b, l:r] >= scene["confidence_threshold"]).ravel().astype(float)
            bl, bt, br, bb = full_box
            margin = max(br - bl, bb - bt) * .15
            cl, ct, cr, cb = transform["content_box"]
            x1, y1, x2, y2 = max(cl, int(bl - margin)), max(ct, int(bt - margin)), min(cr, int(br + margin)), min(cb, int(bb + margin))
            yy, xx = np.mgrid[y1:y2, x1:x2]
            ring = (xx < bl) | (xx >= br) | (yy < bt) | (yy >= bb)
            background = points[index, y1:y2, x1:x2][ring]
            background_support = (confidence[index, y1:y2, x1:x2][ring] >= scene["confidence_threshold"]).astype(float)
            camera_center = -cameras[index, :, :3].T @ cameras[index, :, 3]
            # A photo-surface pointer is only a navigation aid, not a validated
            # object anchor. Thin floor hazards often cannot pass foreground tests.
            surface = supported_anchor(region, support, camera_center=camera_center) if len(region) else None
            if surface is not None:
                evidence_markers.append({
                    "photo_id": candidate["photo_id"], "category": candidate["category"],
                    "marker": {"scene_id": scene["id"], "position_world": surface,
                               "photo_id": candidate["photo_id"], "evidence_box": candidate["evidence"]["box"],
                               "method": "photo_surface_estimate",
                               "uncertainty": "Posizione indicativa dalla profondità della foto, non confermata da più viste."}})
            center = foreground_anchor(region, support, background, background_support, camera_center) if len(region) else None
            views = []
            if center is not None:
                for p in scene["images"]:
                    i = p["tensor_index"]
                    other_center = -cameras[i, :, :3].T @ cameras[i, :, 3]
                    if i != index and np.linalg.norm(other_center - camera_center) < .02 * np.linalg.norm(np.asarray(center) - camera_center):
                        continue
                    camera_point = cameras[i, :, :3] @ np.asarray(center) + cameras[i, :, 3]
                    if camera_point[2] <= 0:
                        continue
                    homogeneous = intrinsics[i] @ camera_point
                    u, v = homogeneous[:2] / homogeneous[2]
                    if not (0 <= u < 518 and 0 <= v < 518):
                        continue
                    u, v = int(round(u)), int(round(v))
                    if u >= 518 or v >= 518:
                        continue
                    cl, ct, cr, cb = p["transform"]["content_box"]
                    matching_boxes = [c["evidence"]["box"] for c in proposals if c["photo_id"] == p["photo_id"]
                                      and c["category"] == candidate["category"] and c["evidence"].get("box")]
                    sx2, sy2 = p["transform"]["scale_xy"]
                    ox2, oy2 = p["transform"]["offset_xy"]
                    boxed = any(l2 * sx2 + ox2 <= u < r2 * sx2 + ox2 and t2 * sy2 + oy2 <= v < b2 * sy2 + oy2
                                for l2, t2, r2, b2 in matching_boxes)
                    if boxed and cl <= u < cr and ct <= v < cb and confidence[i, v, u] >= scene["confidence_threshold"] and abs(depth[i, v, u] - camera_point[2]) <= .03 * camera_point[2]:
                        views.append(p["photo_id"])
            if len(scene["images"]) < 2:
                abstentions.append({"photo_id": candidate["photo_id"], "category": candidate["category"], "reason": "Single-view estimate cannot validate a 3D hazard anchor"})
                continue
            if center is None or len(views) < 2 or candidate["photo_id"] not in views:
                abstentions.append({"photo_id": candidate["photo_id"], "category": candidate["category"], "reason": "No separated foreground or matching boxed evidence with a distinct camera baseline"})
                continue
            anchors.append({"photo_id": candidate["photo_id"], "category": candidate["category"], "evidence_box": candidate["evidence"]["box"],
                            "anchor": {"scene_id": scene["id"], "position_world": center, "method": "foreground_multiview", "source_photo_ids": views,
                                       "uncertainty": "Posizione proposta da una regione e più viste coerenti; verificare che appartenga all’oggetto indicato. Scala metrica sconosciuta."}})
    return {"scenes": scenes, "measurements": [], "anchors": anchors, "anchor_abstentions": abstentions,
            "evidence_markers": evidence_markers, "provenance": "derived",
            "status": "degraded" if any(s["status"] != "ready" for s in scenes) else "succeeded",
            "warnings": ["Metric scale unknown; no dimensions are claimed", *context["outputs"]["reconstruct"].get("warnings", [])]}
