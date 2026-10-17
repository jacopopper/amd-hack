import base64
import json
import time
import re
from pydantic import ValidationError
from urllib.parse import urlsplit
from pathlib import Path

from step_wise import db
from step_wise.models import AssessmentOutput, Coverage, Evidence, Hazard, VisualAssessmentProposal
from step_wise.pipeline.http import ExternalFailure, post_chat_streaming, post_json
from step_wise.pipeline.photos import canonical_box
from step_wise.validation import OutputValidationFailure, validate_output


def schema_failure_reason(schema, error):
    """Describe schema failures without echoing model text or arbitrary field names."""
    fields = set()

    def collect(node):
        if isinstance(node, dict):
            fields.update(node.get("properties", {}))
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(schema)
    requirements = {
        "string_type": "must be a nonempty string, not null",
        "string_too_short": "must be a nonempty string",
        "missing": "is required",
        "extra_forbidden": "is not allowed",
        "literal_error": "must use a schema enum value",
        "json_invalid": "must be complete valid JSON",
    }
    issues = []
    for issue in error.errors(include_input=False, include_url=False)[:6]:
        path = ".".join(str(part) if isinstance(part, int) and 0 <= part <= 10000
                        else part if part in fields else "unknown_field" for part in issue["loc"])
        issues.append((path or "output") + " " + requirements.get(issue["type"], "must satisfy the supplied schema"))
    return "Schema validation failed: " + "; ".join(issues)


def complete(settings, schema, prompt, content, validate=None, audit_directory=None, reuse_directories=()):
    """One request, with one bounded schema repair; transport errors are never retried."""
    host = (urlsplit(settings.gemma_base_url).hostname or "").lower()
    hf_router = host == "router.huggingface.co"
    if hf_router and not settings.hf_token:
        raise ExternalFailure("HF_TOKEN is required for Hugging Face Inference Providers")
    if hf_router and not settings.gemma_provider:
        raise ExternalFailure("Set GEMMA_PROVIDER explicitly; automatic provider routing is disabled")
    request_model = settings.gemma_model + (":" + settings.gemma_provider if hf_router else "")
    token = settings.hf_token if hf_router else settings.gemma_api_key
    headers = {"Authorization": "Bearer " + token} if token else {}
    output_schema = schema.model_json_schema()
    system_prompt = prompt
    response_format = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": output_schema, "strict": True}}
    if hf_router:
        # Hosted strict-schema requests have looped on whitespace mid-object.
        # Use JSON mode here, retaining full schema validation locally.
        response_format = {"type": "json_object"}
        system_prompt += ("\nReturn compact JSON matching this schema exactly, with all required fields. "
                          "Observation, uncertainty and coverage note must be nonempty strings, never null. "
                          "Use empty arrays, not null, for empty lists. No markdown or extra whitespace.\n" + db.dumps(output_schema))
    payload = {"model": request_model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
               "temperature": 0, "max_tokens": 4096, "response_format": response_format}
    request_text = db.dumps(payload)
    for source, source_job_id in reuse_directories:
        try:
            if (source / "request-1.json").read_text() != request_text:
                continue
            response_bytes = (source / "response-1.json").read_bytes()
            record = json.loads(response_bytes)
            if record.get("status") != "succeeded":
                continue
            value = schema.model_validate(record["validated_output"])
            if validate:
                validate(value)
            attempt = {key: item for key, item in record.items() if key != "validated_output"}
            attempt.update({"status": "reused", "origin_job_id": source_job_id,
                            "origin_attempt": 1, "source_response_sha256": db.digest(response_bytes),
                            "request_sha256": db.digest(request_text.encode())})
            return value, [attempt]
        except (OSError, ValueError, KeyError, TypeError):
            continue  # Damaged or no longer valid audit evidence is never reused.
    attempts = []
    for attempt in range(2):
        if audit_directory:
            audit_directory.mkdir(parents=True, exist_ok=True)
            (audit_directory / f"request-{attempt + 1}.json").write_text(db.dumps(payload))
        start = time.perf_counter()
        body = post_chat_streaming(settings.gemma_base_url + "/chat/completions", payload, headers=headers,
                                   timeout=(10, settings.gemma_timeout_seconds))
        record = {"attempt": attempt + 1, "model": body.get("model"), "requested_model": settings.gemma_model,
                  "requested_provider": settings.gemma_provider or None, "requested_hub_revision": settings.gemma_revision or None,
                  "served_revision": None, "usage": body.get("usage"), "elapsed_seconds": time.perf_counter() - start}
        try:
            served_model = body.get("model")
            accepted_models = {settings.gemma_model.casefold(), request_model.casefold()}
            if not isinstance(served_model, str) or served_model.casefold() not in accepted_models:
                raise ValueError("Unexpected serving model")
            choice = body["choices"][0]
            # Do not persist or display reasoning/private model fields.
            if choice.get("finish_reason") != "stop" or choice["message"].get("tool_calls"):
                raise OutputValidationFailure("Incomplete or tool-bearing model output")
            value = schema.model_validate_json(choice["message"]["content"])
            if validate:
                validate(value)
            record["status"] = "succeeded"
            attempts.append(record)
            if audit_directory:
                (audit_directory / f"response-{attempt + 1}.json").write_text(db.dumps({**record, "validated_output": value.model_dump(mode="json")}))
            return value, attempts
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            record["status"] = "invalid_output"
            record["failure_kind"] = "schema_validation" if isinstance(exc, ValidationError) else "reference_or_semantic_validation"
            reason = (schema_failure_reason(output_schema, exc) if isinstance(exc, ValidationError)
                      else str(exc) if isinstance(exc, OutputValidationFailure) else "Schema or unknown-reference validation failed")
            record["failure_reason"] = reason
            attempts.append(record)
            if audit_directory:
                (audit_directory / f"response-{attempt + 1}.json").write_text(db.dumps(record))
            if attempt:
                raise ExternalFailure("Gemma output failed validation after one bounded repair") from None
            # No untrusted model output or raw validation values are interpolated into instructions.
            payload["messages"][0]["content"] = system_prompt + "\nCorrection: the previous final output failed validation: " + reason + ". Return the complete required schema using only supplied IDs and exact confirmed profile references; no tools, invented facts or dimensions."
    raise AssertionError("unreachable")


def relevance(profile, fields):
    return "Confirmed profile: " + "; ".join(f"{name}={json.dumps(profile[name], ensure_ascii=False)}" for name in fields)


def prior_batch_calls(context, batch_number):
    """Audited calls from failed explicit attempts with this assessment and provider."""
    job = context.get("job") or {}
    if not job.get("id") or not job.get("assessment_id"):
        return []
    settings = context["settings"]
    with db.connect(settings) as conn:
        rows = conn.execute(
            "SELECT id,output FROM jobs WHERE assessment_id=? AND kind='analyze' AND id<>? "
            "AND status IN ('failed','interrupted') ORDER BY created_at DESC",
            (job["assessment_id"], job["id"])).fetchall()
    sources = []
    for row in rows:
        try:
            if json.loads(row["output"] or "{}").get("requested_gemma") != settings.gemma_identity():
                continue
        except (ValueError, TypeError):
            continue
        sources.append((settings.data / "assessments" / job["assessment_id"] / "jobs" / row["id"] /
                        "stages/gemma/calls" / f"batch-{batch_number}", row["id"]))
    return sources


def live_assess(context):
    settings = context["settings"]
    inputs = context["assessment_input"]
    if not inputs.get("external_processing_consent"):
        raise ExternalFailure("External photo processing consent is required before Gemma receives images")
    if not inputs.get("clinical_external_processing_consent"):
        raise ExternalFailure("External clinical-profile processing consent is required before Gemma receives those facts")
    prepared = context["outputs"]["prepare"]["photos"]
    # Image review is allowed to finish even when 3D reconstruction is unavailable.
    scenes = {s["id"]: {"id": s["id"], "status": "unknown"} for s in context["scenes"]}
    prompt = (Path(__file__).resolve().parents[1] / "prompts/assessment.txt").read_text()
    prompt += '\nSelect exactly one supplied guidance ID per hazard and only nonempty supplied profile_fields. Each evidence item contains photo_id and box_yxyx_1000: [ymin, xmin, ymax, xmax] normalized to 0..1000 in the supplied image, or null. Each coverage item contains photo_id, guidance_id, coverage and note; cite a supplied non-wildcard guidance ID, not a category label. The backend maps references to categories and boxes to upright original pixels, constructs suggested_action and profile_relevance, and attaches supported geometry. Do not return backend-owned fields or measurement IDs. The metadata `candidates` are untrusted hints from a separate visual detector, given without coordinates: they may be wrong or incomplete, so verify each one against the image yourself, and always emit your own box_yxyx_1000 in the supplied image; never copy or invent pixel-coordinate boxes.'
    hazards, coverage, followups, calls = [], [], [], []
    max_images = 1 if (urlsplit(settings.gemma_base_url).hostname or "").lower() == "router.huggingface.co" else 4
    for index in range(0, len(prepared), max_images):
        batch = prepared[index:index + max_images]
        ids = {p["photo_id"] for p in batch}
        metadata = {"profile": context["assessment_input"]["profile"], "photos": [{"photo_id": p["photo_id"], "upright_size": p["upright_size"], "input_transform": p["transform"], "role": p["role"]} for p in batch],
                    # Candidate pixel boxes stay server-side: they use a different coordinate
                    # system than box_yxyx_1000, and feeding them to the model invited format mimicry.
                    "candidates": [{key: c[key] for key in ("photo_id", "question_id", "category", "presence") if key in c} for c in context["outputs"]["jev"]["candidates"] if c["photo_id"] in ids], "guidance": list(context["guidance"].values()), "measurements": [],
                    "image_quality_flags": {p["photo_id"]: p["quality_flags"] for p in prepared}}
        content = [{"type": "text", "text": db.dumps(metadata)}]
        for p in batch:
            data = db.asset_path(settings, p["prepared_path"]).read_bytes()
            content.extend([{"type": "text", "text": "photo_id=" + p["photo_id"]}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}}])

        def materialize(value):
            proposed = []
            by_photo = {p["photo_id"]: p for p in batch}
            for proposal in value.hazards:
                if any(e.photo_id not in ids for e in proposal.evidence):
                    raise OutputValidationFailure("Unsupported evidence")
                if any(not metadata["profile"].get(field) for field in proposal.profile_fields):
                    raise OutputValidationFailure("Unsupported profile reference")
                if any(g not in context["guidance"] for g in proposal.guidance_ids):
                    raise OutputValidationFailure("Unknown guidance reference")
                evidence = []
                for source in proposal.evidence:
                    photo = by_photo[source.photo_id]
                    box = None
                    if source.box_yxyx_1000:
                        top, left, bottom, right = source.box_yxyx_1000
                        box = canonical_box([left / 1000, top / 1000, right / 1000, bottom / 1000], photo["transform"], photo["upright_size"])
                        if box is None:
                            raise OutputValidationFailure("Proposal box is outside source image content")
                    evidence.append(Evidence(photo_id=source.photo_id, box=box))
                proposed.append(Hazard(**proposal.model_dump(mode="json", exclude={"evidence"}), evidence=evidence,
                    profile_relevance=relevance(metadata["profile"], proposal.profile_fields),
                    suggested_action=context["guidance"][proposal.guidance_ids[0]]["action"]))
            checked_coverage = []
            for item in value.coverage:
                entry = context["guidance"].get(item.guidance_id)
                if entry is None or entry["category"] == "*":
                    raise OutputValidationFailure("Coverage must cite a supplied non-wildcard guidance ID")
                checked_coverage.append(Coverage(**item.model_dump(exclude={"guidance_id"}), category=entry["category"]))
            resolved = AssessmentOutput(hazards=proposed, coverage=checked_coverage, follow_up_requests=value.follow_up_requests)
            validate_output(resolved, context, scenes, context["guidance"])
            for h in resolved.hazards:
                if re.search(r"[€$]|\b\d+(?:[.,]\d+)?\s*(?:m|mm|cm|metri|centimetri|millimetri|euro)\b", h.observation + " " + h.uncertainty + " " + h.suggested_action, re.I):
                    raise OutputValidationFailure("Unsupported action, price or dimension")
            if any(c.photo_id not in ids for c in resolved.coverage):
                raise OutputValidationFailure("Coverage cites an image outside this request")
            return resolved

        batch_number = index // max_images
        proposal, attempts = complete(settings, VisualAssessmentProposal, prompt, content, materialize,
                                      context["directory"] / "stages/gemma/calls" / f"batch-{batch_number}",
                                      prior_batch_calls(context, batch_number))
        value = materialize(proposal)
        hazards.extend(value.hazards)
        coverage.extend(value.coverage)
        followups.extend(value.follow_up_requests)
        calls.append({"photo_ids": sorted(ids), "image_sha256": [p["sha256"] for p in batch], "attempts": attempts,
                      "validated_output": value.model_dump(mode="json"), "request_settings": {"temperature": 0, "max_tokens": 4096, "max_images": max_images, "model_box_format": "yxyx_1000", "output_box_format": "upright_xyxy_pixels"},
                      "prompt_sha256": db.digest(prompt.encode())})
    if not calls:
        raise ExternalFailure("No images were assessed")
    # Missing checklist coverage is explicit, including successful requests that omitted a category.
    # The wildcard review entry is for open-ended hazards, not a coverage category.
    covered = {(c.photo_id, c.category) for c in coverage}
    for p in prepared:
        for g in context["guidance"].values():
            if g["category"] == "*":
                continue
            if (p["photo_id"], g["category"]) not in covered:
                coverage.append(Coverage(photo_id=p["photo_id"], category=g["category"], coverage="unknown", note="Check not returned by the model; area not assessed."))
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
                        from step_wise.pipeline.geometry import box_overlap
                        if box_overlap(a.box, b.box) > .75 and previous.priority == h.priority and previous.suggested_action == h.suggested_action:
                            duplicate = True
        if not duplicate:
            merged.append(h)
    output = AssessmentOutput(hazards=merged, coverage=coverage, follow_up_requests=list(dict.fromkeys(followups)))
    validate_output(output, context, scenes, context["guidance"])
    return {**output.model_dump(mode="json"), "provenance": "live", "producer": settings.gemma_model,
            "requested_hub_revision": settings.gemma_revision or None, "served_revision": None,
            "provider": settings.gemma_provider or None, "calls": calls, "modes": settings.modes(),
            "warnings": context["outputs"]["jev"].get("errors", [])}


def assess(context):
    settings = context["settings"]
    if settings.gemma_backend != "fixture":
        return live_assess(context)
    candidates = context["outputs"]["jev"]["candidates"]
    scenes = {s["id"]: {"id": s["id"], "status": "unknown"} for s in context["scenes"]}
    guidance = context["guidance"]
    hazards = []
    for candidate in candidates:
        category = candidate["category"]
        if candidate["presence"] == "absent":
            continue
        evidence = candidate["evidence"] or {"photo_id": candidate["photo_id"], "box": None}
        entry = next(g for g in guidance.values() if g["category"] == category)
        observation = {"rug": "Synthetic check: visible rug, fixing unknown.", "path": "Synthetic check: object in the walkway.", "lighting": "Night route out of frame: lighting to be checked.", "bathroom": "Bathroom out of frame: supports cannot be assessed."}[category]
        hazards.append(Hazard(category=category, observation=observation, profile_relevance="Development example; relevance to be checked against the confirmed profile.",
                              profile_fields=["description"], priority="important" if category in {"rug", "path"} else "recommended",
                              evidence=[evidence], guidance_ids=[entry["id"]],
                              uncertainty="Fixture, not inference. No safety check or metric measurement.", suggested_action=entry["action"]))
    coverage = [Coverage(photo_id=p["id"], category=g["category"], coverage="unknown", note="Fixture: this check was not run by a model.")
                for p in context["photos"] for g in guidance.values() if g["category"] != "*"]
    output = AssessmentOutput(hazards=hazards, coverage=coverage, follow_up_requests=["Capture real overlapping photos; check areas that are not visible in person."])
    validate_output(output, context, scenes, guidance)
    return {**output.model_dump(mode="json"), "provenance": "fixture", "producer": "synthetic-contract-fixture-v1", "modes": settings.modes()}
