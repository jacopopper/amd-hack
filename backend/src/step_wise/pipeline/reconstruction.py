import os
import json
import math
import subprocess
import time
from inspect import getfile
from pathlib import Path

import numpy as np
from PIL import Image

from step_wise import db
from step_wise.pipeline.http import ExternalFailure


def aligned_input(image):
    """Controlled square input: preserve all pixels; record actual rounded resize/pad."""
    w, h = image.size
    if w >= h:
        rw, rh = 518, 14 * round(518 * h / w / 14)
    else:
        rw, rh = 14 * round(518 * w / h / 14), 518
    if min(rw, rh) < 14:
        raise ValueError("Extreme aspect ratio is unsuitable for reconstruction")
    x, y = (518 - rw) // 2, (518 - rh) // 2
    prepared = Image.new("RGB", (518, 518), "white")
    prepared.paste(image.resize((rw, rh), Image.Resampling.BICUBIC), (x, y))
    return prepared, {"upright_size": [w, h], "input_size": [518, 518], "scale_xy": [rw / w, rh / h], "offset_xy": [x, y], "content_box": [x, y, x + rw, y + rh], "upstream_loader": "pad on prealigned 518×518; checked for identity"}


def validate_predictions(depth, confidence, cameras, intrinsics, points, count):
    if depth.shape != (count, 518, 518, 1) or confidence.shape != (count, 518, 518) or cameras.shape != (count, 3, 4) or intrinsics.shape != (count, 3, 3) or points.shape != (count, 518, 518, 3):
        raise ValueError("Unexpected reconstruction output shapes")
    if not all(np.isfinite(a).all() for a in (depth, confidence, cameras, intrinsics, points)):
        raise ValueError("Non-finite reconstruction outputs")
    for camera, k in zip(cameras, intrinsics, strict=True):
        rotation = camera[:, :3]
        if not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-3) or not np.isclose(np.linalg.det(rotation), 1, atol=1e-3) or min(k[0, 0], k[1, 1]) <= 0:
            raise ValueError("Invalid reconstructed camera")


def observed_surface(points, depth, confidence, colors, masks, threshold):
    """Connect adjacent observed pixels, never bridge an occlusion or fill unseen space."""
    valid = np.asarray(masks, bool) & (depth[..., 0] > 0) & (confidence >= threshold)
    stride = max(1, math.ceil(math.sqrt(np.count_nonzero(valid) / 1_000_000)))
    xyz = points[:, ::stride, ::stride]
    z = depth[:, ::stride, ::stride, 0]
    rgb = colors[:, ::stride, ::stride]
    valid = valid[:, ::stride, ::stride] & np.isfinite(xyz).all(axis=-1)
    index = np.arange(z.size).reshape(z.shape)
    # Winding faces the source camera (OpenCV image y points down).
    a, b, c, d = index[:, :-1, :-1], index[:, :-1, 1:], index[:, 1:, :-1], index[:, 1:, 1:]
    faces = np.concatenate((np.stack((a, c, b), -1).reshape(-1, 3),
                            np.stack((b, c, d), -1).reshape(-1, 3)))
    faces = faces[valid.ravel()[faces].all(axis=1)]
    triangle_depth = z.ravel()[faces]
    # Depth discontinuities are silhouettes, not surfaces to stretch across.
    faces = faces[np.ptp(triangle_depth, axis=1) <= .03 * triangle_depth.min(axis=1)]
    if not len(faces):
        raise ValueError("Insufficient observed surface connectivity")
    used, remapped = np.unique(faces, return_inverse=True)
    return xyz.reshape(-1, 3)[used], rgb.reshape(-1, 3)[used], remapped.reshape(-1, 3)


def live_scene_local(context, scene, selected):
    settings = context["settings"]
    checkpoint = Path(settings.vggt_checkpoint)
    if not settings.vggt_commit or not settings.vggt_checkpoint_sha256 or not checkpoint.is_file():
        raise ExternalFailure("Provide a local VGGT_CHECKPOINT, its SHA256 and the installed VGGT_COMMIT; no automatic checkpoint download")
    import torch
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri
    from vggt.utils.geometry import unproject_depth_map_to_point_map
    installed = subprocess.run(["git", "-C", str(Path(getfile(VGGT)).resolve().parents[2]), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if installed != settings.vggt_commit:
        raise ExternalFailure("Installed VGGT commit differs from configured commit")
    import hashlib
    with checkpoint.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != settings.vggt_checkpoint_sha256:
        raise ExternalFailure("VGGT checkpoint checksum mismatch")
    if not torch.cuda.is_available():
        raise ExternalFailure("No compatible PyTorch GPU runtime is available")
    directory = context["directory"] / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    paths, transforms, masks = [], [], []
    for p in selected:
        with Image.open(db.asset_path(settings, p["path"])) as original:
            image, transform = aligned_input(original.convert("RGB"))
        path = directory / f"{p['id']}-vggt.png"
        temporary = path.with_suffix(".tmp")
        image.save(temporary, "PNG")
        os.replace(temporary, path)
        paths.append(str(path))
        transforms.append(transform)
        l, t, r, b = transform["content_box"]
        mask = np.zeros((518, 518), dtype=bool)
        mask[t:b, l:r] = True
        masks.append(mask)
    images = load_and_preprocess_images(paths, mode="pad")
    expected = np.stack([np.asarray(Image.open(path)).transpose(2, 0, 1) / 255. for path in paths])
    if tuple(images.shape) != expected.shape or not np.allclose(images.numpy(), expected, atol=1e-6):
        raise ExternalFailure("Upstream loader changed prepared pixels; update and verify transforms before inference")
    start = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    model = VGGT()
    model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True))
    model.eval().to("cuda")
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    with torch.inference_mode(), torch.autocast("cuda", dtype=dtype):
        predictions = model(images.to("cuda"))
        extrinsic, intrinsic = pose_encoding_to_extri_intri(predictions["pose_enc"], images.shape[-2:])
    torch.cuda.synchronize()
    depth = predictions["depth"][0].float().cpu().numpy()
    confidence = predictions["depth_conf"][0].float().cpu().numpy()
    cameras, intrinsics = extrinsic[0].float().cpu().numpy(), intrinsic[0].float().cpu().numpy()
    points = unproject_depth_map_to_point_map(depth, cameras, intrinsics)
    validate_predictions(depth, confidence, cameras, intrinsics, points, len(selected))
    finite_positive = (depth[..., 0] > 0) & np.stack(masks)
    if np.count_nonzero(finite_positive) < 1000:
        raise ValueError("Insufficient reconstructed surface support")
    # Keep a dense surface: only the least-confident fifth is culled, so rooms
    # do not render with large empty patches in the viewer.
    threshold = float(np.quantile(confidence[finite_positive], .5))
    display_threshold = float(np.quantile(confidence[finite_positive], .2))
    display, colors, faces = observed_surface(
        points, depth, confidence,
        (images.numpy().transpose(0, 2, 3, 1) * 255).astype(np.uint8), masks, display_threshold)
    if len(display) < 1000 or np.max(np.abs(display)) > 1e6:
        raise ValueError("Unusable reconstructed geometry")
    analysis_path = directory / f"{scene['id']}-analysis.npz"
    temporary = analysis_path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, depth=depth, confidence=confidence, extrinsics=cameras, intrinsics=intrinsics, world_points=points)
    os.replace(temporary, analysis_path)
    ply_path = directory / f"{scene['id']}-display.ply"
    write_ply(ply_path, display, colors, faces)
    properties = torch.cuda.get_device_properties(0)
    runtime = {"gpu": properties.name, "gpu_total_bytes": properties.total_memory, "torch": torch.__version__, "hip": torch.version.hip, "cuda": torch.version.cuda,
               "dtype": str(dtype), "peak_allocated_bytes": torch.cuda.max_memory_allocated(), "peak_reserved_bytes": torch.cuda.max_memory_reserved(), "free_total_bytes_after": list(torch.cuda.mem_get_info()), "elapsed_seconds": time.perf_counter() - start}
    del predictions, model
    torch.cuda.empty_cache()
    view_mode = "single_view_estimate" if len(selected) == 1 else "multiview"
    return {"id": scene["id"], "status": "ready", "provenance": "live", "reconstruction_kind": view_mode,
            "anchor_eligibility": "abstain_single_view" if len(selected) == 1 else "multiview_review_required",
            "checkpoint_sha256": checksum, "vggt_commit": installed, "runtime": runtime,
            "selected_photo_ids": [p["id"] for p in selected], "coordinate_convention": "OpenCV world-to-camera [R|t]; z depth; raw world units",
            "depth_interpretation": "camera_z", "raw_coordinate_units": "relative", "scale_state": "unknown", "scale_factor": None, "calibration": None,
            "analysis_path": analysis_path.relative_to(settings.data).as_posix(), "analysis_sha256": db.digest(analysis_path.read_bytes()), "ply_path": ply_path.relative_to(settings.data).as_posix(),
            "bounds": [display.min(axis=0).tolist(), display.max(axis=0).tolist()], "display_transform": np.diag([1., -1., -1., 1.]).tolist(),
            "images": [{"photo_id": p["id"], "tensor_index": i, "transform": transforms[i], "intrinsics": intrinsics[i].tolist(), "world_to_camera": cameras[i].tolist(), "depth_asset": "analysis.npz:depth", "confidence_asset": "analysis.npz:confidence"} for i, p in enumerate(selected)],
            "cameras": [np.linalg.inv(np.vstack([e, [0, 0, 0, 1]]))[:3, 3].tolist() for e in cameras], "measurements": [], "confidence_threshold": threshold,
            "display_geometry": "observed_surface_mesh", "display_confidence_threshold": display_threshold,
            "display_vertices": len(display), "display_faces": len(faces)}


def live_scene(context, scene, selected):
    """Run CUDA inference in the isolated VGGT environment, never in the API venv."""
    settings = context["settings"]
    checkpoint = Path(settings.vggt_checkpoint)
    python = Path(settings.vggt_python)
    if not settings.vggt_commit or not settings.vggt_checkpoint_sha256 or not checkpoint.is_file():
        raise ExternalFailure("Provide a local VGGT_CHECKPOINT, its SHA256 and the installed VGGT_COMMIT; no automatic checkpoint download")
    if not python.is_file():
        raise ExternalFailure("VGGT_PYTHON must point to the isolated model-cache/vggt-env Python runtime")
    request_directory = context["directory"] / "stages" / "reconstruct" / "gpu-requests"
    request_directory.mkdir(parents=True, exist_ok=True)
    request_path = request_directory / f"{scene['id']}.json"
    request = {"data": str(settings.data.resolve()), "directory": str(context["directory"].resolve()),
               "scene": {"id": scene["id"]},
               "photos": [{key: photo[key] for key in ("id", "path", "scene_id", "role")} for photo in selected],
               "vggt": {"checkpoint": str(checkpoint.resolve()), "checkpoint_sha256": settings.vggt_checkpoint_sha256,
                        "commit": settings.vggt_commit, "timeout_seconds": settings.vggt_timeout_seconds}}
    request_path.write_text(db.dumps(request))
    secret_markers = ("TOKEN", "API_KEY", "SECRET", "PASSWORD", "CREDENTIAL")
    child_env = {key: value for key, value in os.environ.items()
                 if not any(marker in key.upper() for marker in secret_markers)}
    child_env["PYTHONNOUSERSITE"] = "1"
    source = Path(__file__).resolve().parents[4]
    vggt_source = source / "model-cache" / "vggt"
    child_env.pop("PYTHONPATH", None)
    child_env.pop("PYTHONHOME", None)
    child_env.pop("PYTHONSTARTUP", None)
    child_env.pop("VIRTUAL_ENV", None)
    child_env["PYTHONPATH"] = os.pathsep.join([str(source / "backend" / "src"), str(vggt_source)])
    try:
        result = subprocess.run([str(python), "-m", "step_wise.pipeline.vggt_worker", str(request_path)], cwd=source,
                                capture_output=True, text=True, timeout=settings.vggt_timeout_seconds,
                                check=False, env=child_env)
    except subprocess.TimeoutExpired:
        raise ExternalFailure(f"VGGT exceeded its configured {settings.vggt_timeout_seconds}s timeout; no implicit retry") from None
    except OSError:
        raise ExternalFailure("Isolated VGGT Python runtime could not be started") from None
    if result.returncode:
        try:
            error = json.loads(result.stderr.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            error = {}
        category, message = error.get("error_category"), error.get("message")
        if category == "OutOfMemoryError":
            raise ExternalFailure("CUDA ran out of memory; create an explicit smaller frame selection")
        if message and isinstance(message, str) and len(message) <= 300:
            raise ExternalFailure(message)
        raise ExternalFailure("Isolated VGGT worker failed; verify the pinned CUDA runtime and capture")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ExternalFailure("Isolated VGGT worker returned invalid JSON") from None
    if output.get("id") != scene["id"] or output.get("status") != "ready" or output.get("checkpoint_sha256") != settings.vggt_checkpoint_sha256 or output.get("vggt_commit") != settings.vggt_commit:
        raise ExternalFailure("Isolated VGGT result does not match the requested scene or pinned model")
    expected_kind = "single_view_estimate" if len(selected) == 1 else "multiview"
    expected_anchor = "abstain_single_view" if len(selected) == 1 else "multiview_review_required"
    runtime = output.get("runtime", {})
    if (output.get("reconstruction_kind") != expected_kind or output.get("anchor_eligibility") != expected_anchor
            or output.get("scale_state") != "unknown" or output.get("scale_factor") is not None
            or output.get("selected_photo_ids") != [photo["id"] for photo in selected]
            or len(output.get("images", [])) != len(selected) or not runtime.get("gpu") or not runtime.get("cuda") or runtime.get("hip") is not None
            or not isinstance(runtime.get("elapsed_seconds"), (int, float)) or not math.isfinite(runtime["elapsed_seconds"]) or runtime["elapsed_seconds"] <= 0):
        raise ExternalFailure("Isolated VGGT result violates the CUDA, input-view or non-metric output contract")
    analysis = db.asset_path(settings, output.get("analysis_path", ""))
    ply = db.asset_path(settings, output.get("ply_path", ""))
    if not analysis.is_file() or db.digest(analysis.read_bytes()) != output.get("analysis_sha256") or not ply.is_file() or not ply.stat().st_size:
        raise ExternalFailure("Isolated VGGT artifacts failed integrity checks")
    return output


def live_reconstruct(context):
    scenes, warnings = [], []
    for scene in context["scenes"]:
        selected = [p for p in context["photos"] if p["scene_id"] == scene["id"] and p["role"] == "overview"]
        if not selected:
            reason = "Upload an overview photo for this room"
            scenes.append({"id": scene["id"], "status": "insufficient_coverage", "provenance": "live", "scale_state": "unknown", "scale_factor": None, "reason": reason})
            warnings.append({"scene_id": scene["id"], "message": reason})
            continue
        if len(selected) > context["settings"].frame_cap:
            reason = f"Room has {len(selected)} overview photos, above the configured cap of {context['settings'].frame_cap}; no implicit frame dropping"
            scenes.append({"id": scene["id"], "status": "failed", "provenance": "live", "scale_state": "unknown", "scale_factor": None, "reason": reason})
            warnings.append({"scene_id": scene["id"], "message": reason})
            continue
        try:
            scenes.append(live_scene(context, scene, selected))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ExternalFailure) else "Reconstruction failed; inspect the pinned runtime and capture. Image review remains available."
            scenes.append({"id": scene["id"], "status": "failed", "provenance": "live", "scale_state": "unknown", "scale_factor": None, "reason": reason, "error_category": type(exc).__name__})
            warnings.append({"scene_id": scene["id"], "message": reason})
    return {"scenes": scenes, "provenance": "live", "status": "degraded" if any(s["status"] != "ready" for s in scenes) else "succeeded", "warnings": warnings}


def write_ply(path, points, colors, faces=None):
    points = np.asarray(points, dtype=np.float32)
    colors = np.asarray(colors, dtype=np.uint8)
    if points.ndim != 2 or points.shape[1] != 3 or colors.shape != points.shape or not np.isfinite(points).all():
        raise ValueError("PLY requires finite N×3 points and colors")
    if faces is not None:
        faces = np.asarray(faces, dtype=np.int32)
        if faces.ndim != 2 or faces.shape[1] != 3 or np.any(faces < 0) or np.any(faces >= len(points)):
            raise ValueError("PLY faces must reference existing vertices")
    header = ("ply\nformat binary_little_endian 1.0\n" + f"element vertex {len(points)}\n" +
              "property float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\n")
    if faces is not None:
        header += f"element face {len(faces)}\nproperty list uchar int vertex_indices\n"
    header += "end_header\n"
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        stream.write(header.encode("ascii"))
        vertices = np.empty(len(points), dtype=[("xyz", "<f4", (3,)), ("rgb", "u1", (3,))])
        vertices["xyz"], vertices["rgb"] = points, colors
        stream.write(vertices.tobytes())
        if faces is not None:
            triangles = np.empty(len(faces), dtype=[("count", "u1"), ("indices", "<i4", (3,))])
            triangles["count"], triangles["indices"] = 3, faces
            stream.write(triangles.tobytes())
    os.replace(temporary, path)


def reconstruct(context):
    settings = context["settings"]
    if settings.recon_backend != "fixture":
        return live_reconstruct(context)
    scenes = []
    for scene in context["scenes"]:
        photos = [p["id"] for p in context["photos"] if p["scene_id"] == scene["id"] and p["role"] == "overview"]
        if not photos:
            scenes.append({"id": scene["id"], "status": "insufficient_coverage", "provenance": "fixture", "reason": "No overview photos"})
            continue
        x, z = np.meshgrid(np.linspace(-2, 2, 45), np.linspace(-1.5, 1.5, 35))
        floor = np.column_stack([x.ravel(), np.zeros(x.size), z.ravel()])
        xw, yw = np.meshgrid(np.linspace(-2, 2, 45), np.linspace(0, 2.5, 25))
        wall = np.column_stack([xw.ravel(), yw.ravel(), np.full(xw.size, -1.5)])
        points = np.concatenate([floor, wall])
        colors = np.tile([148, 172, 158], (len(points), 1))
        target = context["directory"] / "assets" / f"{scene['id']}-synthetic.ply"
        target.parent.mkdir(parents=True, exist_ok=True)
        write_ply(target, points, colors)
        scenes.append({"id": scene["id"], "status": "ready", "selected_photo_ids": photos, "provenance": "fixture",
                       "warning": "Synthetic viewer geometry; not reconstructed from photos", "coordinate_convention": "synthetic x-right y-up z-back; raw relative units",
                       "scale_state": "unknown", "scale_factor": None, "calibration": None,
                       "bounds": [points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                       "display_transform": np.eye(4).tolist(), "ply_path": target.relative_to(settings.data).as_posix(),
                       "cameras": [], "images": [], "measurements": []})
    return {"scenes": scenes, "provenance": "fixture"}
