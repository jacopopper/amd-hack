# Jev image evaluation

## Results — 2026-10-16

**Jev looks useful for generating visual candidates in the hackathon prototype.** Both passes matched all 42 shared binary labels (7 positives, 35 negatives per pass). This is a small, selected set with simple presence questions; it is not a 100% hazard-detection accuracy claim.

| Check | First pass | Second pass |
|---|---:|---:|
| Shared presence labels at threshold 0.5 | 42/42 | 42/42 |
| Box-specific presence labels | 11/11 | 11/11 |
| Coverage / rug-attachment choices, excluding one ambiguous label | 11/13 | 11/13 |
| Present-target boxes with IoU ≥ 0.5 | 8/8 | 8/8 |
| Absent-target boxes suppressed by presence gate | 3/3 | 3/3 |

Across 14 successful requests, median wall-clock latency was **1.61 seconds** (range **0.70–2.12 s**). Returned input usage was **46,176 tokens**, approximately **$0.00194** at the published rate. These are remote API timings including request overhead; they do not measure AMD inference. Every response identified itself as `jev-1.14.0`.

First-pass mean box IoU was **0.839** against approximate reference boxes. The thin alarm cord was weakest: **0.558 IoU**, confidence **0.48**; its prediction extended into the alarm unit above the requested cord. Large objects and rugs were easier. Boxes used scene-specific target descriptions, including distinguishing repeated grab bars; this does not test automatic discovery of every object.

Two failures repeated: the cable photo and wooden staircase received `rug_attachment=unknown` despite `rug=false` at the chosen threshold. The corresponding Choice confidences were **0.73** and **0.65**. Questions are evaluated independently, so dependent results need explicit gating in application code. The attachment choice should be ignored when no rug candidate is present.

The cropped control correctly returned `floor_view=out_of_frame`; the cluttered room returned `obscured`. For the ambiguous cable surface (excluded from choice accuracy before calling the API), `floor_view` changed between passes, with confidence **0.25 → 0.14**. All 42 shared binary decisions and all scored choice decisions stayed unchanged. Both actual rugs received `attachment=unknown`, appropriately avoiding an unsupported claim about their undersides.

All three absent-target Box questions still returned rectangles. Their separate presence values were **0.02, 0.02, and 0.04**, so the gate suppressed them. Never treat a returned box as proof that a hazard exists.

**Recommended integration:** fixed visual checklist → presence/coverage gates → candidate boxes → Gemma checks the original evidence and patient relevance → reviewed 3D markers and report. Ask for another angle when coverage is missing. Preserve “attachment unknown” rather than converting rug presence into “loose rug.” Benchmark on real overlapping phone captures before locking the demo; missing supports, subtle trip hazards, repeated objects without tailored descriptions, poor lighting, and 3D anchoring remain untested.

Artifacts: [annotated gallery](results-2026-10-16/gallery.html), [machine-readable summary](results-2026-10-16/summary.json), and individual raw responses in the same directory. The gallery intentionally shows absent-target boxes for debugging; those should not appear in the product.

## Method

This smoke test evaluates `jev-1.14.0` for Step Wise using six public Wikimedia photos and one crop of the same tidy-room photo. No patient information is transmitted. The test has 42 shared binary observations, 14 shared choice questions (13 scored; one ambiguous floor-view label excluded), and 11 target-presence/box pairs per pass. Two passes check repeatability on identical image bytes and prompts.

Labels and approximate visible-extent reference boxes were recorded in `manifest.json` before any model calls. They come from a single assistant visual inspection, not clinician annotation. Labels, image titles, filenames, source URLs, and reference coordinates are not included in API requests. The manifest includes image checksums and the script snapshots it into the results directory.

The binary threshold is 0.5, chosen before running. Box overlap is intersection-over-union (IoU), with 0.5 used as a descriptive cutoff. Thin or occluded targets are sensitive to small coordinate differences. These are smoke-test counts, not evidence of clinical accuracy, calibrated confidence, or performance on a representative home dataset. A visible rug does not establish that it is unsecured, and an absent visible feature does not establish that the whole room is safe.

## Run

Requires Python 3, `requests`, and Pillow (already installed in this workspace). The key is read from `JEV_API_KEY` in the environment or the repository `.env`; the file is ignored by Git. The script sends credentials only to `https://api.typesafe.ai/v1/systemone`, disables redirects, and does not save authorization headers.

```bash
python3 eval/jev/evaluate.py self-check
python3 eval/jev/evaluate.py run
python3 eval/jev/evaluate.py summarize
```

`run` makes up to 14 API calls and incurs API usage. Existing successful result files are reused, so rerunning resumes the same experiment. For a fresh run, provide a new `--output` directory. Use `--only bathroom` or `--repeats 1` to reduce calls. HTTP failures stop the run without automatic retries. `summarize` is offline and regenerates `summary.json`, annotated images, and `gallery.html`. Pink boxes show raw Jev outputs even for absent targets; green boxes show reference annotations. A production UI must suppress boxes for absent targets.

## API details checked on 2026-10-16

- Pin `jev-1.14.0`; `jev-latest` currently points to 1.13, which lacks Box support.
- Include `TypeSafe-Danger-Zone-Images: true`. Images and boxes are explicitly experimental.
- PNG/JPEG only, each side 280–1568 pixels. Normalize EXIF orientation and strip metadata before sending. The stored evaluation images already satisfy these limits.
- Send one UUID image reference in `state.photo`, and refer to `photo` from questions. Repeating the UUID in instructions injects additional image copies.
- A Box always returns coordinates, even for an absent target. Pair it with an independent presence question. A presence score is not a patient's probability of falling.
- Published input price is $0.042 per million tokens; output tokens are free. Cost estimates use returned input-token usage and are not billing receipts.

Official references: [Images](https://docs.typesafe.ai/images), [Box](https://docs.typesafe.ai/primitives/box), [Noul](https://docs.typesafe.ai/primitives/noul), [Choice](https://docs.typesafe.ai/primitives/choice), [API](https://docs.typesafe.ai/api), [Models and pricing](https://docs.typesafe.ai/models).

## Image attribution

`sources.json` records each source page, author, license, download URL, checksum, and transformations. Resized/cropped/annotated image derivatives retain their source license; these image licenses do not automatically apply to the evaluation code. The generated gallery also displays full image credits. Images were resized and re-encoded without EXIF metadata. The extra control is the upper 960×320-pixel crop of `staged_room.jpg`.
