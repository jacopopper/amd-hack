# Actual Jev calls on the pitch photos

Two live calls to `jev-1.14.0` on 17 October 2026 used the exact open-ended
question, image preparation, and response interpretation in the current backend.
Only metadata-stripped images and the two general questions were sent; no profile,
filenames, pitch annotations, or expected target labels were sent.

Open [the visual gallery](index.html) to see the actual boxes and raw JSON links.

This is the frozen **original generic-prompt baseline**. The later
[optimized prompts and before/after results](optimization/index.html) localize
both corridor rugs and the bathtub with substantially higher box confidence.

| Photo | Presence score | Box confidence | Backend presence gate | Latency |
| --- | ---: | ---: | --- | ---: |
| Corridor | 0.90 | 0.04 | present | 1.481 s |
| Bathroom | 0.70 | 0.06 | unknown; evidence suppressed | 0.454 s |

Visual inspection: the corridor box lies on a wall painting rather than the
foreground rug. The bathroom box is a thin vertical strip spanning part of the
wall, plumbing, and toilet. Neither box provides convincing hazard localization.
The current backend gates boxes only on the general presence score, so its
acceptance of the corridor box does not establish localization quality.

These are one-region, object-agnostic outputs. They do not reproduce the nine
hardcoded illustrative hotspots in the pitch, and they differ from the older
target-specific Jev evaluation. Scores are model outputs, not fall probabilities.
The two responses report 9,721 input tokens and 80 output tokens in total.

The exact original questions, responses and overlays are retained here. The
current `eval/jev/inspect_pitch.py` harness uses the optimized backend prompts
and writes to `optimization/objects-v2/` by default. It does not overwrite this
baseline. Source photo credits and links appear in the gallery; originals remain
under `pitch/assets/photos/`.
