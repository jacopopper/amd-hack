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

One capture was deliberately reduced to 39 accepted photos (EXIF/GPS-free) and committed under [`frontend/public/capture-data/`](../frontend/public/capture-data/) for the hackathon demo: the photos, the observed surface mesh, camera metadata, the polished splat cloud and the mock hazard findings. `room-polished.ply` (~110 MB) and `observed.ply` are stored with Git LFS; the unpolished `room.ply`, checkpoints and analysis arrays stay in the local `data/` directories.

`scripts/polish_capture_surfaces.py <capture>` fits planes to photo-backed floor and ceiling regions of `capture-analysis.npz`, moves near-plane Gaussians toward the fitted planes and squeezes their covariance along the plane normal (the cord and carpet evidence regions are protected). It writes `room-polished.ply` plus `surface-polish.json` (plane fits, RMS before/after, input/output SHA-256). Colors, opacity and spherical harmonics are untouched, and no holes are filled.

Known caveats for the committed capture:

- The two hazard findings are hand-placed demo marks with depth-estimated pin positions, not JEV outputs.
- Quaternion norms in both `room.ply` and `room-polished.ply` are inherited from gsplat training output and are not unit-normalized (an integrity check flags this); conversion libraries normalize on load.

`scripts/prepare_local_room_review.py <capture>` stages a capture into the frontend and regenerates `hazards.json`; committed repository assets take precedence when they already exist.
