from step_wise.pipeline.http import ExternalFailure


def generate(context):
    settings = context["settings"]
    if settings.avatar_backend == "fixture":
        return {"status": "skipped", "provenance": "fixture", "reason": "Avatar generation is not simulated by the development fixture"}
    inputs = context["assessment_input"]
    if not inputs.get("profile_confirmed"):
        raise ExternalFailure("Confirm the functional profile before generating an avatar")
    from step_wise.pipeline.avatar_blender import generate_glb
    return generate_glb(context)
