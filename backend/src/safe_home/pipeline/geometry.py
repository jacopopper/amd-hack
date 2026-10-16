import numpy as np
from safe_home import db


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


def supported_anchor(points, confidence, min_points=20):
    """Abstain when a foreground region has insufficient support or mixed depths."""
    points, confidence = np.asarray(points, float), np.asarray(confidence, float)
    selected = points[np.isfinite(points).all(axis=1) & np.isfinite(confidence) & (confidence >= .8)]
    if len(selected) < min_points:
        return None
    center = np.median(selected, axis=0)
    distances = np.linalg.norm(selected - center, axis=1)
    if np.quantile(distances, .9) > .2 * max(np.linalg.norm(center), 1e-6):
        return None
    return center.tolist()


def geometry(context):
    scenes = context["outputs"]["reconstruct"]["scenes"]
    anchors, abstentions = [], []
    for scene in scenes:
        if scene["status"] != "ready" or scene["provenance"] not in {"live", "replay"}:
            continue
        path = db.asset_path(context["settings"], scene["analysis_path"])
        if db.digest(path.read_bytes()) != scene["analysis_sha256"]:
            raise ValueError("Reconstruction analysis checksum mismatch")
        with np.load(path, allow_pickle=False) as arrays:
            points, depth, confidence = arrays["world_points"], arrays["depth"][..., 0], arrays["confidence"]
            intrinsics, cameras = arrays["intrinsics"], arrays["extrinsics"]
        by_photo = {p["photo_id"]: p for p in scene["images"]}
        for candidate in context["outputs"]["jev"]["candidates"]:
            if candidate["presence"] != "present" or not candidate["evidence"] or candidate["photo_id"] not in by_photo or candidate["category"] not in {"rug", "path", "stairs"}:
                continue
            image = by_photo[candidate["photo_id"]]
            index = image["tensor_index"]
            transform = image["transform"]
            sx, sy = transform["scale_xy"]
            ox, oy = transform["offset_xy"]
            l, t, r, b = candidate["evidence"]["box"]
            l, t, r, b = l * sx + ox, t * sy + oy, r * sx + ox, b * sy + oy
            # A substantial interior region, never a single box-center depth sample.
            dx, dy = (r - l) * .2, (b - t) * .2
            l, t, r, b = max(0, int(l + dx)), max(0, int(t + dy)), min(518, int(r - dx)), min(518, int(b - dy))
            region = points[index, t:b, l:r].reshape(-1, 3)
            support = (confidence[index, t:b, l:r] >= scene["confidence_threshold"]).ravel().astype(float)
            center = supported_anchor(region, support) if len(region) else None
            views = []
            if center is not None:
                for p in scene["images"]:
                    i = p["tensor_index"]
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
                    if cl <= u < cr and ct <= v < cb and confidence[i, v, u] >= scene["confidence_threshold"] and abs(depth[i, v, u] - camera_point[2]) <= .03 * camera_point[2]:
                        views.append(p["photo_id"])
            if center is None or len(views) < 2 or candidate["photo_id"] not in views:
                abstentions.append({"photo_id": candidate["photo_id"], "category": candidate["category"], "reason": "Insufficient compact foreground region or consistent second view"})
                continue
            anchors.append({"photo_id": candidate["photo_id"], "category": candidate["category"], "evidence_box": candidate["evidence"]["box"],
                            "anchor": {"scene_id": scene["id"], "position_world": center, "method": "foreground_multiview", "source_photo_ids": views,
                                       "uncertainty": "Posizione proposta da una regione e più viste coerenti; verificare che appartenga all’oggetto indicato. Scala metrica sconosciuta."}})
    return {"scenes": scenes, "measurements": [], "anchors": anchors, "anchor_abstentions": abstentions,
            "warnings": ["Metric scale unknown; no dimensions are claimed", *context["outputs"]["reconstruct"].get("warnings", [])]}
