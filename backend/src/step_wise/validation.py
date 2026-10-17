import math

from step_wise.models import AssessmentOutput, Hazard


class OutputValidationFailure(ValueError):
    """Fixed validation messages only; safe for redacted model audit and repair."""


def validate_hazard(hazard: Hazard, photos, scenes, guidance, profile, measurements):
    for evidence in hazard.evidence:
        photo = photos.get(evidence.photo_id)
        if photo is None:
            raise OutputValidationFailure("Unknown or cross-assessment evidence photo")
        if evidence.box:
            _, _, r, b = evidence.box
            if r > photo["payload"]["width"] or b > photo["payload"]["height"]:
                raise OutputValidationFailure("Evidence box exceeds upright image")
    if any(g not in guidance for g in hazard.guidance_ids):
        raise OutputValidationFailure("Unknown or incompatible guidance")
    applicable = [g for g in hazard.guidance_ids if guidance[g]["category"] == hazard.category]
    fallback = [g for g in hazard.guidance_ids if guidance[g]["category"] == "*"]
    if fallback:
        if fallback != ["home-hazard-review"] or len(hazard.guidance_ids) != 1 or applicable:
            raise OutputValidationFailure("Generic hazard review cannot be mixed with category-specific guidance")
    elif len(applicable) != len(hazard.guidance_ids):
        raise OutputValidationFailure("Hazard needs matching category guidance or the generic review entry")
    if any(not profile.get(field) for field in hazard.profile_fields):
        raise OutputValidationFailure("Unsupported profile reference")
    if any(m not in measurements for m in hazard.measurement_ids):
        raise OutputValidationFailure("Untrusted measurement reference")
    evidence_scenes = {photos[e.photo_id]["scene_id"] for e in hazard.evidence}
    for mid in hazard.measurement_ids:
        m = measurements[mid]
        if m.get("scene_id") not in evidence_scenes or m.get("validation_status") != "validated":
            raise OutputValidationFailure("Measurement is not validated for this scene")
    if hazard.anchor:
        a = hazard.anchor
        scene = scenes.get(a.scene_id)
        if scene is None or scene.get("status") != "ready" or a.scene_id not in evidence_scenes:
            raise OutputValidationFailure("Anchor scene is unavailable or incompatible")
        if any(p not in photos or photos[p]["scene_id"] != a.scene_id for p in a.source_photo_ids):
            raise OutputValidationFailure("Anchor support belongs to another scene")
        if len(a.source_photo_ids) != len(set(a.source_photo_ids)):
            raise OutputValidationFailure("Anchor support must use distinct photos")
        if a.method == "synthetic_fixture" and scene.get("provenance") != "fixture":
            raise OutputValidationFailure("Synthetic anchors require a fixture scene")
        if a.method == "foreground_multiview":
            if (scene.get("provenance") not in {"live", "replay"} or scene.get("reconstruction_kind") != "multiview"
                    or scene.get("anchor_eligibility") == "abstain_single_view" or len(a.source_photo_ids) < 2):
                raise OutputValidationFailure("Foreground anchors require live multiview support")
            images = {image["photo_id"]: image for image in scene.get("images", [])}
            selected = set(scene.get("selected_photo_ids", []))
            centers = []
            for photo_id in a.source_photo_ids:
                image = images.get(photo_id)
                boxes = [e.box for e in hazard.evidence if e.photo_id == photo_id and e.box]
                if image is None or photo_id not in selected or not boxes:
                    raise OutputValidationFailure("Foreground anchor needs boxed reconstruction evidence in each support photo")
                camera, intrinsics = image["world_to_camera"], image["intrinsics"]
                camera_point = [sum(camera[i][j] * a.position_world[j] for j in range(3)) + camera[i][3] for i in range(3)]
                projected = [sum(intrinsics[i][j] * camera_point[j] for j in range(3)) for i in range(3)]
                if not all(math.isfinite(value) for value in projected) or camera_point[2] <= 0 or projected[2] <= 0:
                    raise OutputValidationFailure("Foreground anchor projects behind a support camera")
                u, v = projected[0] / projected[2], projected[1] / projected[2]
                transform = image["transform"]
                sx, sy = transform["scale_xy"]
                ox, oy = transform["offset_xy"]
                x, y = (u - ox) / sx, (v - oy) / sy
                if not any(l <= x <= r and t <= y <= b for l, t, r, b in boxes):
                    raise OutputValidationFailure("Foreground anchor does not project into its evidence box")
                centers.append([-sum(camera[j][i] * camera[j][3] for j in range(3)) for i in range(3)])
            if max(math.dist(left, right) for left in centers for right in centers) < .02 * max(
                    math.dist(a.position_world, center) for center in centers):
                raise OutputValidationFailure("Foreground anchors need distinct camera baselines")
        bounds = scene.get("bounds")
        if not bounds or any(v < bounds[0][i] or v > bounds[1][i] for i, v in enumerate(a.position_world)):
            raise OutputValidationFailure("Anchor lies outside reconstructed scene")


def validate_output(output: AssessmentOutput, context, scenes, guidance):
    photos = {p["id"]: p for p in context["photos"]}
    profile = context["assessment_input"]["profile"]
    for hazard in output.hazards:
        validate_hazard(hazard, photos, scenes, guidance, profile, context.get("measurements", {}))
        if hazard.suggested_action not in {guidance[g]["action"] for g in hazard.guidance_ids}:
            raise OutputValidationFailure("Generated suggested action must match its cited guidance exactly")
    for coverage in output.coverage:
        if coverage.photo_id not in photos or coverage.category not in {g["category"] for g in guidance.values() if g["category"] != "*"}:
            raise OutputValidationFailure("Unknown coverage reference")
    return output
