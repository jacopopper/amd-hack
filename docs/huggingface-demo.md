# Hugging Face reconstructed-room demo

Public Space: https://huggingface.co/spaces/jacopopper/step-wise-demo

Direct app: https://jacopopper-step-wise-demo.static.hf.space

Deployment revision: `9f99e2769a9b7ecec1c17af70e85eb088ff361d7` (public and RUNNING).

## Deployed content

The source was fast-forwarded to remote commit `8be9805b78109d0bb7e95fefaeafc165f8cd223a`. Overlapping local UI changes were resolved in favor of the remote English review interface. The prior local work remains in the stash named `Before remote-priority HF reconstruction deployment`.

The static deployment contains the **actual offline VGGT/Gaussian-splat reconstruction**, its interactive renderer, camera positions, two hazard pins recorded from live TypeSafe Jev runs, and eight photo-evidence images from the committed judge snapshot. The room was reconstructed from 39 photos. This replaces the earlier synthetic fixture demo.

- Model: `capture-data/room.ksplat`, 21,529,257 bytes.
- Model SHA-256: `300c50a89bc9b51e727ad6bf1a8cbbafae44a6b9e6aac69b3b3036a59e447c38`.
- Source snapshot: `demo/snapshot/case.json`.
- Snapshot SHA-256: `622e31cbddb37cd239124957eacd6e36cb7f4b56030c7b95453d8c01f94f7615`.
- Staged deployment: approximately 31.2 MB, including the renderer and license notices.

The patient profile is fictional. The rug and cable findings were flagged by live TypeSafe Jev runs recorded in the source repository (`pitch/jev-results/2026-10-17/room-capture/`): the rug is model-localized (presence 0.98, box 0.88), the cable was flagged on presence (0.78) and kept as a reviewer-confirmed approximate pin; metric scale is unvalidated. The app is read-only: navigation, orbit/zoom and photo inspection work, while uploads, live inference, saved reviews and report release are unavailable. No backend, patient database or credentials are published.

The patient card uses `frontend/public/room-cover-polished.png`, an AI-polished version of the supplied wide room screenshot, with interface overlays removed using built-in imagegen. This is a presentation cover, not evidence; the original photos and 3D reconstruction are unchanged. The exact edit prompt is recorded in `docs/room-cover-prompt.txt`.

## Rebuild and publish

```sh
.venv/bin/python scripts/export_hf_demo.py
NEXT_PUBLIC_STATIC_DEMO=true npm --prefix frontend run build
.venv/bin/python scripts/stage_hf_demo.py
.venv/bin/python scripts/deploy_hf_demo.py
```

The exporter clones the committed judge snapshot into a temporary database and exports its read API responses and evidence assets. It never reads the working patient database or model credentials. The build writes `frontend/.next-demo/`.

Staging copies the built interface and only the Git-tracked reconstruction files under `frontend/public/capture-data/`, verifying each against `HEAD`. It also includes `local-room-viewer.html`. Untracked local captures, source data, full-resolution PLYs, checkpoints, credentials and databases are excluded. Do not upload the whole source repository.

Deployment explicitly reads `HF_TOKEN` from `.env`, overriding any stale inherited token, scans the staged files for configured credentials, and uses the installed Hub SDK to upload. This avoids `hf` 1.8's incorrect attempt to recreate existing Static Spaces as Gradio. An atomic upload replaces the managed app directories and deletes stale synthetic snapshots, old capture pages and unused hashed bundles. It never selects paid compute or storage.

Native links and explicit `/pazienti/index.html?id=...` URLs keep navigation and refresh compatible with the HF static host. Normal backend-driven development mode is unchanged.

## Verification

```sh
npm --prefix frontend run lint
.venv/bin/python -m unittest tests.test_hf_demo tests.test_demo_snapshot
python3 -m http.server 3024 --bind 127.0.0.1 --directory dist/hf-space
# Another terminal:
.venv/bin/python scripts/smoke_hf_demo.py
# Public deployment:
.venv/bin/python scripts/smoke_hf_demo.py --base-url https://jacopopper-step-wise-demo.static.hf.space
```

The production build and lint passed, as did all three snapshot/export tests. The public model was downloaded independently and matched the SHA-256 above; obsolete synthetic assets were confirmed absent.

The build includes TypeScript validation. The browser test waits for the entire splat stream and a completed rendered frame, checks evidence navigation and disabled writes, reloads the patient URL, checks return links and the intake guard, and opens the mobile landing. It rejects failed HTTP responses, JavaScript errors, mutations and `/api/` calls.

Live browser verification passed on the deployed revision: the complete splat stream, a rendered frame, an orbit drag that changed the canvas, evidence navigation, read-only controls, refresh, return links and mobile landing. It observed 124 read requests with no failed requests, API calls or JavaScript errors. A separate logged-out check confirmed the HF page embeds the new review and reconstruction renderer.

The remote viewer deliberately switches to photos when WebGL is unavailable or a software-rendered frame exceeds 1.5 seconds. The 3D button can still reopen the loaded room. The local software-only test rendered the full room in about 10 seconds per sharp frame; this is not a hardware-accelerated performance measurement. The existing desktop-oriented review layout is retained.

## Rollback

Restore the known-good static artifact and upload it as a new Space revision. There is no mutable visitor state to migrate. No source Git commit or push was performed; the local branch was fast-forwarded to the remote and deployment adaptations remain uncommitted.
