"""Manually complete the five-photo Monolocale VGGT study, without Blender.

Run with model-cache/vggt-env/bin/python3 (NumPy, OpenCV and SciPy).
The normalized photo selections below are specific to this room. Original
analysis and photos are read only; all output is a separate visual GLB.
"""
import argparse
import io
import json
import sqlite3
import struct
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import median_filter


# Clean photographic regions selected by hand, in original-image fractions.
FLOOR_PATCH = (1, [[.35, .76], [.71, .76], [.73, .99], [.33, .99]])
KITCHEN_WALL_PATCH = (2, [[.42, .17], [.93, .12], [.93, .23], [.43, .27]])
# Each object is taken from its clearest photograph, avoiding five overlapping
# depth sheets. Background and reflective open-door pixels are intentionally
# supplied by the structural panels instead.
OBJECT_REGIONS = [
    [[[0, .32], [.19, .31], [.40, .43], [.40, .60], [.27, .69], [.52, .67], [.52, 1], [0, 1]],
     [[.26, .14], [.40, .14], [.45, .40], [.45, .45], [.28, .43]]],
    [[[0, .475], [.26, .48], [.27, .88], [0, .89]]],
    [[[0, .28], [.80, .246], [.87, .275], [.87, .46], [.99, .49], [.99, .94], [.81, .96], [.66, .93], [.16, .82], [.17, .72], [0, .695]]],
    [[[0, .485], [.32, .48], [.50, .57], [.65, .68], [.90, .69], [1, .75], [1, 1], [0, 1]],
     [[.13, .182], [.28, .185], [.35, .33], [.31, .34], [.14, .27]]],
    [[[0, .27], [.17, .27], [.18, .23], [.44, .24], [.44, .29], [.74, .29], [.75, .79], [.54, .79], [.46, 1], [0, 1]]],
]
# Visible planar fronts are repaired along camera rays using fitted VGGT
# planes. Their texture coordinates remain the exact original photo pixels.
PHOTO_PANELS = [
    ("glass-block-window", 1, [[.077,.241],[.720,.241],[.716,.459],[.100,.466]], [-.016105,-.398948,.916832], 1.385448),
    ("entrance-door", 0, [[.402,.060],[.610,.060],[.610,.422],[.445,.424]], [-.016105,-.398948,.916832], 1.375),
    ("upper-cabinet-front", 2, [[.105,.297],[.789,.266],[.778,.468],[.122,.452]], [.998732,.020219,-.046097], -.895143),
    ("base-cabinet-front", 2, [[.185,.584],[.535,.614],[.535,.780],[.185,.725]], [.999174,-.018859,-.036001], -.817176),
    ("office-cabinet-front", 4, [[.455,.290],[.741,.289],[.720,.788],[.452,.767]], [.999821,.005671,-.018027], .134724),
    ("monitor-back", 0, [[.000,.355],[.174,.311],[.198,.467],[.004,.528]], [.988748,-.015104,-.148829], -.208400),
]


def polygon_mask(shape, polygon, box):
    left, top, right, bottom = box
    polygon = np.asarray(polygon) * [right - left, bottom - top] + [left, top]
    mask = np.zeros(shape, np.uint8)
    cv2.fillPoly(mask, [np.rint(polygon).astype(np.int32)], 1)
    return mask.astype(bool)


def fit_plane(points):
    selected = points
    for _ in range(4):
        center = selected.mean(axis=0)
        _, _, axes = np.linalg.svd(selected - center, full_matrices=False)
        normal = axes[-1]
        error = np.abs((points - center) @ normal)
        selected = points[error <= max(.003, np.quantile(error, .85))]
    if normal[np.argmax(abs(normal))] < 0:
        normal = -normal
    return normal, float(center @ normal)


class Glb:
    """Small self-contained glTF writer; no exporter/runtime dependency."""

    def __init__(self):
        self.binary = bytearray()
        self.doc = {"asset": {"version": "2.0", "generator": "Photo surface completion"},
                    "extensionsUsed": ["KHR_materials_unlit"], "scene": 0,
                    "scenes": [{"nodes": []}], "nodes": [], "meshes": [],
                    "materials": [], "textures": [], "images": [],
                    "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
                    "accessors": [], "bufferViews": [], "buffers": []}

    def blob(self, value):
        self.binary.extend(b"\0" * (-len(self.binary) % 4))
        index = len(self.doc["bufferViews"])
        self.doc["bufferViews"].append({"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(value)})
        self.binary.extend(value)
        return index

    def array(self, value, kind):
        value = np.ascontiguousarray(value)
        accessor = {"bufferView": self.blob(value.tobytes()), "componentType": 5125 if value.dtype == np.uint32 else 5126,
                    "count": len(value), "type": kind}
        if kind == "VEC3":
            accessor.update(min=value.min(axis=0).tolist(), max=value.max(axis=0).tolist())
        self.doc["accessors"].append(accessor)
        return len(self.doc["accessors"]) - 1

    def material(self, name, rgb):
        stream = io.BytesIO()
        Image.fromarray(rgb).save(stream, "JPEG", quality=94)
        self.doc["images"].append({"bufferView": self.blob(stream.getvalue()), "mimeType": "image/jpeg"})
        self.doc["textures"].append({"sampler": 0, "source": len(self.doc["images"]) - 1})
        self.doc["materials"].append({"name": name, "doubleSided": True,
            "extensions": {"KHR_materials_unlit": {}}, "pbrMetallicRoughness": {
                "baseColorTexture": {"index": len(self.doc["textures"]) - 1}, "metallicFactor": 0, "roughnessFactor": 1}})
        return len(self.doc["materials"]) - 1

    def mesh(self, name, vertices, faces, uv, material, extras=None):
        if not len(faces):
            return
        used, remapped = np.unique(faces, return_inverse=True)
        vertices = np.asarray(vertices[used], dtype="<f4")
        uv = np.asarray(uv[used], dtype="<f4")
        assert np.isfinite(vertices).all() and np.isfinite(uv).all()
        primitive = {"attributes": {"POSITION": self.array(vertices, "VEC3"), "TEXCOORD_0": self.array(uv, "VEC2")},
                     "indices": self.array(remapped.astype("<u4").ravel(), "SCALAR"), "material": material}
        self.doc["meshes"].append({"name": name, "primitives": [primitive]})
        self.doc["nodes"].append({"name": name, "mesh": len(self.doc["meshes"]) - 1, "extras": extras or {}})
        self.doc["scenes"][0]["nodes"].append(len(self.doc["nodes"]) - 1)

    def save(self, path):
        components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}
        for accessor in self.doc["accessors"]:
            view = self.doc["bufferViews"][accessor["bufferView"]]
            assert view["byteLength"] == accessor["count"] * components[accessor["type"]] * 4
        self.binary.extend(b"\0" * (-len(self.binary) % 4))
        self.doc["buffers"] = [{"byteLength": len(self.binary)}]
        encoded = json.dumps(self.doc, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 4)
        path.write_bytes(struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(self.binary))
                         + struct.pack("<I4s", len(encoded), b"JSON") + encoded
                         + struct.pack("<I4s", len(self.binary), b"BIN\0") + self.binary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", required=True)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data/live-tests/monolocale-completed"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{args.data / 'assessment.sqlite3'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    scenes = conn.execute("SELECT * FROM scenes WHERE assessment_id=?", (args.assessment,)).fetchall()
    assert len(scenes) == 1, "This manual study expects the Monolocale's single room"
    scene = json.loads(scenes[0]["payload"])
    source = np.load(args.data / scene["analysis_path"])
    depth, points = source["depth"][..., 0], source["world_points"].copy()
    extrinsics, intrinsics = source["extrinsics"], source["intrinsics"]
    photos = []
    for item in scene["images"]:
        row = conn.execute("SELECT a.path FROM photos p JOIN assets a ON p.asset_id=a.id WHERE p.id=? AND p.active=1", (item["photo_id"],)).fetchone()
        photos.append(np.asarray(Image.open(args.data / row["path"]).convert("RGB")))
    assert len(photos) == 5, "Selections are hand-authored for these five views"
    conn.close()
    boxes = [i["transform"]["content_box"] for i in scene["images"]]
    floor_mask = polygon_mask(depth.shape[1:], FLOOR_PATCH[1], boxes[FLOOR_PATCH[0]])
    floor_down, floor_offset = fit_plane(points[FLOOR_PATCH[0]][floor_mask])
    wall_mask = polygon_mask(depth.shape[1:], KITCHEN_WALL_PATCH[1], boxes[KITCHEN_WALL_PATCH[0]])
    right, _ = fit_plane(points[KITCHEN_WALL_PATCH[0]][wall_mask])
    up = -floor_down
    right -= (right @ up) * up
    right /= np.linalg.norm(right)
    rotation = np.stack([right, up, np.cross(right, up)])
    translation = np.array([0, floor_offset, 0])
    transform = np.eye(4)
    transform[:3, :3], transform[:3, 3] = rotation, translation
    local = points @ rotation.T + translation
    # Hand-reviewed structural extents in the fitted, unscaled room frame.
    xmin, xmax, zmin, zmax, height = -.99, .285, -1.415, .22, .738
    floor_depth = -.006
    glb = Glb()
    metrics = []

    def raw(local_points):
        return (local_points - translation) @ rotation

    def project(query, view):
        camera = query @ extrinsics[view, :3, :3].T + extrinsics[view, :3, 3]
        pixels = camera @ intrinsics[view].T
        pixels = pixels[..., :2] / np.maximum(pixels[..., 2:], 1e-8)
        return pixels, camera[..., 2]

    def sample_photo(query, view, tolerance=.025):
        pixel, z = project(query, view)
        left, top, right_edge, bottom = boxes[view]
        image = photos[view]
        xx = ((pixel[..., 0] - left) / (right_edge - left) * image.shape[1]).astype(np.float32)
        yy = ((pixel[..., 1] - top) / (bottom - top) * image.shape[0]).astype(np.float32)
        color = cv2.remap(image, xx, yy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        measured = cv2.remap(depth[view], pixel[..., 0].astype(np.float32), pixel[..., 1].astype(np.float32), cv2.INTER_NEAREST)
        edge = np.minimum.reduce([pixel[..., 0] - left, right_edge - pixel[..., 0], pixel[..., 1] - top, bottom - pixel[..., 1]])
        visible = (z > 0) & (edge > 2) & (abs(z - measured) < tolerance)
        score = np.clip(edge / 25, 0, 1) / np.maximum(z, .1)**2 * visible
        return color, score

    def panel(name, corners, normal, fallback, texture_size=1024, fill_floor=False):
        corners = np.array(corners, float)
        uu, vv = np.meshgrid(np.linspace(0, 1, texture_size), np.linspace(0, 1, texture_size))
        query_local = corners[0] + uu[..., None] * (corners[1] - corners[0]) + vv[..., None] * (corners[3] - corners[0])
        query = raw(query_local)
        image = np.zeros((texture_size, texture_size, 3), np.float32)
        weight = np.zeros((texture_size, texture_size), np.float32)
        views = {"floor": [1], "window-wall": [0, 1], "kitchen-wall": [2, 3], "office-wall": [4], "unseen-end-wall": [], "ceiling": [1]}[name]
        for i in views:
            color, score = sample_photo(query, i, .015 if fill_floor else .045)
            if name == "window-wall":
                score *= (query_local[..., 0] > -.17) if i == 0 else (query_local[..., 0] <= -.17)
            # Prefer frontal views; do not stretch edge-on source pixels.
            center = -extrinsics[i, :3, :3].T @ extrinsics[i, :3, 3]
            direction = center - query
            cosine = abs(direction @ (normal @ rotation)) / np.maximum(np.linalg.norm(direction, axis=-1), 1e-5)
            score *= cosine**3
            score **= 3
            image += color * score[..., None]
            weight += score
        supported = weight > 1e-7
        image = np.clip(image / np.maximum(weight[..., None], 1e-8), 0, 255).astype(np.uint8)
        if fill_floor:
            # Rectify a clean observed wood patch, then mirror-tile only the gaps.
            px, pz = np.meshgrid(np.linspace(-.64, -.47, 320), np.linspace(-.65, -.44, 400))
            patch, _ = sample_photo(raw(np.stack([px, px * 0 + floor_depth, pz], -1)), 1)
            # Remove the source patch's broad light gradient before repetition;
            # otherwise the same shadow appears as a checkerboard across gaps.
            illumination = cv2.GaussianBlur(patch.astype(np.float32), (0,0), 35)
            patch = np.clip(patch.astype(np.float32) * patch.mean((0,1)) / np.maximum(illumination, 1), 0, 255).astype(np.uint8)
            tile = np.concatenate([patch, patch[:, ::-1]], axis=1)
            tile = np.concatenate([tile, tile[::-1]], axis=0)
            tile_x = ((query_local[..., 0] + .64) / .17 * 320 % tile.shape[1]).astype(np.float32)
            tile_y = ((query_local[..., 2] + .65) / .21 * 400 % tile.shape[0]).astype(np.float32)
            base = cv2.remap(tile, tile_x, tile_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
            Image.fromarray(patch).save(args.output / "floor-source-patch.jpg")
        else:
            base = np.full_like(image, fallback)
        supported = cv2.erode(supported.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        feather = np.clip(cv2.distanceTransform(supported.astype(np.uint8), cv2.DIST_L2, 3) / (70 if fill_floor else 12), 0, 1)
        image = (image * feather[..., None] + base * (1 - feather[..., None])).astype(np.uint8)
        Image.fromarray(image).save(args.output / f"{name}.jpg", quality=94)
        material = glb.material(name, image)
        metadata = {"surface_provenance": "photo_projected_with_manual_completion", "observed_texture_fraction": float(supported.mean())}
        if name != "floor":
            metadata.update(cutaway_normal=(normal @ rotation).tolist(), cutaway_origin=raw(corners[0]).tolist())
        glb.mesh(name, raw(corners), np.array([[0, 1, 2], [0, 2, 3]]), np.array([[0, 0], [1, 0], [1, 1], [0, 1]]), material, metadata)
        metrics.append({"name": name, "observed_texture_fraction": float(supported.mean())})

    panel("floor", [[xmin, floor_depth, zmax], [xmax, floor_depth, zmax], [xmax, floor_depth, zmin], [xmin, floor_depth, zmin]], np.array([0, 1, 0]), [126, 83, 48], 1536, True)
    panel("window-wall", [[xmax, height, zmin], [xmin, height, zmin], [xmin, 0, zmin], [xmax, 0, zmin]], np.array([0, 0, 1]), [188, 184, 172])
    panel("kitchen-wall", [[xmin, height, zmin], [xmin, height, zmax], [xmin, 0, zmax], [xmin, 0, zmin]], np.array([1, 0, 0]), [184, 181, 170])
    panel("office-wall", [[xmax, height, zmax], [xmax, height, zmin], [xmax, 0, zmin], [xmax, 0, zmax]], np.array([-1, 0, 0]), [182, 179, 169])
    # The unphotographed end remains an inferred wall, cut away when looking in.
    panel("unseen-end-wall", [[xmin, height, zmax], [xmax, height, zmax], [xmax, 0, zmax], [xmin, 0, zmax]], np.array([0, 0, -1]), [179, 175, 165])
    panel("ceiling", [[xmin,height,zmin],[xmax,height,zmin],[xmax,height,zmax],[xmin,height,zmax]], np.array([0,-1,0]), [175,171,163])

    photo_materials = [glb.material(f"original-photo-{i+1}", photo) for i, photo in enumerate(photos)]
    repaired_masks = [np.zeros(depth.shape[1:], bool) for _ in photos]
    for name, i, polygon, normal, offset in PHOTO_PANELS:
        normal = np.array(normal)
        left, top, right_edge, bottom = boxes[i]
        uv = np.array(polygon)
        pixels = uv * [right_edge-left, bottom-top] + [left,top]
        camera_rays = np.column_stack([pixels, np.ones(4)]) @ np.linalg.inv(intrinsics[i]).T
        rays = camera_rays @ extrinsics[i, :3, :3]
        camera_center = -extrinsics[i, :3, :3].T @ extrinsics[i, :3, 3]
        ray_depth = (offset - camera_center @ normal) / (rays @ normal)
        vertices = camera_center + rays * ray_depth[:, None]
        metadata = {"surface_provenance": "manually_repaired_photo_plane", "source_photo_id": scene["images"][i]["photo_id"]}
        if name in ("glass-block-window", "entrance-door"):
            metadata.update(cutaway_normal=(np.array([0,0,1]) @ rotation).tolist(), cutaway_origin=vertices[0].tolist())
        glb.mesh(name, vertices, np.array([[0,1,2],[0,2,3]]), uv, photo_materials[i], metadata)
        repaired_masks[i] |= polygon_mask(depth.shape[1:], polygon, boxes[i])

    def fill_box(name, low, high, material, texture_span=.12):
        x0,y0,z0 = low
        x1,y1,z1 = high
        faces = [
            [[x0,y1,z0],[x1,y1,z0],[x1,y1,z1],[x0,y1,z1]],
            [[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0]],
            [[x1,y0,z1],[x0,y0,z1],[x0,y1,z1],[x1,y1,z1]],
            [[x0,y0,z1],[x0,y0,z0],[x0,y1,z0],[x0,y1,z1]],
            [[x1,y0,z0],[x1,y0,z1],[x1,y1,z1],[x1,y1,z0]],
        ]
        for j, corners in enumerate(faces):
            corners = np.array(corners)
            u = np.linalg.norm(corners[1]-corners[0]) / texture_span
            v = np.linalg.norm(corners[3]-corners[0]) / texture_span
            glb.mesh(f"{name}-{j}", raw(corners), np.array([[0,1,2],[0,2,3]]),
                     np.array([[0,0],[u,0],[u,v],[0,v]]), material,
                     {"surface_provenance": "manually_inferred_gap_fill"})

    # The table plane is supported in two photos. Complete its occluded cloth
    # and underside using a clean crop of the same photographed tablecloth.
    cloth = glb.material("photographed-tablecloth", photos[4][1035:1095, 20:160])
    fill_box("table-cloth-completion", [-.43,.105,-.62], [-.106,.215,-.12], cloth, .095)
    leg_material = glb.material("table-leg-dark", np.full((8,8,3), [44,39,33], np.uint8))
    for x in [-.405,-.13]:
        for z in [-.59,-.15]:
            fill_box("table-leg-completion", [x-.005,0,z-.005], [x+.005,.12,z+.005], leg_material)
    fabric = glb.material("photographed-sofa-fabric", photos[3][975:1040, 40:140])
    fill_box("sofa-base-completion", [-.94,.035,-1.32], [-.59,.105,-.96], fabric, .17)

    # Preserve the actual photographed objects, at full source texture resolution.
    for i, photo in enumerate(photos):
        material = photo_materials[i]
        left, top, right_edge, bottom = boxes[i]
        yy, xx = np.mgrid[top:bottom:2, left:right_edge:2]
        object_mask = np.zeros(depth.shape[1:], bool)
        for region in OBJECT_REGIONS[i]:
            object_mask |= polygon_mask(depth.shape[1:], region, boxes[i])
        smoothed = median_filter(depth[i], size=3)
        z = smoothed[yy, xx]
        camera_points = np.stack([(xx - intrinsics[i, 0, 2]) / intrinsics[i, 0, 0] * z,
                                  (yy - intrinsics[i, 1, 2]) / intrinsics[i, 1, 1] * z, z], -1)
        vertices = (camera_points - extrinsics[i, :3, 3]) @ extrinsics[i, :3, :3]
        loc = vertices @ rotation.T + translation
        uv = np.stack([(xx - left) / (right_edge - left), (yy - top) / (bottom - top)], -1)
        valid = object_mask[yy, xx] & ~repaired_masks[i][yy, xx] & (loc[..., 1] > .006) & (loc[..., 1] < height - .04) & (loc[..., 0] > xmin - .03) & (loc[..., 0] < xmax + .03) & (loc[..., 2] > zmin - .04) & (loc[..., 2] < zmax + .03)
        if i in (0, 4):
            table_inside = ((loc[...,0] > -.435) & (loc[...,0] < -.10) & (loc[...,2] > -.625)
                            & (loc[...,2] < -.115) & (loc[...,1] < .225) & (loc[...,1] > .09))
            valid &= ~table_inside
        index = np.arange(z.size).reshape(z.shape)
        a, b, c, d = index[:-1, :-1], index[:-1, 1:], index[1:, :-1], index[1:, 1:]
        faces = np.concatenate([np.stack([a, c, b], -1).reshape(-1, 3), np.stack([b, c, d], -1).reshape(-1, 3)])
        faces = faces[valid.ravel()[faces].all(axis=1)]
        zd = z.ravel()[faces]
        faces = faces[np.ptp(zd, axis=1) < .03 * zd.min(axis=1)]
        centers = loc.reshape(-1, 3)[faces].mean(axis=1)
        categories = np.zeros(len(faces), int)
        categories[centers[:, 0] < xmin + .06] = 1
        categories[centers[:, 2] < zmin + .05] = 2
        categories[centers[:, 0] > xmax - .06] = 3
        categories[centers[:, 2] > zmax - .05] = 4
        wall_normals = [None, [1, 0, 0], [0, 0, 1], [-1, 0, 0], [0, 0, -1]]
        origins = [None, [xmin, 0, 0], [0, 0, zmin], [xmax, 0, 0], [0, 0, zmax]]
        for category in range(5):
            metadata = {"surface_provenance": "observed_depth", "source_photo_id": scene["images"][i]["photo_id"]}
            if category:
                metadata.update(cutaway_normal=(np.asarray(wall_normals[category]) @ rotation).tolist(), cutaway_origin=raw(np.asarray(origins[category])).tolist())
            glb.mesh(f"photo-{i+1}-{'objects' if category == 0 else 'wall-'+str(category)}", vertices.reshape(-1, 3), faces[categories == category], uv.reshape(-1, 2), material, metadata)
        metrics.append({"name": f"photo-{i+1}", "faces": len(faces)})
    glb.save(args.output / "room.glb")
    report = {"assessment_id": args.assessment, "scene_id": scenes[0]["id"], "source_analysis_sha256": scene["analysis_sha256"],
              "provenance": "photo_completed", "coordinate_space": "raw_scene_relative", "display_transform": transform.tolist(),
              "alignment_matrix": transform.tolist(), "floor_plane": {"normal": floor_down.tolist(), "offset": floor_offset},
              "manual_room_bounds": [[xmin, 0, zmin], [xmax, height, zmax]], "surfaces": metrics,
              "limitations": ["Room extents and unseen surfaces manually inferred; not a surveyed model", "Textures on unseen floor repeat a photographed wood patch", "No metric measurements or new analytical anchors"],
              "model_bytes": (args.output / "room.glb").stat().st_size}
    (args.output / "completion.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
