import json
import base64
import math
import time
from pathlib import Path

from step_wise import db
from step_wise.config import ROOT
from step_wise.models import Candidate, Evidence
from step_wise.pipeline.http import ExternalFailure, post_json
from step_wise.pipeline.photos import canonical_box


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"


def request_payload(image_bytes, entries):
    """Use the approved open-ended question without sending profile or checklist context."""
    reference = db.uid()
    question = (Path(__file__).resolve().parents[1] / "prompts/jev-general.txt").read_text().strip()
    questions = {
        "hazard_review": {"type": "noul", "instructions": question},
        "hazard_region": {"type": "box", "instructions": "If the answer is yes, draw a tight box around the single visible feature that most warrants review. If none does, return an empty box."},
    }
    return {"model": MODEL, "state": {"photo": reference}, "assets": {reference: {"type": "image", "format": "jpg", "data_base64": base64.b64encode(image_bytes).decode()}}, "questions": questions}


def interpret(body, photo, prepared, entries):
    if body.get("model") != MODEL:
        raise ValueError("Unexpected Jev model version")
    answers = body["answers"]
    present, box = answers["hazard_review"], answers["hazard_region"]
    if present.get("type") != "noul" or box.get("type") != "box":
        raise ValueError("Invalid Jev response types")
    score = present["noul"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Invalid presence score")
    coords = [box["box"][k] for k in ("left", "top", "right", "bottom")]
    mapped = None if coords == [0, 0, 0, 0] else canonical_box(coords, prepared["transform"], prepared["upright_size"])
    presence = "present" if score >= .75 else "absent" if score <= .25 else "unknown"
    candidate = Candidate(photo_id=photo["id"], category="potential_home_safety_hazard", question_id="general-home-safety",
                          presence=presence, coverage="unknown",
                          evidence=Evidence(photo_id=photo["id"], box=mapped) if mapped and presence == "present" else None,
                          producer=MODEL, provenance="live", provider_response={"presence": present, "box": box})
    return [candidate.model_dump(mode="json")]


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
