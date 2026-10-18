# Live Jev runs on the demo case photos

Eight live calls to `jev-1.14.0` on 17 October 2026, one per photo of the
bundled demo case (`demo/capture`), using the exact production questions,
image preparation and evidence gates in
[`backend/src/step_wise/pipeline/jev.py`](../../../../backend/src/step_wise/pipeline/jev.py)
via [`scripts/detect_capture_hazards.py`](../../../../../scripts/detect_capture_hazards.py).
Only the metadata-stripped case photos and the six fixed visual-object question
pairs were sent; no profile, filenames, or expected targets were sent.

| Photo | Strongest target | Presence | Box confidence | Gate outcome |
| --- | --- | ---: | ---: | --- |
| IMG_7580 | rug_near | 0.84 | 0.61 | both gates passed; disagreed in 3D with the kitchen-rug anchor, so it backs no finding |
| IMG_7599 | floor_cable | 0.78 | 0.18 | presence gate passed; box below the 0.50 localization gate |
| IMG_7603 | rug_near | 0.98 | 0.88 | both gates passed; localizes the kitchen carpet |
| other photos | — | ≤ 0.37 | — | below the 0.75 presence gate; nothing flagged |

The demo case ([`backend/src/step_wise/capture_case.py`](../../../../backend/src/step_wise/capture_case.py))
builds its two findings from these recorded runs and every finding carries its
scores and a pointer to its raw record. The kitchen carpet is model-localized
(presence 0.98, box 0.88). The walkway cable was flagged on presence (0.78) but
its box scored below the automatic localization gate, so the case keeps it as a
reviewer-confirmed approximate pin and says so on the finding; the raw box is
preserved in `IMG_7599.json`.

`summary.json` lists per-photo status, latency and token usage (43,043 input
and 1,824 output tokens in total); each `IMG_*.json` keeps the exact questions,
response and candidates. Gates (presence ≥ 0.75, box ≥ 0.50) are the production
gates and are provisional and uncalibrated. These runs show the production
detection path executing on the case photos; they do not establish
open-ended hazard-detection accuracy.
