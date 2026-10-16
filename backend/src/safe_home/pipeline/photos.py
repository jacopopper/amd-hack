import io
import os
import warnings
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from safe_home import db


def decode(data, max_pixels=40_000_000):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            raw = Image.open(io.BytesIO(data))
            if raw.format not in {"JPEG", "PNG"}:
                raise ValueError("Only JPEG and PNG are supported")
            if raw.width * raw.height > max_pixels:
                raise ValueError("Image exceeds decoded pixel limit")
            raw_size = raw.size
            orientation = raw.getexif().get(274, 1)
            raw.load()
            upright = ImageOps.exif_transpose(raw).convert("RGB")
            # Recreate pixels to remove EXIF, ICC, comments and other metadata.
            clean = Image.frombytes("RGB", upright.size, upright.tobytes())
            return clean, {"raw_size": list(raw_size), "upright_size": list(clean.size), "exif_orientation_applied": orientation}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Invalid or oversized JPEG/PNG image") from None


def prepared_image(image):
    w, h = image.size
    scale = min(1., 1568 / max(w, h))
    rw, rh = max(1, round(w * scale)), max(1, round(h * scale))
    width, height = max(280, rw), max(280, rh)
    left, top = (width - rw) // 2, (height - rh) // 2
    result = Image.new("RGB", (width, height), "#eeeeee")
    result.paste(image.resize((rw, rh), Image.Resampling.LANCZOS), (left, top))
    return result, {"input_size": [width, height], "scale_xy": [rw / w, rh / h], "offset_xy": [left, top], "content_box": [left, top, left + rw, top + rh]}


def canonical_box(normalized_box, transform, upright_size):
    l, t, r, b = normalized_box
    if not all(np.isfinite(v) and 0 <= v <= 1 for v in normalized_box) or not (l < r and t < b):
        raise ValueError("Malformed normalized box")
    width, height = transform["input_size"]
    sx, sy = transform["scale_xy"]
    ox, oy = transform["offset_xy"]
    cl, ct, cr, cb = transform["content_box"]
    l, t, r, b = max(l * width, cl), max(t * height, ct), min(r * width, cr), min(b * height, cb)
    if l >= r or t >= b:
        return None  # entirely in padding
    w, h = upright_size
    return [max(0., (l - ox) / sx), max(0., (t - oy) / sy), min(float(w), (r - ox) / sx), min(float(h), (b - oy) / sy)]


def ingest(settings, aid, data, scene_label, role, expected_revision):
    if len(data) > settings.max_file_bytes:
        raise OverflowError("Photo exceeds file limit")
    image, metadata = decode(data, settings.max_pixels)
    photo_id, asset_id = db.uid(), db.uid()
    path = settings.data / "assessments" / aid / "uploads" / f"{asset_id}.jpg"
    metadata.update({"raw_sha256": db.digest(data), "width": image.width, "height": image.height})
    try:
        with db.connect(settings, write=True) as conn:
            db.editable(conn, aid, expected_revision)
            count = conn.execute("SELECT count(*) FROM photos WHERE assessment_id=? AND active=1", (aid,)).fetchone()[0]
            total = conn.execute("SELECT coalesce(sum(byte_size),0) FROM assets WHERE assessment_id=? AND scope='source'", (aid,)).fetchone()[0]
            if count >= settings.max_photos or total + len(data) > settings.max_assessment_bytes:
                raise OverflowError("Assessment media limit reached")
            path.parent.mkdir(parents=True, exist_ok=True)
            scene = conn.execute("SELECT id FROM scenes WHERE assessment_id=? AND label=?", (aid, scene_label)).fetchone()
            scene_id = scene[0] if scene else db.uid()
            if scene is None:
                conn.execute("INSERT INTO scenes(id,assessment_id,label) VALUES(?,?,?)", (scene_id, aid, scene_label))
            image.save(path, "JPEG", quality=95)
            content = path.read_bytes()
            metadata["upright_sha256"] = db.digest(content)
            conn.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?)", (asset_id, aid, path.relative_to(settings.data).as_posix(), "image/jpeg", len(content), db.digest(content), "upload", "source"))
            conn.execute("INSERT INTO photos(id,assessment_id,scene_id,asset_id,role,payload) VALUES(?,?,?,?,?,?)", (photo_id, aid, scene_id, asset_id, role, db.dumps(metadata)))
            db.invalidate(conn, aid)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return photo_id


def prepare(context):
    results, seen = [], set()
    for photo in context["photos"]:
        image, _ = decode(db.asset_path(context["settings"], photo["path"]).read_bytes())
        prepared, transform = prepared_image(image)
        directory = context["directory"] / "assets"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{photo['id']}-prepared.jpg"
        temporary = target.with_suffix(".tmp")
        prepared.save(temporary, "JPEG", quality=92)
        os.replace(temporary, target)
        thumb = image.copy()
        thumb.thumbnail((480, 480))
        thumb_path = directory / f"{photo['id']}-thumb.jpg"
        temporary = thumb_path.with_suffix(".tmp")
        thumb.save(temporary, "JPEG", quality=85)
        os.replace(temporary, thumb_path)
        grey = np.array(image.resize((128, 128)).convert("L"), dtype=float)
        sharpness = float(np.var(np.diff(grey, axis=0)) + np.var(np.diff(grey, axis=1)))
        flags = []
        checksum = photo["payload"]["raw_sha256"]
        if checksum in seen:
            flags.append("exact_duplicate")
        if sharpness < 15:
            flags.append("possible_blur")
        if min(image.size) < 280:
            flags.append("small_image")
        seen.add(checksum)
        results.append({"photo_id": photo["id"], "scene_id": photo["scene_id"], "role": photo["role"],
                        "upright_size": list(image.size), "transform": transform, "quality_flags": flags,
                        "prepared_path": target.relative_to(context["settings"].data).as_posix(),
                        "thumbnail_path": thumb_path.relative_to(context["settings"].data).as_posix(),
                        "sha256": db.digest(target.read_bytes()), "provenance": "derived"})
    return {"photos": results}
