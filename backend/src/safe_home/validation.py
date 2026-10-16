from safe_home.models import AssessmentOutput, Hazard


def validate_hazard(hazard: Hazard, photos, scenes, guidance, profile, measurements):
    for evidence in hazard.evidence:
        photo = photos.get(evidence.photo_id)
        if photo is None:
            raise ValueError("Unknown or cross-assessment evidence photo")
        if evidence.box:
            _, _, r, b = evidence.box
            if r > photo["payload"]["width"] or b > photo["payload"]["height"]:
                raise ValueError("Evidence box exceeds upright image")
    if any(g not in guidance or guidance[g]["category"] != hazard.category for g in hazard.guidance_ids):
        raise ValueError("Unknown or incompatible guidance")
    if any(not profile.get(field) for field in hazard.profile_fields):
        raise ValueError("Unsupported profile reference")
    if any(m not in measurements for m in hazard.measurement_ids):
        raise ValueError("Untrusted measurement reference")
    evidence_scenes = {photos[e.photo_id]["scene_id"] for e in hazard.evidence}
    for mid in hazard.measurement_ids:
        m = measurements[mid]
        if m["scene_id"] not in evidence_scenes or m["validation_status"] != "validated":
            raise ValueError("Measurement is not validated for this scene")
    if hazard.anchor:
        a = hazard.anchor
        scene = scenes.get(a.scene_id)
        if scene is None or scene.get("status") != "ready" or a.scene_id not in evidence_scenes:
            raise ValueError("Anchor scene is unavailable or incompatible")
        if any(p not in photos or photos[p]["scene_id"] != a.scene_id for p in a.source_photo_ids):
            raise ValueError("Anchor support belongs to another scene")
        bounds = scene.get("bounds")
        if not bounds or any(v < bounds[0][i] or v > bounds[1][i] for i, v in enumerate(a.position_world)):
            raise ValueError("Anchor lies outside reconstructed scene")


def validate_output(output: AssessmentOutput, context, scenes, guidance):
    photos = {p["id"]: p for p in context["photos"]}
    profile = context["assessment_input"]["profile"]
    for hazard in output.hazards:
        validate_hazard(hazard, photos, scenes, guidance, profile, {})
    for coverage in output.coverage:
        if coverage.photo_id not in photos or coverage.category not in {g["category"] for g in guidance.values()}:
            raise ValueError("Unknown coverage reference")
    return output
