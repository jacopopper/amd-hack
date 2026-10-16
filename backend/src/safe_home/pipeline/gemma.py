import base64
import json
import time
import re
from pathlib import Path

from safe_home import db
from safe_home.models import AssessmentOutput, Coverage, Hazard
from safe_home.pipeline.http import ExternalFailure, post_json
from safe_home.validation import validate_output


def complete(settings, schema, prompt, content, validate=None, audit_directory=None):
    """One request, with one bounded schema repair; transport errors are never retried."""
    payload = {"model": settings.gemma_model, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": content}],
               "temperature": 0, "max_tokens": 4096, "response_format": {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}}
    attempts = []
    for attempt in range(2):
        if audit_directory:
            audit_directory.mkdir(parents=True, exist_ok=True)
            (audit_directory / f"request-{attempt + 1}.json").write_text(db.dumps(payload))
        start = time.perf_counter()
        body = post_json(settings.gemma_base_url + "/chat/completions", payload)
        record = {"attempt": attempt + 1, "model": body.get("model"), "requested_revision": settings.gemma_revision, "usage": body.get("usage"), "elapsed_seconds": time.perf_counter() - start}
        try:
            if body.get("model") != settings.gemma_model:
                raise ValueError("Unexpected serving model")
            choice = body["choices"][0]
            # Do not persist or display reasoning/private model fields.
            if choice.get("finish_reason") != "stop" or choice["message"].get("tool_calls"):
                raise ValueError("Incomplete or tool-bearing model output")
            value = schema.model_validate_json(choice["message"]["content"])
            if validate:
                validate(value)
            record["status"] = "succeeded"
            attempts.append(record)
            if audit_directory:
                (audit_directory / f"response-{attempt + 1}.json").write_text(db.dumps({**record, "validated_output": value.model_dump(mode="json")}))
            return value, attempts
        except (ValueError, KeyError, TypeError, IndexError):
            record["status"] = "invalid_output"
            attempts.append(record)
            if audit_directory:
                (audit_directory / f"response-{attempt + 1}.json").write_text(db.dumps(record))
            if attempt:
                raise ExternalFailure("Gemma output failed validation after one bounded repair") from None
            # No untrusted model output or raw validation values are interpolated into instructions.
            payload["messages"].append({"role": "user", "content": "The previous final output failed structural/reference validation. Return the complete required schema using only supplied IDs and exact confirmed profile references; no tools, invented facts or dimensions."})
    raise AssertionError("unreachable")


def relevance(profile, fields):
    return "Profilo confermato: " + "; ".join(f"{name}={json.dumps(profile[name], ensure_ascii=False)}" for name in fields)


def live_assess(context):
    settings = context["settings"]
    if not settings.gemma_revision:
        raise ExternalFailure("GEMMA_MODEL_REVISION is required for reproducible live inference")
    prepared = context["outputs"]["prepare"]["photos"]
    scenes = {s["id"]: s for s in context["outputs"]["geometry"]["scenes"]}
    prompt = (Path(__file__).resolve().parents[1] / "prompts/assessment.txt").read_text()
    prompt += '\nFor profile_relevance return exactly "Profilo confermato: " followed by the selected profile_fields as name=JSON(value), joined by "; ". Only select nonempty supplied fields. Anchors must be null; the backend attaches only supported geometry. No measurement may be invented.'
    hazards, coverage, followups, calls = [], [], [], []
    for index in range(0, len(prepared), 4):
        batch = prepared[index:index + 4]
        ids = {p["photo_id"] for p in batch}
        metadata = {"profile": context["assessment_input"]["profile"], "photos": [{"photo_id": p["photo_id"], "upright_size": p["upright_size"], "input_transform": p["transform"], "role": p["role"]} for p in batch],
                    "candidates": [{key: value for key, value in c.items() if key != "provider_response"} for c in context["outputs"]["jev"]["candidates"] if c["photo_id"] in ids], "guidance": list(context["guidance"].values()), "measurements": [],
                    "coverage_warnings": context["outputs"]["geometry"]["warnings"]}
        content = [{"type": "text", "text": db.dumps(metadata)}]
        for p in batch:
            data = db.asset_path(settings, p["prepared_path"]).read_bytes()
            content.extend([{"type": "text", "text": "photo_id=" + p["photo_id"]}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}}])

        def validate(value):
            validate_output(value, context, scenes, context["guidance"])
            for h in value.hazards:
                if any(e.photo_id not in ids for e in h.evidence) or h.anchor is not None or h.profile_relevance != relevance(metadata["profile"], h.profile_fields):
                    raise ValueError("Unsupported evidence, anchor or profile statement")
                if h.suggested_action not in {context["guidance"][g]["action"] for g in h.guidance_ids} or re.search(r"[€$]|\b\d+(?:[.,]\d+)?\s*(?:m|mm|cm|metri|centimetri|millimetri|euro)\b", h.observation + " " + h.suggested_action, re.I):
                    raise ValueError("Unsupported action, price or dimension")
            if any(c.photo_id not in ids for c in value.coverage):
                raise ValueError("Coverage cites an image outside this request")

        value, attempts = complete(settings, AssessmentOutput, prompt, content, validate, context["directory"] / "stages/gemma/calls" / f"batch-{index // 4}")
        hazards.extend(value.hazards)
        coverage.extend(value.coverage)
        followups.extend(value.follow_up_requests)
        calls.append({"photo_ids": sorted(ids), "image_sha256": [p["sha256"] for p in batch], "attempts": attempts,
                      "validated_output": value.model_dump(mode="json"), "request_settings": {"temperature": 0, "max_tokens": 4096, "max_images": 4},
                      "prompt_sha256": db.digest(prompt.encode())})
    if not calls:
        raise ExternalFailure("No images were assessed")
    # Missing checklist coverage is explicit, including successful requests that omitted a category.
    covered = {(c.photo_id, c.category) for c in coverage}
    for p in prepared:
        for g in context["guidance"].values():
            if (p["photo_id"], g["category"]) not in covered:
                coverage.append(Coverage(photo_id=p["photo_id"], category=g["category"], coverage="unknown", note="Controllo non restituito dal modello; area non valutata."))
    # Preserve separate objects unless boxes overlap on the same source evidence.
    merged = []
    for h in hazards:
        duplicate = False
        for previous in merged:
            if previous.category != h.category:
                continue
            for a in previous.evidence:
                for b in h.evidence:
                    if a.photo_id == b.photo_id and a.box and b.box:
                        from safe_home.pipeline.geometry import box_overlap
                        if box_overlap(a.box, b.box) > .75 and previous.priority == h.priority and previous.suggested_action == h.suggested_action:
                            duplicate = True
        if not duplicate:
            merged.append(h)
    output = AssessmentOutput(hazards=merged, coverage=coverage, follow_up_requests=list(dict.fromkeys(followups)))
    for hazard in output.hazards:
        for proposed in context["outputs"]["geometry"]["anchors"]:
            for evidence in hazard.evidence:
                from safe_home.pipeline.geometry import box_overlap
                if hazard.category == proposed["category"] and evidence.photo_id == proposed["photo_id"] and evidence.box and box_overlap(evidence.box, proposed["evidence_box"]) >= .5:
                    from safe_home.models import Anchor
                    hazard.anchor = Anchor.model_validate(proposed["anchor"])
    validate_output(output, context, scenes, context["guidance"])
    return {**output.model_dump(mode="json"), "provenance": "live", "producer": settings.gemma_model, "model_revision": settings.gemma_revision, "calls": calls, "modes": settings.modes(), "warnings": context["outputs"]["jev"].get("errors", [])}


def assess(context):
    settings = context["settings"]
    if settings.gemma_backend != "fixture":
        return live_assess(context)
    candidates = context["outputs"]["jev"]["candidates"]
    scenes = {s["id"]: s for s in context["outputs"]["geometry"]["scenes"]}
    guidance = context["guidance"]
    hazards = []
    for candidate in candidates:
        category = candidate["category"]
        if candidate["presence"] == "absent":
            continue
        evidence = candidate["evidence"] or {"photo_id": candidate["photo_id"], "box": None}
        entry = next(g for g in guidance.values() if g["category"] == category)
        observation = {"rug": "Controllo sintetico: tappeto visibile, fissaggio sconosciuto.", "path": "Controllo sintetico: oggetto nel passaggio.", "lighting": "Percorso notturno fuori inquadratura: illuminazione da verificare.", "bathroom": "Bagno fuori inquadratura: supporti non valutabili."}[category]
        hazards.append(Hazard(category=category, observation=observation, profile_relevance="Esempio di sviluppo; rilevanza da verificare sul profilo confermato.",
                              profile_fields=["description"], priority="important" if category in {"rug", "path"} else "recommended",
                              evidence=[evidence], guidance_ids=[entry["id"]],
                              uncertainty="Fixture, non inferenza. Nessuna verifica di sicurezza o misura metrica.", suggested_action=entry["action"]))
    coverage = [Coverage(photo_id=p["id"], category=g["category"], coverage="unknown", note="Fixture: questo controllo non è stato eseguito da un modello.") for p in context["photos"] for g in guidance.values()]
    output = AssessmentOutput(hazards=hazards, coverage=coverage, follow_up_requests=["Acquisire immagini sovrapposte reali; verificare di persona le aree non visibili."])
    validate_output(output, context, scenes, guidance)
    return {**output.model_dump(mode="json"), "provenance": "fixture", "producer": "synthetic-contract-fixture-v1", "modes": settings.modes()}
