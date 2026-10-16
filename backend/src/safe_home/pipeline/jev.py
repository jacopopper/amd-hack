import json
import base64
import math
import time

from safe_home import db
from safe_home.config import ROOT
from safe_home.models import Candidate, Evidence
from safe_home.pipeline.http import ExternalFailure, post_json
from safe_home.pipeline.photos import canonical_box


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"


def request_payload(image_bytes, entries):
    """The Jev boundary accepts pixels and public guidance only, never a profile/context."""
    reference = db.uid()
    questions = {}
    for entry in entries:
        category = entry["category"]
        questions[category + "_present"] = {"type": "noul", "instructions": f"Is {entry['question']} visibly present in `photo`? Judge only visible evidence."}
        questions[category + "_box"] = {"type": "box", "instructions": f"Smallest visible-extent box around one explicit target: {entry['question']}. This does not establish presence."}
        questions[category + "_coverage"] = {"type": "choice", "instructions": f"Can the relevant area for {entry['question']} be inspected in `photo`?", "criteria": {"visible": "The relevant area is visible.", "partial": "Only part of the relevant area can be inspected.", "obscured": "The area is hidden by other objects.", "out_of_frame": "The relevant area is outside the photo.", "unknown": "Coverage cannot be determined."}}
    questions["rug_attachment"] = {"type": "choice", "instructions": "For a visible rug, is underside attachment actually visible? A flat top cannot establish fixation.", "criteria": {"fixed": "Attachment is visibly present.", "loose": "The underside is exposed and visibly unattached.", "unknown": "Attachment cannot be inspected or no rug is visible."}}
    return {"model": MODEL, "state": {"photo": reference}, "assets": {reference: {"type": "image", "format": "jpg", "data_base64": base64.b64encode(image_bytes).decode()}}, "questions": questions}


def interpret(body, photo, prepared, entries):
    if body.get("model") != MODEL:
        raise ValueError("Unexpected Jev model version")
    answers = body["answers"]
    result = []
    for entry in entries:
        name = entry["category"]
        present, box, coverage = (answers[name + suffix] for suffix in ("_present", "_box", "_coverage"))
        if present.get("type") != "noul" or box.get("type") != "box" or coverage.get("type") != "choice":
            raise ValueError("Invalid Jev response types")
        score = present["noul"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid presence score")
        coords = [box["box"][k] for k in ("left", "top", "right", "bottom")]
        mapped = canonical_box(coords, prepared["transform"], prepared["upright_size"])
        presence = "present" if score >= .75 else "absent" if score <= .25 else "unknown"
        attachment = answers["rug_attachment"]["choice"] if name == "rug" else "unknown"
        candidate = Candidate(photo_id=photo["id"], category=name, question_id=entry["id"], presence=presence, coverage=coverage["choice"],
                              evidence=Evidence(photo_id=photo["id"], box=mapped) if mapped and presence == "present" else None,
                              attachment=attachment, producer=MODEL, provenance="live",
                              provider_response={"presence": present, "box": box, "coverage": coverage, "attachment": answers["rug_attachment"] if name == "rug" else None})
        result.append(candidate.model_dump(mode="json"))
    return result


def fixture(context):
    photo = next(p for p in context["photos"] if p["role"] == "overview")
    w, h = photo["payload"]["width"], photo["payload"]["height"]
    saved = json.loads((ROOT / "demo/fixtures/candidates.json").read_text())
    candidates = []
    for item in saved["candidates"]:
        box = item["box"]
        evidence = Evidence(photo_id=photo["id"], box=[box[0] * w, box[1] * h, box[2] * w, box[3] * h]) if box else None
        candidates.append(Candidate(photo_id=photo["id"], category=item["category"], question_id=item["category"],
                                    presence=item["presence"], coverage=item["coverage"], evidence=evidence,
                                    attachment=item["attachment"], producer="synthetic-contract-fixture-v1",
                                    provenance="fixture", provider_response=item).model_dump(mode="json"))
    return {"candidates": candidates, "provenance": "fixture", "warning": saved["warning"]}


def inspect(context):
    if context["settings"].gemma_backend == "fixture":
        if context["settings"].jev_enabled:
            raise ValueError("Fixture development cannot enable live Jev calls")
        return fixture(context)
    if not context["settings"].jev_enabled:
        return {"candidates": [], "provenance": "live", "status": "skipped", "warning": "Jev disabled; Gemma must perform the full checklist sweep"}
    if not context["assessment_input"]["external_processing_consent"]:
        raise ExternalFailure("External image processing was not acknowledged; Jev will not receive photos")
    if not context["settings"].jev_api_key:
        raise ExternalFailure("JEV_API_KEY is missing; disable Jev explicitly or configure credentials")
    prepared = {p["photo_id"]: p for p in context["outputs"]["prepare"]["photos"]}
    candidates, calls, errors = [], [], []
    for photo in context["photos"]:
        p = prepared[photo["id"]]
        payload = request_payload(db.asset_path(context["settings"], p["prepared_path"]).read_bytes(), list(context["guidance"].values()))
        start = time.perf_counter()
        try:
            body = post_json(ENDPOINT, payload, {"Authorization": "Bearer " + context["settings"].jev_api_key, "TypeSafe-Danger-Zone-Images": "true"}, timeout=(10, 90))
            candidates.extend(interpret(body, photo, p, list(context["guidance"].values())))
            calls.append({"photo_id": photo["id"], "image_sha256": p["sha256"], "questions": payload["questions"], "model": body["model"], "usage": body.get("usage"), "elapsed_seconds": time.perf_counter() - start})
        except (ExternalFailure, ValueError, KeyError, TypeError) as exc:
            # Continue with the required real Gemma checklist sweep, with visible degradation.
            errors.append({"photo_id": photo["id"], "status": "failed", "category": type(exc).__name__, "message": str(exc) if isinstance(exc, ExternalFailure) else "Jev response invalid; no automatic retry; Gemma must inspect this photo"})
    return {"candidates": candidates, "calls": calls, "errors": errors, "status": "degraded" if errors else "succeeded", "provenance": "live"}
