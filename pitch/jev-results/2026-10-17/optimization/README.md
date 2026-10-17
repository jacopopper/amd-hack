# Jev prompt optimization on the two pitch photos

The same six visual-object question pairs are sent to both photos: nearest rug,
second/farthest rug, footstool, floor cable, bathtub, and safety grab bar. Each
pair asks independently whether the object is visible and where its visible
extent lies. No patient profile, filename, expected labels, or pitch hotspots
are sent. This replaces the vague single-box question about an unspecified hazard.

Open the [before/after comparison](index.html), [clean result gallery](objects-v2/index.html),
or [independent repeat](objects-v2-repeat/index.html). All trial questions and
responses are retained, including the unsuccessful spatial rewording in `spatial-v1/`.

| Visible object | Presence, first → repeat | Box confidence, first → repeat | Box IoU between runs |
| --- | --- | --- | ---: |
| Corridor: nearest rug | 0.99 → 0.99 | 0.70 → 0.68 | 0.990 |
| Corridor: farther rug | 0.95 → 0.95 | 0.91 → 0.89 | 0.941 |
| Bathroom: bathtub | 0.97 → 0.98 | 0.84 → 0.77 | 1.000 |

Only presence ≥ 0.75 **and** box confidence ≥ 0.50 produces displayed evidence.
These are provisional, uncalibrated thresholds. The corridor footstool remains
uncertain (0.42 → 0.45 presence) despite a plausible box, so it is withheld.
Absent targets still returned boxes; their presence gate suppresses those boxes.
Nothing is boxed as a “missing grab bar.” Localization of a visible bathtub is
separate from deciding whether it is hazardous or what action is appropriate.

There were six optimization calls: two spatial prompts, two object prompts, and
two repeats. Only the two requested pitch photos were sent. The repeated run is
a consistency check on the same images, not held-out accuracy validation.
`summary.json` records the exact numbers. Gemma still performs the full-image
hazard sweep; this fixed Jev inventory cannot discover every type of hazard.

The chosen request is implemented in `backend/src/step_wise/prompts/jev-visual.json`
and interpreted by `pipeline/jev.py`. The previous `jev-general.txt` is retained
only as historical prompt evidence. The approach follows TypeSafe's documented
[one-target Box with a paired presence question](https://docs.typesafe.ai/primitives/box).

```bash
# Reuses the completed matching run; use --output with a fresh path for new calls.
.venv/bin/python eval/jev/inspect_pitch.py run
# Offline regeneration, with no API calls.
.venv/bin/python eval/jev/inspect_pitch.py render
```
