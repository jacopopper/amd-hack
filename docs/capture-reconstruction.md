# Local photo capture reconstruction

`scripts/reconstruct_capture.py` is a local-only experimental VGGT → gsplat runner. It creates a separate private room viewer without changing the assessment database, existing room presentation, hazard anchors or metric calibration.

## Environments

- Preparation: project Python environment with `pillow-heif>=1,<2`, Pillow and NumPy.
- VGGT: the existing pinned source/checkpoint and isolated `model-cache/vggt-env` CUDA runtime.
- Splat training/evaluation: that CUDA environment with gsplat 1.5.3, SciPy, and a working gsplat CUDA extension. A source install also needs a compatible CUDA compiler and Ninja.
- Viewer dependencies, installed separately from the application:

```bash
npm install --prefix model-cache/capture-viewer \
  @mkkellogg/gaussian-splats-3d@0.4.7 three@0.170.0 --no-audit --no-fund
```

## Run

Use a **new output directory** and an explicit ordered list of accepted photo paths. Do not pass excluded photos to preparation or reuse an earlier reconstruction that contained them.

```bash
OUT="$PWD/data/live-tests/my-room"
.venv/bin/python scripts/reconstruct_capture.py prepare --output "$OUT" \
  --images /absolute/path/accepted-01.HEIC /absolute/path/accepted-02.HEIC

export PYTHONPATH="$PWD/backend/src:$PWD/model-cache/vggt"
model-cache/vggt-env/bin/python3 scripts/reconstruct_capture.py vggt --output "$OUT"
model-cache/vggt-env/bin/python3 scripts/reconstruct_capture.py train --output "$OUT" \
  --steps 9000 --resolution 1036
model-cache/vggt-env/bin/python3 scripts/reconstruct_capture.py evaluate --output "$OUT"
.venv/bin/python scripts/reconstruct_capture.py viewer --output "$OUT"
.venv/bin/python -m http.server 8014 --bind 127.0.0.1 --directory "$OUT"
```

The local runner explicitly allows the supplied view count for this isolated run. It does not raise the application's upload or reconstruction limits. A GPU out-of-memory failure is a failure, not permission to silently discard photos.

## Artifacts and limits

- `capture.json`: source/canonical image hashes and upright sizes. Originals are not modified; prepared copies contain no EXIF/GPS metadata.
- `scene.json`, `assets/*-analysis.npz`: all selected VGGT cameras, depth, confidence and relative world coordinates.
- `room.ply`, `room.splat`, `checkpoint.pt`: trained Gaussian splats and checkpoint. The PLY retains degree-2 spherical harmonics; the simpler `.splat` format retains only base color.
- `observed.ply`: confidence/depth-discontinuity-filtered observed triangle surfaces, not a watertight completion.
- `verification.json`, `evaluation/`: per-training-view image-fit/opacity checks and interpolated-camera renders. Opacity is **not** geometric accuracy or proof that all room surfaces were observed.
- `index.html`, `vendor/`: network-independent viewer with photo navigation, Gaussian/mesh/point modes and downloads. Third-party viewer licenses are copied alongside it.

Optional `masks.json` maps photo IDs to excluded polygons in normalized upright-photo coordinates. This masks pixels only, not entire photos. Whole-photo exclusions must happen before preparation and VGGT.

All outputs, including photos and screenshots, stay under gitignored `data/`. No hosted model calls, clinical processing, report release, scene completion or metric validation occur. VGGT's existing research-checkpoint license still applies.

## Committed review capture and surface polish

One capture was deliberately reduced to 39 accepted photos (EXIF/GPS-free) for the hackathon demo. The repository keeps only what the demo uses:

- [`frontend/public/capture-data/`](../frontend/public/capture-data/): `room.ksplat` (the polished splats compressed to ~20 MB), `viewpoints.json` (cameras), `hazards.json` (mock findings) and the vendored viewer. [`local-room-viewer.html`](../frontend/public/local-room-viewer.html) streams them inside the case's room view.
- [`demo/capture/`](../demo/capture/): the 8 photos the demo case shows, plus `training.json` and `surface-polish.json`. `python -m step_wise.cli import-capture` builds the reviewable case from them, downscaling the photos to 1600 px; `export-demo` then freezes it as the judge snapshot.

The full-resolution sources (`room-polished.ply` ~110 MB, `observed.ply`, all 39 photos) are no longer in the tree; recover them from commit `fc8228b` (`git checkout fc8228b -- frontend/public/capture-data`). The unpolished `room.ply`, checkpoints and analysis arrays stay in the local `data/` directories.

`scripts/polish_capture_surfaces.py <capture>` fits planes to photo-backed floor and ceiling regions of `capture-analysis.npz`, moves near-plane Gaussians toward the fitted planes and squeezes their covariance along the plane normal (the cord and carpet evidence regions are protected). It writes `room-polished.ply` plus `surface-polish.json` (plane fits, RMS before/after, input/output SHA-256). Colors, opacity and spherical harmonics are untouched, and no holes are filled.

`node scripts/compress_splats.mjs <room-polished.ply> <room.ksplat>` writes the compressed copy: 16-bit positions, 8-bit degree-1 harmonics, and no Gaussians below opacity 5/255, which the viewer already discarded. On the review views it is visually indistinguishable from the 110 MB PLY.

Known caveats for the committed capture:

- The two hazard findings are hand-placed demo marks with depth-estimated pin positions, not JEV outputs.
- Quaternion norms in both `room.ply` and `room-polished.ply` are inherited from gsplat training output and are not unit-normalized (an integrity check flags this); conversion libraries normalize on load.

`scripts/prepare_local_room_review.py <capture>` stages a capture into `frontend/public/capture-data` and `demo/capture`, compresses its splats and regenerates `hazards.json`; committed repository assets take precedence when they already exist.
