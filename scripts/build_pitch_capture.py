"""Pack the committed 39-photo capture into pitch/assets/capture3d.js for the pitch 3D slide.

Cameras are the real VGGT poses from viewpoints.json; points are the centres and base colours
of the opaque Gaussians in room-polished.ply. Each point is tagged with the photo that sees it
from closest up (inside its view frustum), so the slide can grow the room photo by photo.
The fitted ceiling band is left out so the room reads from above, like a dollhouse.
"""
import base64
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "frontend/public/capture-data"
OUT = ROOT / "pitch/assets/capture3d.js"
POINTS = 160_000
SH_C0 = 0.28209479177387814


def gaussians(path):
    raw = path.read_bytes()
    header, body = raw.split(b"end_header\n", 1)
    lines = header.decode().splitlines()
    count = int(next(l for l in lines if l.startswith("element vertex")).split()[-1])
    names = [l.split()[-1] for l in lines if l.startswith("property")]
    data = np.frombuffer(body, dtype="<f4", count=count * len(names)).reshape(count, len(names))
    col = {n: i for i, n in enumerate(names)}
    xyz = data[:, [col["x"], col["y"], col["z"]]]
    rgb = np.clip(0.5 + SH_C0 * data[:, [col["f_dc_0"], col["f_dc_1"], col["f_dc_2"]]], 0, 1)
    opacity = 1 / (1 + np.exp(-data[:, col["opacity"]]))
    return xyz, rgb, opacity


def main():
    views = json.loads((CAPTURE / "viewpoints.json").read_text())["views"]
    xyz, rgb, opacity = gaussians(CAPTURE / "room-polished.ply")
    keep = opacity > 0.6
    xyz, rgb = xyz[keep], rgb[keep]
    rng = np.random.default_rng(7)
    pick = rng.choice(len(xyz), size=min(POINTS, len(xyz)), replace=False)
    xyz, rgb = xyz[pick], rgb[pick]

    cams, first, nearest = [], np.full(len(xyz), -1), np.full(len(xyz), np.inf)
    for i, v in enumerate(views):
        w2c = np.array(v["world_to_camera"]); k = np.array(v["intrinsics"])
        c2w = np.linalg.inv(w2c)
        cams.append({"c": c2w[:3, 3], "r": c2w[:3, 0], "d": c2w[:3, 1], "f": c2w[:3, 2]})
        p = xyz @ w2c[:3, :3].T + w2c[:3, 3]
        z = p[:, 2]
        u = k[0, 0] * p[:, 0] / np.maximum(z, 1e-6) + k[0, 2]
        w = k[1, 1] * p[:, 1] / np.maximum(z, 1e-6) + k[1, 2]
        seen = (z > 0.05) & (z < 2.5) & (u >= 0) & (u < 2 * k[0, 2]) & (w >= 0) & (w < 2 * k[1, 2])
        closer = seen & (z < nearest)
        first[closer], nearest[closer] = i, z[closer]
    first[first < 0] = len(views) - 1

    # Display frame: y up along the fitted floor normal, centred on the points, unit radius.
    planes = {p["surface"]: p for p in json.loads((CAPTURE / "surface-polish.json").read_text())["surfaces"]}
    up = np.array(planes["floor"]["plane_normal"]); up /= np.linalg.norm(up)
    if up @ np.mean([c["d"] for c in cams], axis=0) > 0:
        up = -up
    fwd = np.mean([c["f"] for c in cams], axis=0); fwd -= up * (fwd @ up); fwd /= np.linalg.norm(fwd)
    right = np.cross(up, fwd)
    rot = np.stack([right, up, fwd])
    centre = np.median(xyz, axis=0)
    local = (xyz - centre) @ rot.T
    scale = np.percentile(np.abs(local), 98) * 1.05
    local /= scale
    floor_y, ceiling_y = ((np.array(planes[k]["plane_center"]) - centre) @ up / scale for k in ("floor", "ceiling"))
    room = local[:, 1] < ceiling_y - 0.12 * (ceiling_y - floor_y)
    local, rgb, first = local[room], rgb[room], first[room]

    order = np.argsort(first, kind="stable")
    local, rgb, first = local[order], rgb[order], first[order]
    q = np.clip(np.round(local / 2 * 32767), -32767, 32767).astype("<i2")
    starts = np.searchsorted(first, np.arange(len(views) + 1)).tolist()
    tr = lambda a: [round(float(x), 4) for x in a]
    out = {
        "scale": 2,
        "points": base64.b64encode(q.tobytes()).decode(),
        "rgb": base64.b64encode((rgb * 255).round().astype(np.uint8).tobytes()).decode(),
        "starts": starts,
        "cams": [{"c": tr((c["c"] - centre) @ rot.T / scale), "f": tr(c["f"] @ rot.T), "r": tr(c["r"] @ rot.T),
                  "u": tr(-c["d"] @ rot.T)} for c in cams],
    }
    OUT.write_text("window.CAPTURE3D=" + json.dumps(out, separators=(",", ":")) + ";\n")
    print(f"{OUT.relative_to(ROOT)}: {len(q)} points, {len(cams)} cameras, {OUT.stat().st_size / 1e6:.1f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
