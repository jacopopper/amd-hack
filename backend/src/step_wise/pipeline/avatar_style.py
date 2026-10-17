"""Avatar look shared by the API-side wrapper and the Blender script.

Pure Python on purpose: Blender loads this file by path and has no access to the step_wise package.
The figure is one fixed, friendly older-woman illustration. Reviewer-confirmed facts only decide whether the
walker is drawn, so the picture never shows an aid the record does not support.
"""

WALKER_AIDS = {"walker", "rollator", "deambulatore"}

# Flat sRGB colours: the pack's photographic textures looked noisy and inflated the GLB to ~18 MB.
PALETTE = {"skin": "#E2B59C", "hair": "#B9B9BE", "top": "#727A32", "bottom": "#4F8066", "shoes": "#6A4B86",
           "sock": "#E8E1D6", "teeth": "#F1ECE2", "tongue": "#B85C5C", "metal": "#8F949C", "grip": "#2B2F33",
           "callout": "#FF7A14"}
ASSETS = (("eyes", "low-poly", "Eyes"), ("eyebrows", "eyebrow001", "Eyebrows"), ("eyelashes", "eyelashes01", "Eyelashes"),
          ("tongue", "tongue01", "Tongue"), ("teeth", "teeth_base", "Teeth"), ("hair", "bob02", "Hair"),
          ("clothes", "female_elegantsuit01", "Clothes"), ("clothes", "shoes02", "Clothes"))


def select_style(display_traits):
    aid = next((trait.get("value") for trait in display_traits
                if trait.get("field") == "mobility_aid" and trait.get("status") == "current"), None)
    return {"age": .85, "gender": 0.0, "hair": "bob02", "garment": "female_elegantsuit01", "shoes": "shoes02",
            # The pack's skirt is cut for a neutral body; widen it so aged thighs do not poke through flat colours.
            "lower_flare": 1.06, "palette": dict(PALETTE), "assets": [list(asset) for asset in ASSETS],
            "walker": isinstance(aid, str) and aid.casefold().strip() in WALKER_AIDS}


def required_mesh_names(style):
    return {asset[1] for asset in style["assets"]}


def public_summary(style):
    """Non-clinical look tags that are safe to expose to the frontend."""
    return {"walker": style["walker"]}
