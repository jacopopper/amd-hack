# Runtime readiness and implementation evidence

Recorded 16 October 2026. **No staged room capture or AMD host is available**, confirmed by the user during implementation. This is a development prototype; P0 is not complete.

| Capability | Current evidence / status |
|---|---|
| Host | Linux, Python 3.12.3, Node 22.22.3, npm 10.9.8; 62 GiB RAM, approximately 447 GiB available disk at initial inspection |
| AMD raw device access | No `/dev/kfd` or `/dev/dri`; no compatible AMD runtime available |
| AMD hosted endpoint | Not configured/validated for this app; no credential values inspected or published |
| Model access/licenses | Gemma/VGGT target only. Exact permitted checkpoints, revisions, licenses, ROCm/vLLM images and digests must be verified before download/execution |
| Gemma | Target `google/gemma-4-31B-it`; bounded image/JSON HTTP client and report calls implemented, offline request/reference tests passed; actual serving/inference pending |
| VGGT | Adapter implemented: explicit local checkpoint/commit validation, recorded preprocessing, camera/depth/confidence NPZ and binary PLY export. No GPU run has validated it |
| Jev | Pixel-only typed HTTP adapter implemented with presence/attribute gates and no ambiguous POST retry; disabled by default. Application live requests pending |
| Dataset | Two project-generated synthetic controls with frozen checksums. Real 12–20 view staged capture, marker and independent tape measurements pending |
| Point cloud | Synthetic binary colored PLY for viewer development; relative units, unknown metric scale, no automatic pins |
| CPU application | Authenticated API; local SQLite queue; bounded upload/preparation; labelled fixtures; review decisions/revisions; deterministic Italian HTML/PDF; family release/revocation; retention/deletion |
| Case runner | Isolated run/check/inspect/compare, all stage names, actual-input fingerprints, hashed reuse, independent-stage preservation, report reruns, revision-bound attestations and explicit frozen baselines. Actual manual inspection/real baseline remains pending |
| Containers | CPU API/worker build, health and fixture HTTP smoke passed. Base digests pinned after validation. Official checksum-verified Compose 5.6.0 was used from `/tmp/safe-home-tools/docker-compose`; the host has no installed Compose plugin. ROCm override passed configuration validation only, with nonexecuting placeholder variables; no GPU image is claimed tested |

The external boundary is explicit: Jev may receive prepared images only after assessment acknowledgement; the patient profile must never enter its payload. Gemma is intended to run on the internal AMD endpoint. No application live model call has been made. No five-minute target, centimeter accuracy, fall reduction, clinical accuracy or reviewer-time benefit has been established.

## Tests already exercised

- Twenty-two focused Python unittest checks passed on 16 October: fixture repeat/targeted reuse/corruption, deliberately failing expectation, finite boxes/presence gates, EXIF/padding/metadata checks, report meaning/ID rejection, input invalidation, review/release/deletion, stale writes, queue/auth/restart boundaries, bounded HTTP repair, pixel-only Jev payloads, real Gemma request shape via mocked transport, rotated camera math, configuration mismatch, concurrent review during rendering, baseline integrity, expired capabilities, escaped markup and blocked renderer fetches. No external inference was invoked by this suite.
- Production frontend typecheck/build passed with the npm lockfile. three.js is loaded on demand; its viewer bundle still exceeds Vite's 500 kB advisory threshold.
- The existing Jev `self-check` passed; historical experiment files remain unchanged.
- HTTP fixture smoke passed against a fresh local SQLite volume: create, upload, queued analysis, simulated decisions, deterministic draft/PDF, stale release rejection, new release and family/private-access checks.
- Headless browser smoke passed at 1440 px and 390 px: login/intake, multi-photo upload, canonical photo box, synthetic PLY, synthetic pin picking/pin-to-photo selection/pin removal, keyboard approve/edit/reject/defer, refresh persistence, PDF retrieval and forced WebGL fallback. Screenshots are in gitignored `data/browser-smoke/`. These are synthetic UI checks, not a real pin-placement check.
- Italian PDF rendering was inspected with Poppler. An eight-page stress test covers 1,990-character observations/actions and a 600-character unbroken string; every page was inspected without clipping/overlap. Oversized headings and priority ordering were corrected. The draft is saved as `data/validation/long-text.pdf`, is unreleased and uses simulated fixture review. No real clinical approval is implied.

Full CPU dependency versions are frozen in `requirements.lock`; exact JS versions are in `web/package-lock.json`. Main installed versions: FastAPI 0.143.3, Pydantic 2.14.1, NumPy 2.5.4, Pillow 12.3.0, requests 2.34.2, Jinja2 3.1.6, WeasyPrint 69.0, React 19.3.1, Vite 8.3.4, TypeScript 7.0.2, three.js 0.186.1. These are **CPU development** results, not validated GPU pins. Optional QA used Playwright 1.64.0 with an already installed isolated Chromium binary.

## Configuration names (never values)

`SAFE_HOME_DATA`, `SAFE_HOME_DATABASE`, `REVIEWER_USER`, `REVIEWER_PASSWORD`, `REVIEWER_IDENTITY`, `RECON_BACKEND`, `GEMMA_BACKEND`, `JEV_ENABLED`, `JEV_API_KEY`, `GEMMA_BASE_URL`, `GEMMA_MODEL`, `GEMMA_MODEL_REVISION`, `VGGT_CHECKPOINT`, `VGGT_COMMIT`, `VGGT_CHECKPOINT_SHA256`, `SAFE_HOME_WORKER_LOCK`, `SAFE_HOME_PORT`. GPU packaging also needs `ROCM_WORKER_BASE`, `VLLM_ROCM_IMAGE`, `VIDEO_GID`, `RENDER_GID` and optional `HF_TOKEN`. Supply verified immutable GPU image digests; none are preselected.

Fixture mode provisions no GPU. Live failures cannot silently become fixtures. Keys, auth headers, profile text and plaintext family tokens must remain absent from diagnostics. Uvicorn launch commands disable access logs; a hosted proxy must do the same or redact capability paths. Retention starts with a dry run. Use staged media and fictional profiles throughout.

## Next implementation gates

1. Obtain the real staged capture with permissions, overlap, marker, independent tape measurements and labels frozen before live calls. The synthetic room-001 case is not that capture.
2. Validate the live clients and VGGT adapter in the exact AMD runtime. Inspect prepared transforms, actual images/JSON requests, camera/depth outputs and every final pin. Keep failed scenes/image-only evidence visible.
3. Complete the GPU smoke script’s listed single-image/multi-image known-target, vLLM hardware-identity and co-resident memory gates. Current preflight reports blocked on this host. The ROCm recipe requires compatible externally supplied images; no successful image/commit has been invented.
4. Complete benchmark ablations, reviewed labels, warm/cold and memory observations, independent measurement error evaluation and dated provider/provisioned-time cost accounting. `scripts/benchmark.py` currently collects actual attempt denominators/stage outputs and reports pending quality/cost gates; it is not the completed evaluation.
5. Complete actual real-scene pin correction/manual baseline inspection, the hosted deployment log/retention audit, and the recorded submission demo. P1 calibration/measurement endpoints and P2 extensions remain pending.

## Verified CPU base images

- Python 3.12.3 slim Bookworm: `sha256:afc139a0a640942491ec481ad8dda10f2c5b753f5c969393b12480155fe15a63`.
- Node 22.22.3 Bookworm slim: `sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752`.

The CPU Compose build/fixture smoke used isolated project `safe-home-cpu-check`, loopback port 8012 and its own data volume. These CPU digests say nothing about ROCm compatibility. GPU smoke and benchmark collectors have saved blocked records under gitignored `data/gpu-smoke/` and `data/benchmarks/`; no benchmark accuracy or cost result exists.

## First beta handoff

The beta scope was frozen at the user’s request on 16 October: verified CPU workflow, review/release integrity, inspectable case tools and unvalidated live adapters. Real AMD execution, a captured room, a checked real baseline, full benchmark/ablation/cost results and submission evidence remain open. P1/P2 are deferred. This does not mark full P0 or the whole roadmap complete.

A live native worker was observed holding the main `data/worker.lock`; the case command refused concurrent work with exit 3, as designed. That worker was preserved. Restart an already running API/worker to load beta source changes. Isolated container verification uses its own named volume and never resets the main assessment database.
