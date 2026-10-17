#!/usr/bin/env python3
"""Local-only capture reconstruction. Private inputs and outputs belong under data/."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def prepare(output, paths):
    import pillow_heif
    pillow_heif.register_heif_opener()
    if (output / "capture.json").exists():
        raise ValueError("Capture already exists; choose a new output directory")
    if len(paths) != len(set(p.resolve() for p in paths)):
        raise ValueError("Repeated input paths")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    (output / "images").mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (5 * 280, ((len(paths) + 4) // 5) * 235), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    photos = []
    for i, path in enumerate(paths):
        with Image.open(path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            original_size = list(image.size)
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            # Copy pixels to discard GPS, EXIF, XMP and source-container metadata.
            image = Image.fromarray(np.asarray(image))
        target = output / "images" / f"{i:03d}-{path.stem}.jpg"
        image.save(target, quality=96, subsampling=0)
        photos.append({"id": path.stem, "path": target.relative_to(output).as_posix(),
                       "source_name": path.name, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                       "upright_source_size": original_size, "size": list(image.size)})
        thumb = ImageOps.contain(image, (270, 205))
        x, y = (i % 5) * 280, (i // 5) * 235
        sheet.paste(thumb, (x + (280 - thumb.width) // 2, y))
        draw.text((x + 10, y + 210), path.stem, fill="black")
        print(f"Prepared {i + 1}/{len(paths)}: {path.name} {original_size}", flush=True)
    sheet.save(output / "contact-sheet.jpg", quality=90)
    save_json(output / "capture.json", {"version": 1, "photos": photos, "external_processing": False,
                                      "scale_state": "unknown", "metric_validated": False})


def reconstruct(output):
    import fcntl
    from step_wise.config import ROOT, Settings
    from step_wise.pipeline.reconstruction import live_scene_local
    capture = json.loads((output / "capture.json").read_text())
    if (output / "scene.json").exists():
        raise ValueError("Reconstruction exists; do not replace it implicitly")
    settings = Settings.from_env()
    from dataclasses import replace
    settings = replace(settings, data=output, max_photos=max(40, len(capture["photos"])),
                       frame_cap=len(capture["photos"]))
    with (ROOT / "model-cache/vggt-inference.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scene = live_scene_local({"settings": settings, "directory": output},
                                 {"id": "capture"}, capture["photos"])
    scene["source_photo_hashes"] = [p["sha256"] for p in capture["photos"]]
    scene["limitations"] = ["Photographed surfaces only; unseen geometry is not filled",
                            "Relative coordinates, no validated dimensions",
                            "VGGT estimated cameras, not surveyed camera poses"]
    save_json(output / "scene.json", scene)
    print(json.dumps({"views": len(capture["photos"]), "runtime": scene["runtime"],
                      "vertices": scene["display_vertices"], "triangles": scene["display_faces"]}), flush=True)


def capture_masks(output, scene):
    """Apply explicitly recorded normalized exclusion polygons, never drop a view."""
    exclusions = json.loads((output / "masks.json").read_text()) if (output / "masks.json").exists() else {}
    masks = []
    for entry in scene["images"]:
        transform = entry["transform"]
        mask = Image.new("L", (518, 518))
        draw = ImageDraw.Draw(mask)
        l, t, r, b = transform["content_box"]
        draw.rectangle((l, t, r - 1, b - 1), fill=255)
        for polygon in exclusions.get(entry["photo_id"], []):
            draw.polygon([(l + x * (r - l), t + y * (b - t)) for x, y in polygon], fill=0)
        masks.append(np.asarray(mask) > 0)
    return np.stack(masks)


def seed_points(analysis, masks, colors, maximum=220_000):
    """Confidence-filtered seeds from every view in one VGGT coordinate frame."""
    points = analysis["world_points"][:, ::3, ::3]
    confidence = analysis["confidence"][:, ::3, ::3]
    depth = analysis["depth"][:, ::3, ::3, 0]
    valid = masks[:, ::3, ::3] & (depth > 0) & np.isfinite(points).all(-1)
    valid &= confidence >= np.quantile(confidence[valid], .3)
    xyz, rgb = points[valid], colors[:, ::3, ::3][valid]
    if len(xyz) < 1000:
        raise ValueError("Too few confident surface seeds")
    rng = np.random.default_rng(17)
    if len(xyz) > maximum:
        selected = rng.choice(len(xyz), maximum, replace=False)
        xyz, rgb = xyz[selected], rgb[selected]
    return xyz.astype(np.float32), rgb.astype(np.float32)


def train(output, steps, resolution):
    import math
    import torch
    import torch.nn.functional as F
    from gsplat import DefaultStrategy, export_splats, rasterization
    from scipy.spatial import cKDTree
    if not torch.cuda.is_available():
        raise RuntimeError("Gaussian training requires CUDA; no CPU fallback")
    if (output / "training.json").exists():
        raise ValueError("Training already exists; use a new capture output")
    torch.manual_seed(17)
    np.random.seed(17)
    torch.set_num_threads(6)
    scene = json.loads((output / "scene.json").read_text())
    capture = json.loads((output / "capture.json").read_text())
    analysis = np.load(output / scene["analysis_path"])
    masks = capture_masks(output, scene)
    colors = np.stack([np.asarray(Image.open(output / "assets" / f"{p['id']}-vggt.png")) / 255
                       for p in capture["photos"]])
    xyz, rgb = seed_points(analysis, masks, colors)
    distances, _ = cKDTree(xyz).query(xyz, k=4, workers=4)
    initial_scale = np.sqrt(np.mean(distances[:, 1:] ** 2, axis=1)).clip(1e-5).astype(np.float32)
    centers = np.asarray(scene["cameras"])
    scene_scale = float(np.linalg.norm(centers - centers.mean(0), axis=1).max())
    n = len(xyz)
    device = "cuda"
    params = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(torch.tensor(xyz, device=device)),
        "scales": torch.nn.Parameter(torch.tensor(np.log(initial_scale[:, None].repeat(3, axis=1)), device=device)),
        "quats": torch.nn.Parameter(F.normalize(torch.randn(n, 4, device=device), dim=-1)),
        "opacities": torch.nn.Parameter(torch.full((n,), math.log(.1 / .9), device=device)),
        "sh0": torch.nn.Parameter(torch.tensor((rgb[:, None] - .5) / .28209479177387814, device=device)),
        "shN": torch.nn.Parameter(torch.zeros(n, 8, 3, device=device)),
    })
    rates = {"means": .00016 * scene_scale, "scales": .005, "quats": .001,
             "opacities": .05, "sh0": .0025, "shN": .0025 / 20}
    optimizers = {k: torch.optim.Adam([{"params": [p], "lr": rates[k]}], eps=1e-15)
                  for k, p in params.items()}
    strategy = DefaultStrategy(refine_start_iter=400, refine_stop_iter=int(steps * .7),
                               reset_every=3000, refine_every=100, pause_refine_after_reset=90,
                               grow_grad2d=.0004, prune_scale3d=.2)
    strategy.check_sanity(params, optimizers)
    state = strategy.initialize_state(scene_scale)
    views = np.tile(np.eye(4, dtype=np.float32), (len(capture["photos"]), 1, 1))
    views[:, :3] = analysis["extrinsics"]
    viewmats = torch.tensor(views, device=device)
    Ks = torch.tensor(analysis["intrinsics"], device=device)
    poses = torch.nn.Parameter(torch.zeros(len(views), 6, device=device))
    pose_optimizer = torch.optim.Adam([poses], lr=.0001)
    high_images = []
    for photo, entry in zip(capture["photos"], scene["images"], strict=True):
        canvas = Image.new("RGB", (resolution, resolution), "white")
        l, t, r, b = entry["transform"]["content_box"]
        factor = resolution / 518
        box = tuple(round(v * factor) for v in (l, t, r, b))
        with Image.open(output / photo["path"]) as image:
            canvas.paste(image.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS), box[:2])
        high_images.append(torch.tensor(np.asarray(canvas).copy(), device=device, dtype=torch.float32) / 255)
    targets = torch.stack(high_images)
    high_masks = F.interpolate(torch.tensor(masks[:, None], device=device, dtype=torch.float32),
                               size=(resolution, resolution), mode="nearest").permute(0, 2, 3, 1)
    low_targets = torch.tensor(colors, device=device, dtype=torch.float32)
    low_masks = torch.tensor(masks[..., None], device=device, dtype=torch.float32)
    depth_targets = torch.tensor(analysis["depth"], device=device, dtype=torch.float32)
    order = []
    seen = np.zeros(len(views), dtype=int)
    records = []
    start = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    (output / "renders").mkdir(exist_ok=True)

    def adjusted_camera(index):
        twist = poses[index]
        x, y, z = twist[:3].unbind()
        zero = x * 0
        skew = torch.stack([zero, -z, y, z, zero, -x, -y, x, zero]).reshape(3, 3)
        generator = torch.cat([torch.cat([skew, twist[3:, None]], dim=1),
                               torch.zeros(1, 4, device=device)], dim=0)
        return torch.matrix_exp(generator) @ viewmats[index]

    def render(index, size, degree):
        rendered, alpha, info = rasterization(params["means"], params["quats"], params["scales"].exp(),
                             params["opacities"].sigmoid(), torch.cat([params["sh0"], params["shN"]], 1),
                             adjusted_camera(index)[None], (Ks[index] * torch.tensor(
                                 [[size / 518, size / 518, size / 518],
                                  [size / 518, size / 518, size / 518], [1, 1, 1]], device=device))[None],
                             size, size, sh_degree=degree, render_mode="RGB+ED",
                             packed=True, near_plane=.005, far_plane=50)
        # Avoid the installed gsplat packed-background shape bug; composite RGB only.
        rendered = torch.cat([rendered[..., :3] + 1 - alpha, rendered[..., 3:]], dim=-1)
        return rendered, alpha, info

    def checkpoint(step):
        torch.save({"params": params.state_dict(), "poses": poses.detach().cpu(),
                    "step": step, "input_sha256": scene["source_photo_hashes"],
                    "analysis_sha256": scene["analysis_sha256"]}, output / "checkpoint.pt")

    for step in range(steps):
        if not order:
            order = np.random.permutation(len(views)).tolist()
        index = order.pop()
        seen[index] += 1
        size = 518 if step < min(1500, steps // 3) else resolution
        target = low_targets[index] if size == 518 else targets[index]
        mask = low_masks[index] if size == 518 else high_masks[index]
        degree = min(step // 1500, 2)
        rendered, alpha, info = render(index, size, degree)
        prediction = rendered[0, ..., :3]
        strategy.step_pre_backward(params, optimizers, state, step, info)
        l1 = ((prediction - target).abs() * mask).sum() / (mask.sum() * 3)
        # Local structural similarity, excluding all unobserved/moving-person pixels.
        pair = torch.stack([prediction * mask + target * (1 - mask), target]).permute(0, 3, 1, 2)
        pair = F.avg_pool2d(pair, 2)
        mean = F.avg_pool2d(pair, 7, 1, 3)
        variance = F.avg_pool2d(pair.square(), 7, 1, 3) - mean.square()
        covariance = F.avg_pool2d(pair[0:1] * pair[1:2], 7, 1, 3)[0] - mean[0] * mean[1]
        ssim = (((2 * mean[0] * mean[1] + .01**2) * (2 * covariance + .03**2)) /
                ((mean[0].square() + mean[1].square() + .01**2) *
                 (variance[0] + variance[1] + .03**2))).mean()
        expected_depth = depth_targets[index:index + 1].permute(0, 3, 1, 2)
        if size != 518:
            expected_depth = F.interpolate(expected_depth, (size, size), mode="bilinear", align_corners=False)
        expected_depth = expected_depth[0, 0]
        depth_loss = ((rendered[0, ..., 3] - expected_depth).abs() /
                      expected_depth.clamp_min(.01) * mask[..., 0]).sum() / mask.sum()
        pose_reg = poses[index, :3].square().mean() + poses[index, 3:].square().mean() / scene_scale**2
        loss = .8 * l1 + .2 * (1 - ssim) + .015 * depth_loss + .1 * pose_reg
        if not torch.isfinite(loss):
            raise ValueError(f"Nonfinite training loss at step {step}")
        loss.backward()
        for key, optimizer in optimizers.items():
            if key == "means":
                optimizer.param_groups[0]["lr"] = rates[key] * .01 ** (step / steps)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        if step > 500:
            pose_optimizer.step()
        pose_optimizer.zero_grad(set_to_none=True)
        # Keep the first camera as the gauge; this does not introduce metric scale.
        with torch.no_grad():
            poses[0].zero_()
        strategy.step_post_backward(params, optimizers, state, step, info, packed=True)
        if len(params["means"]) > 750_000:
            strategy.refine_stop_iter = step
        if step % 100 == 0 or step == steps - 1:
            record = {"step": step + 1, "steps": steps, "gaussians": len(params["means"]),
                      "loss": float(loss.detach()), "l1": float(l1.detach()),
                      "ssim": float(ssim.detach()), "depth_loss": float(depth_loss.detach()),
                      "elapsed_seconds": time.perf_counter() - start}
            records.append(record)
            save_json(output / "training-progress.json", record)
            print(json.dumps(record), flush=True)
        if step % 1500 == 0 or step == steps - 1:
            checkpoint(step + 1)
            with torch.no_grad():
                for preview in [5, 12, 20, 29, 36, 44]:
                    if preview >= len(views):
                        continue
                    result, _, _ = render(preview, resolution, degree)
                    image = (result[0, ..., :3].clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)
                    Image.fromarray(image).save(output / "renders" / f"view-{preview:03d}.jpg", quality=92)
    with torch.no_grad():
        for format in ("ply", "splat"):
            export_splats(**{key: params[key] for key in ("means", "scales", "quats", "opacities", "sh0", "shN")},
                          format=format, save_to=str(output / f"room.{format}"))
        final_views = torch.stack([adjusted_camera(i) for i in range(len(views))]).cpu().numpy()
    np.savez_compressed(output / "trained-cameras.npz", extrinsics=final_views, intrinsics=analysis["intrinsics"])
    save_json(output / "training.json", {
        "engine": "gsplat", "version": __import__("gsplat").__version__, "steps": steps,
        "resolution": resolution, "gaussians": len(params["means"]),
        "views": len(views), "view_training_visits": seen.tolist(), "seed_points": n,
        "elapsed_seconds": time.perf_counter() - start,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "analysis_sha256": scene["analysis_sha256"], "source_photo_hashes": scene["source_photo_hashes"],
        "scale_state": "unknown", "metric_validated": False, "external_processing": False,
        "limitations": ["Training-view image fit is not held-out novel-view quality",
                        "VGGT-estimated cameras with photometric pose adjustment",
                        "No inferred completion of unseen surfaces"], "progress": records})
    print("TRAINING_COMPLETE", flush=True)


def write_viewer(output):
    import shutil
    from step_wise.config import ROOT
    from step_wise.pipeline.reconstruction import observed_surface, write_ply
    scene = json.loads((output / "scene.json").read_text())
    capture = json.loads((output / "capture.json").read_text())
    analysis = np.load(output / scene["analysis_path"])
    masks = capture_masks(output, scene)
    colors = np.stack([np.asarray(Image.open(output / "assets" / f"{p['id']}-vggt.png"))
                       for p in capture["photos"]])
    points, rgb, faces = observed_surface(analysis["world_points"], analysis["depth"],
                                         analysis["confidence"], colors, masks,
                                         scene["display_confidence_threshold"])
    write_ply(output / "observed.ply", points, rgb, faces)
    cameras = np.tile(np.eye(4), (len(capture["photos"]), 1, 1))
    cameras[:, :3] = analysis["extrinsics"]
    if (output / "trained-cameras.npz").is_file():
        cameras = np.load(output / "trained-cameras.npz")["extrinsics"]
    viewpoints = [{"id": p["id"], "image": p["path"], "world_to_camera": camera.tolist(),
                   "intrinsics": k.tolist()}
                  for p, camera, k in zip(capture["photos"], cameras, analysis["intrinsics"], strict=True)]
    save_json(output / "viewpoints.json", {"views": viewpoints, "bounds": scene["bounds"],
                                         "mesh_vertices": len(points), "mesh_triangles": len(faces)})
    vendor = output / "vendor"
    vendor.mkdir(exist_ok=True)
    modules = ROOT / "model-cache/capture-viewer/node_modules"
    for source, target in [
        (modules / "three/build/three.module.js", "three.module.js"),
        (modules / "three/examples/jsm/controls/OrbitControls.js", "OrbitControls.js"),
        (modules / "three/examples/jsm/loaders/PLYLoader.js", "PLYLoader.js"),
        (modules / "three/LICENSE", "LICENSE-three.txt"),
        (modules / "@mkkellogg/gaussian-splats-3d/build/gaussian-splats-3d.module.js", "splats.js"),
        (modules / "@mkkellogg/gaussian-splats-3d/LICENSE", "LICENSE-splats.txt"),
    ]:
        shutil.copyfile(source, vendor / target)
    shutil.copyfile(ROOT / "scripts/capture_viewer.html", output / "index.html")
    print(json.dumps({"viewer": str(output / "index.html"), "views": len(viewpoints),
                      "vertices": len(points), "triangles": len(faces)}))


def evaluate(output):
    """Check all selected training views and render genuinely interpolated cameras."""
    import torch
    from gsplat import rasterization
    from scipy.spatial.transform import Rotation, Slerp
    capture = json.loads((output / "capture.json").read_text())
    scene = json.loads((output / "scene.json").read_text())
    training = json.loads((output / "training.json").read_text())
    checkpoint = torch.load(output / "checkpoint.pt", map_location="cuda", weights_only=True)
    hashes = [hashlib.sha256((output / p["path"]).read_bytes()).hexdigest() for p in capture["photos"]]
    if hashes != checkpoint["input_sha256"] or hashes != scene["source_photo_hashes"]:
        raise ValueError("Training and input photos differ")
    if hashlib.sha256((output / scene["analysis_path"]).read_bytes()).hexdigest() != checkpoint["analysis_sha256"]:
        raise ValueError("Training and VGGT reconstruction differ")
    params = checkpoint["params"]
    cameras = np.load(output / "trained-cameras.npz")
    masks = capture_masks(output, scene)
    size = training["resolution"]
    output_renders = output / "evaluation"
    output_renders.mkdir(exist_ok=True)
    metrics = []
    sheet = Image.new("RGB", (5 * 240, ((len(capture["photos"]) + 4) // 5) * 265), "#eeeeee")
    draw = ImageDraw.Draw(sheet)

    def render(camera, k):
        intrinsic = k.copy()
        intrinsic[:2] *= size / 518
        with torch.inference_mode():
            rgb, alpha, _ = rasterization(
                params["means"], params["quats"], params["scales"].exp(), params["opacities"].sigmoid(),
                torch.cat([params["sh0"], params["shN"]], dim=1),
                torch.tensor(camera[None], device="cuda", dtype=torch.float32),
                torch.tensor(intrinsic[None], device="cuda", dtype=torch.float32),
                size, size, sh_degree=2, packed=True, near_plane=.005, far_plane=50)
            rgb = (rgb + 1 - alpha).clamp(0, 1).cpu().numpy()[0]
            alpha = alpha.cpu().numpy()[0, ..., 0]
        return rgb, alpha

    for i, photo in enumerate(capture["photos"]):
        rgb, alpha = render(cameras["extrinsics"][i], cameras["intrinsics"][i])
        mask = np.asarray(Image.fromarray(masks[i]).resize((size, size), Image.Resampling.NEAREST))
        canvas = Image.new("RGB", (size, size), "white")
        box = tuple(round(v * size / 518) for v in scene["images"][i]["transform"]["content_box"])
        with Image.open(output / photo["path"]) as original:
            canvas.paste(original.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS), box[:2])
        error = rgb[mask] - np.asarray(canvas)[mask] / 255
        mse = float(np.mean(error ** 2))
        metrics.append({"photo_id": photo["id"], "psnr_training_view_db": float(-10 * np.log10(max(mse, 1e-10))),
                        "l1_training_view": float(np.abs(error).mean()),
                        "opaque_fraction_of_photo": float(np.mean(alpha[mask] >= .5))})
        image = Image.fromarray((rgb * 255).astype(np.uint8))
        image.save(output_renders / f"{photo['id']}.jpg", quality=93)
        thumb = ImageOps.contain(image.crop(box), (230, 235))
        x, y = (i % 5) * 240, (i // 5) * 265
        sheet.paste(thumb, (x + (240 - thumb.width) // 2, y))
        draw.text((x + 5, y + 240), photo["id"], fill="black")
    sheet.save(output / "render-contact-sheet.jpg", quality=92)
    novel_views = []
    for index in [2, 8, 14, 20, 26, 32]:
        if index + 1 >= len(capture["photos"]):
            continue
        pair = np.linalg.inv(cameras["extrinsics"][[index, index + 1]])
        rotation = Slerp([0, 1], Rotation.from_matrix(pair[:, :3, :3]))([.5]).as_matrix()[0]
        c2w = np.eye(4)
        c2w[:3, :3] = rotation
        c2w[:3, 3] = pair[:, :3, 3].mean(0)
        k = cameras["intrinsics"][[index, index + 1]].mean(0)
        rgb, _ = render(np.linalg.inv(c2w), k)
        path = output_renders / f"interpolated-{index:03d}.jpg"
        Image.fromarray((rgb * 255).astype(np.uint8)).save(path, quality=93)
        novel_views.append(path.relative_to(output).as_posix())
    report = {"views": len(metrics), "metrics_scope": "training-view fit, not held-out accuracy",
              "mean_training_psnr_db": float(np.mean([m["psnr_training_view_db"] for m in metrics])),
              "minimum_opaque_photo_fraction": min(m["opaque_fraction_of_photo"] for m in metrics),
              "all_source_hashes_match": True, "per_view": metrics, "interpolated_renders": novel_views,
              "metric_validated": False, "unseen_surfaces_completed": False}
    save_json(output / "verification.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in {"per_view", "interpolated_renders"}}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "vggt", "train", "viewer", "evaluate"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+")
    parser.add_argument("--steps", type=int, default=9000)
    parser.add_argument("--resolution", type=int, default=1036)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.stage == "prepare":
        if not args.images:
            parser.error("prepare requires --images")
        prepare(output, args.images)
    elif args.stage == "vggt":
        reconstruct(output)
    elif args.stage == "viewer":
        write_viewer(output)
    elif args.stage == "evaluate":
        evaluate(output)
    else:
        if args.steps < 1 or args.resolution < 518:
            parser.error("steps must be positive and resolution at least 518")
        train(output, args.steps, args.resolution)


if __name__ == "__main__":
    main()
