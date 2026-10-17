import json
import os
import re
import struct
import subprocess
from pathlib import Path

from step_wise import db
from step_wise.pipeline.http import ExternalFailure


def _read_glb(path):
    blob = path.read_bytes()
    if len(blob) < 20:
        raise ExternalFailure("MPFB generated an invalid GLB")
    magic, version, size = struct.unpack_from("<4sII", blob)
    json_size, chunk_type = struct.unpack_from("<I4s", blob, 12)
    if magic != b"glTF" or version != 2 or size != len(blob) or chunk_type != b"JSON" or 20 + json_size > len(blob):
        raise ExternalFailure("MPFB generated an invalid GLB")
    try:
        document = json.loads(blob[20:20 + json_size].decode("utf-8").rstrip(" \t\r\n\0"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ExternalFailure("MPFB generated an unreadable GLB") from None
    names = {node.get("name", "") for node in document.get("nodes", [])}
    if not document.get("meshes") or not document.get("skins"):
        raise ExternalFailure("MPFB output is missing a mesh or humanoid rig")
    return document, names, json.dumps(document, ensure_ascii=False).casefold()


def avatar_payload(clinical_facts):
    """Create the minimum local Blender input; citations stay in the protected sidecar."""
    confirmed = [fact for fact in clinical_facts if fact.get("reviewer_confirmed")]
    display_traits = [
        {"field": fact["field"], "value": fact["value"], "status": fact["status"]}
        for fact in confirmed
        if fact["field"] in {"age_group", "mobility_aid"} and fact["status"] == "current"
    ]
    hip_facts = [fact for fact in confirmed if fact["field"] == "hip_condition" and fact["status"] in {"current", "historical"}]
    callouts = [{"id": f"profile-callout-{index + 1}", "field": fact["field"], "status": fact["status"],
                 "fact_index": confirmed.index(fact)} for index, fact in enumerate(hip_facts)]
    return {"display_traits": display_traits, "callouts": callouts}


def generate_glb(context):
    binary = context["settings"].blender_bin
    if not binary or not Path(binary).is_file():
        raise ExternalFailure("Set BLENDER_BIN to an isolated Blender 4.2+ installation with MPFB2 enabled")
    directory = context["directory"] / "avatar-work"
    directory.mkdir(parents=True, exist_ok=True)
    profile_path = directory / "profile.json"
    output_path = context["directory"] / "assets" / "avatar.glb"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    inputs = context["assessment_input"]
    functional_profile = {**inputs["profile"], "clinical_facts": inputs.get("clinical_facts", [])}
    blender_input = avatar_payload(inputs.get("clinical_facts", []))
    profile_path.write_text(db.dumps(blender_input))
    script = Path(__file__).with_name("mpfb_export.py")
    secret_markers = ("TOKEN", "API_KEY", "SECRET", "PASSWORD", "CREDENTIAL")
    child_env = {key: value for key, value in os.environ.items()
                 if not any(marker in key.upper() for marker in secret_markers)}
    child_env["PYTHONNOUSERSITE"] = "1"
    for key, value in (("HOME", context["settings"].blender_home),
                       ("BLENDER_USER_CONFIG", context["settings"].blender_user_config),
                       ("XDG_CONFIG_HOME", context["settings"].blender_xdg_config_home),
                       ("XDG_DATA_HOME", context["settings"].blender_xdg_data_home)):
        if value:
            child_env[key] = value
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10, check=False, env=child_env)
        match = re.search(r"Blender (\d+)\.(\d+)(?:\.(\d+))?", version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups()[:2])) < (4, 2):
            raise ExternalFailure("Avatar generation requires the configured Blender 4.2 or later binary")
        result = subprocess.run([binary, "--background", "--python", str(script), "--", str(profile_path), str(output_path)],
                                capture_output=True, text=True, timeout=context["settings"].avatar_timeout_seconds, check=False,
                                env=child_env)
    except subprocess.TimeoutExpired:
        raise ExternalFailure("MPFB avatar generation exceeded its configured timeout") from None
    except OSError:
        raise ExternalFailure("Configured Blender binary could not be started") from None
    finally:
        profile_path.unlink(missing_ok=True)
    if result.returncode or not output_path.is_file() or output_path.stat().st_size == 0:
        raise ExternalFailure("MPFB avatar generation failed; verify the isolated Blender and MPFB2 installation")
    gltf, node_names, gltf_json = _read_glb(output_path)
    mesh_names = {re.sub(r"\.\d{3}$", "", mesh.get("name", "")) for mesh in gltf.get("meshes", [])}
    required_assets = {"low-poly", "eyebrow001", "eyelashes01", "teeth_base", "tongue01",
                       "short01", "male_casualsuit01", "shoes01"}
    if not required_assets.issubset(mesh_names):
        raise ExternalFailure("MPFB output is missing required neutral avatar assets")
    if "Cube" in node_names:
        raise ExternalFailure("MPFB output contains Blender's default cube")
    required_callouts = {callout["id"] for callout in blender_input["callouts"]}
    if not required_callouts.issubset(node_names):
        raise ExternalFailure("MPFB output is missing a profile callout")
    wants_walker = any(trait["field"] == "mobility_aid" and trait["value"].casefold().strip() in {"walker", "rollator", "deambulatore"}
                       for trait in blender_input["display_traits"] if isinstance(trait.get("value"), str))
    if wants_walker and not any("walker" in name.casefold() for name in node_names):
        raise ExternalFailure("MPFB output is missing the confirmed mobility aid")
    for fact in functional_profile.get("clinical_facts", []):
        for private_text in (fact.get("quote"), fact.get("source_document_id"),
                             fact.get("value") if fact.get("field") in {"hip_condition", "relevant_history"} else None):
            if isinstance(private_text, str) and len(private_text.strip()) >= 5 and private_text.strip().casefold() in gltf_json:
                raise ExternalFailure("MPFB output contains clinical source text")
    manifest = {"status": "succeeded", "provenance": "live", "format": "glb", "path": output_path.relative_to(context["settings"].data).as_posix(),
                "sha256": db.digest(output_path.read_bytes()), "profile_sha256": db.digest(db.dumps(functional_profile).encode()),
                "generator": "MPFB2/Blender", "blender_version": match.group(0).removeprefix("Blender "),
                "mpfb_version": re.search(r"STEP_WISE_MPFB_VERSION=([0-9.]+)", result.stdout).group(1)
                    if re.search(r"STEP_WISE_MPFB_VERSION=([0-9.]+)", result.stdout) else "version-unreported",
                "illustrative_only": True, "clinical_text_embedded": False,
                "mesh_count": len(gltf.get("meshes", [])), "rigged": bool(gltf.get("skins")),
                "asset_pack_sha256": context["settings"].mpfb_asset_pack_sha256 or "unreported"}
    sidecar = context["directory"] / "assets" / "avatar-profile.json"
    facts = [fact for fact in functional_profile.get("clinical_facts", []) if fact.get("reviewer_confirmed")]
    callouts = blender_input["callouts"]
    sidecar.write_text(db.dumps({"schema_version": 1, "functional_profile": context["assessment_input"]["profile"],
                                 "confirmed_facts": facts, "callouts": callouts, "avatar": manifest}))
    manifest["profile_json_path"] = sidecar.relative_to(context["settings"].data).as_posix()
    manifest["profile_json_sha256"] = db.digest(sidecar.read_bytes())
    return manifest
