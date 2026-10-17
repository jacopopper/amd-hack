# Four-screenshot live backend artifacts

These files came from the experimental 17 October 2026 backend run using the four
[watermarked listing screenshots](../../images/listing-2026-10-17/). Local VGGT on an
RTX 5090 produced the room data; Jev and Hugging Face/Novita Gemma supplied live
analysis; MPFB2 generated the avatar. The report used a **fictional clinical
profile** and simulated review decisions. It remains an unreleased draft.

| File | Contents |
| --- | --- |
| `room.ply` | Colored, 90,650-vertex room point cloud for the 3D viewer. |
| `room-analysis.npz` | VGGT depth, confidence, camera, and point arrays for inspection. |
| `point-cloud-review.png` | Four rendered views of the saved point cloud. |
| `avatar.glb` | Rigged MPFB2 avatar from this run. |
| `report-draft.pdf` | Italian PDF draft with the fictional profile and source images. |
| `verification.json` | Passing backend run counts and identifiers. |

The point cloud is recognizable at room scale but sparse and uncalibrated. Its
metric scale is unknown, and the geometry stage placed **no 3D hazard anchors**.
The four rug findings remain photo-based and need human review. These files are
development evidence, not a measured room scan or clinical approval.

The full local attempt history, database, logs, model requests, and credentials
are excluded from this public demo bundle.
