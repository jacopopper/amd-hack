"""Conservatively regularize observed floor/ceiling splats, without filling holes.

Writes a separate derivative PLY; the trained input, colors, opacity, and all
other surfaces remain unchanged. The manual hazard regions are protected.
Units are relative, and the result is for visualization, not measurement.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation


def read_splats(path):
    header = bytearray()
    fields = []
    count = 0
    with path.open("rb") as stream:
        while True:
            line = stream.readline()
            if not line:
                raise ValueError("Incomplete PLY header")
            header.extend(line)
            if line.startswith(b"element vertex "):
                count = int(line.split()[-1])
            if line.startswith(b"property "):
                _, kind, name = line.decode().split()
                if kind != "float":
                    raise ValueError("Expected float-only Gaussian PLY")
                fields.append((name, "<f4"))
            if line.strip() == b"end_header":
                break
        if b"format binary_little_endian 1.0" not in header:
            raise ValueError("Expected binary little-endian PLY")
        rows = np.fromfile(stream, dtype=np.dtype(fields), count=count)
    if len(rows) != count:
        raise ValueError("Truncated Gaussian PLY")
    return bytes(header), rows


def region_points(arrays, scene, index, region):
    left, top, right, bottom = scene["images"][index]["transform"]["content_box"]
    x0, y0, x1, y1 = region
    a, b = round(left + x0 * (right - left)), round(left + x1 * (right - left))
    c, d = round(top + y0 * (bottom - top)), round(top + y1 * (bottom - top))
    points = arrays["world_points"][index, c:d:3, a:b:3].reshape(-1, 3)
    confidence = arrays["confidence"][index, c:d:3, a:b:3].reshape(-1)
    return points[np.isfinite(points).all(1) & (confidence > np.quantile(confidence, .3))]


def fit_plane(points, seed):
    rng = np.random.default_rng(seed)
    if len(points) > 7000:
        points = points[rng.choice(len(points), 7000, replace=False)]
    span = np.linalg.norm(np.ptp(points, axis=0))
    threshold = span * .008
    best = None
    for _ in range(220):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        # Raw world is oriented close to the first camera: y points down.
        if abs(n[1]) < .8:
            continue
        mask = np.abs((points - a) @ n) < threshold
        if best is None or mask.sum() > best.sum():
            best = mask
    if best is None or best.mean() < .4:
        raise ValueError("No reliable near-horizontal surface in manual regions")
    center = points[best].mean(0)
    _, _, axes = np.linalg.svd(points[best] - center, full_matrices=False)
    normal = axes[-1]
    if normal[1] < 0:
        normal = -normal
    inliers = points[np.abs((points - center) @ normal) < threshold]
    return center, normal, axes[:2], inliers, threshold


def polish(source):
    source = source.resolve(strict=True)
    scene = json.loads((source / "scene.json").read_text())
    header, rows = read_splats(source / "room.ply")
    means = np.column_stack([rows[k] for k in ("x", "y", "z")]).astype(float)
    original = means.copy()
    metadata = {"provenance": "derived_planar_surface_regularization", "metric_validated": False,
                "fills_unseen_areas": False, "input_sha256": hashlib.sha256((source / "room.ply").read_bytes()).hexdigest(),
                "preserves_colors_and_opacity": True, "surfaces": []}
    with np.load(source / "assets/capture-analysis.npz") as arrays:
        # Photo-backed manual sampling, avoiding furniture and the ceiling beam.
        groups = {
            "floor": [(22, (.05, .76, .95, .98)), (23, (.05, .77, .95, .98)),
                      (24, (.12, .87, .82, .98)), (25, (.15, .87, .82, .98))],
            "ceiling": [(1, (.15, .025, .85, .10)), (2, (.12, .025, .82, .10)),
                        (3, (.12, .025, .82, .10)), (4, (.15, .025, .82, .09))],
        }
        protected = np.zeros(len(means), dtype=bool)
        for index, box in [(19, (.025, .30, .44, 1)), (23, (.38, .55, .86, .68))]:
            # Preserve all Gaussians in the evidence ray region, including thin
            # carpet/cable splats that the depth estimator may miss.
            camera = arrays["extrinsics"][index]
            xyz = original @ camera[:, :3].T + camera[:, 3]
            uvw = xyz @ arrays["intrinsics"][index].T
            uv = uvw[:, :2] / np.maximum(uvw[:, 2:3], 1e-9)
            l, t, r, b = scene["images"][index]["transform"]["content_box"]
            x0, y0, x1, y1 = box
            protected |= ((xyz[:, 2] > 0) & (uv[:, 0] > l + x0 * (r - l)) &
                          (uv[:, 0] < l + x1 * (r - l)) & (uv[:, 1] > t + y0 * (b - t)) &
                          (uv[:, 1] < t + y1 * (b - t)))
        for number, (name, regions) in enumerate(groups.items()):
            samples = np.concatenate([region_points(arrays, scene, i, box) for i, box in regions])
            center, normal, tangent, inliers, threshold = fit_plane(samples, number)
            xy = (inliers - center) @ tangent.T
            hull = ConvexHull(xy)
            projected = (original - center) @ tangent.T
            inside = np.ones(len(means), dtype=bool)
            for equation in hull.equations:
                inside &= projected @ equation[:2] + equation[2] <= 0
            distance = (original - center) @ normal
            # Only already-near-plane Gaussians inside observed support.
            mask = inside & ~protected & (np.abs(distance) < threshold * 1.5)
            ids = np.flatnonzero(mask)
            means[ids] -= .65 * distance[ids, None] * normal
            # Reduce thickness normal to the plane without blurring its texture.
            quats = np.column_stack([rows[f"rot_{i}"][ids] for i in range(4)])
            rotation = Rotation.from_quat(quats[:, [1, 2, 3, 0]]).as_matrix()
            scales = np.exp(np.column_stack([rows[f"scale_{i}"][ids] for i in range(3)]))
            covariance = (rotation * scales[:, None, :] ** 2) @ rotation.transpose(0, 2, 1)
            squeeze = np.eye(3) - .35 * np.outer(normal, normal)
            values, vectors = np.linalg.eigh(squeeze @ covariance @ squeeze.T)
            vectors[:, :, 0] *= np.linalg.det(vectors)[:, None]
            result_quats = Rotation.from_matrix(vectors).as_quat()[:, [3, 0, 1, 2]]
            for i in range(3):
                rows[f"scale_{i}"][ids] = .5 * np.log(np.maximum(values[:, i], 1e-16))
            for i in range(4):
                rows[f"rot_{i}"][ids] = result_quats[:, i]
            before = float(np.sqrt(np.mean(distance[ids] ** 2))) if len(ids) else 0
            metadata["surfaces"].append({"surface": name, "splats_regularized": len(ids),
                "plane_normal": normal.tolist(), "plane_center": center.tolist(),
                "rms_plane_distance_before": before, "rms_plane_distance_after": before * .35,
                "units": "relative", "observed_region_samples": len(inliers)})
    for i, field in enumerate(("x", "y", "z")):
        rows[field] = means[:, i]
    output = source / "room-polished.ply"
    with output.open("wb") as stream:
        stream.write(header)
        rows.tofile(stream)
    metadata["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    (source / "surface-polish.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"output": str(output), "surfaces": metadata["surfaces"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    polish(parser.parse_args().capture)
