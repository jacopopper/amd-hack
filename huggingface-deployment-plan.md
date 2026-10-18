# Historical plan — superseded by the reconstructed-room deployment

The current deployment uses the actual reconstruction from remote commit `8be9805`, not the synthetic case described below. See [the current runbook](docs/huggingface-demo.md).

# Plan: Deploy the Step Wise interface demo to Hugging Face

Generated: 2026-10-17. Complexity: medium. Status: deployed publicly using the Static Space alternative. See docs/huggingface-demo.md for current deployment status.

## Scope and approach

Deploy a public, browsable demonstration of the existing interface with a bundled fictional case, photos, findings and 3D assets. No live analysis, GPU, external model calls, uploads or report release. Read-only behavior is the proposed interpretation of “web interface demo only”; interactive review writes would expand scope.

Use the existing Next.js static export served by FastAPI in a CPU Basic Docker Space. This preserves the relative `/api` requests and asset routes without rewriting the frontend data layer. Run one API process; no worker or Compose deployment. Use the HF token from the local `.env` for deployment authentication only, loading it without printing it, passing it on the command line, or including the file in uploads. Public visibility is confirmed. Proposed name: `<token-owner>/step-wise-demo`; verify identity and name availability during implementation.

Cost gate: current HF documentation says CPU Basic has no hourly hardware charge, but creating Docker Spaces requires a paid personal or organization plan. No paid upgrade is authorized. Verify account eligibility before creation; if unavailable, use the Static Space alternative below or obtain a budget decision.

## Repository findings

- `frontend/next.config.ts` already exports `frontend/out` in production; its localhost proxy is development-only.
- `frontend/src/lib/api.ts` and the landing page require API responses; uploading `out/` alone would produce a broken demo.
- `Dockerfile.api` already builds the frontend and serves it from FastAPI on port 8000.
- `backend/src/step_wise/api.py` initializes SQLite, serves assets and exported pages, and has no reviewer authentication.
- A `baseline.json` in the configured data directory already blocks mutation methods. The case tooling supports freezing a verified run.
- The CSP currently specifies `frame-ancestors 'self'`, blocking the Hugging Face page's embedded app.
- README references to `compose.demo.yaml`, `render.yaml`, `snapshot.py`, `DemoLanding` and demo-session endpoints do not match files/routes present in this checkout. Do not assume visitor isolation exists.
- Port 3014 could not be reached from the execution environment. Visual equivalence to the user's running instance remains an implementation check.

## Sprint 1: A reproducible, read-only local demo

Goal: browse the intended interface locally with no live credentials or model runtime.

### 1.1 Prepare a minimal demo bundle

Locations: new `demo/hf/`; existing `backend/src/step_wise/cases.py`, `demo.py`, and CLI.
Dependencies: none.

Generate a dedicated fixture run using the existing case tools, inspect it, satisfy the manual checks, and freeze it using the existing baseline mechanism. Include only its SQLite records and referenced assets; exclude unrelated local assessments, real personal information, access tokens and local paths. Validate relocation to a fresh data directory, including database asset paths and checksums. Keep fixture provenance visible. The default synthetic control case is available; richer visuals require a separately reviewed staged sample, not copying the working `data/` directory wholesale.

Acceptance/validation: the isolated API lists one sample and loads its evidence and geometry without a worker or network model call; baseline integrity passes after relocation. Snapshot creation must use a stopped writer or a consistent SQLite backup, not a live database-file copy that omits WAL contents.

### 1.2 Make demo behavior explicit

Locations: `backend/src/step_wise/config.py`, `api.py`; `frontend/src/app/page.tsx`, `frontend/src/components/PatientSpace.tsx`, `PreparePanel.tsx`, `ReleaseDialog.tsx` and affected controls.
Dependencies: 1.1.

Expose a small demo-mode flag to the UI. Label the sample as a frozen demonstration; retain navigation, search, evidence selection, room rotation and zoom. Hide or explain unavailable create, upload, analyze, edit, delete and release actions, including direct navigation to intake routes. Keep the server-side write block; do not rely on hidden buttons. Fail startup if demo mode is requested without the verified frozen bundle.

Acceptance/validation: browser navigation works; representative POST/PATCH/PUT/DELETE requests are rejected; a second visitor cannot alter the first visitor's view. Existing normal-mode behavior remains unchanged.

Sprint demo: open the frozen sample locally and explore the evidence and 3D view with all model credentials unset.

## Sprint 2: Package and verify a Docker Space

Goal: a clean container reproduces Sprint 1.

### 2.1 Add deployment packaging

Locations: new `deploy/huggingface/Dockerfile`, `deploy/huggingface/README.md`, `deploy/huggingface/start.sh`; `.dockerignore` if needed.
Dependencies: Sprint 1.

Reuse the pinned Node/Python stages from `Dockerfile.api`; build with `npm ci` and `npm run build`. Preserve the Python project layout used by `Settings.ROOT`. Include the frozen bundle and guidance. Check backend imports for PDF-library requirements and include the system libraries from `Dockerfile.worker` if required even when reports are precomputed. Run as UID 1000 with an owned writable runtime directory. At startup copy the immutable seed into that directory and point both `STEP_WISE_DATA` and `STEP_WISE_DATABASE` at it. Start only Uvicorn on `0.0.0.0:7860`. Explicitly set fixture backends and disable Jev; no HF/model keys in the runtime.

The staged Space repository must have `Dockerfile` and the Space README at its root. README front matter: `title: Step Wise Demo`, `sdk: docker`, `app_port: 7860`, `license: mit`. Preserve asset-specific attribution separately.

Acceptance/validation: build from an allowlisted clean deployment directory and run with no `.env`, host mounts or GPU. `/api/health`, the landing page, sample API and all referenced assets succeed. Restart regenerates the same sample. No durable storage is required for this frozen demo.

### 2.2 Permit the intended embed

Locations: `backend/src/step_wise/api.py`, configuration and focused backend tests.
Dependencies: 1.2; can be developed alongside 2.1.

Make permitted frame ancestors configurable for the demo and add the verified HF parent origin, normally `https://huggingface.co`, while retaining self. Preserve existing script hashes and other CSP directives. Keep relative API URLs; verify HTTPS/proxy behavior without globally relaxing origin checks.

Acceptance/validation: the direct app URL and HF embedded view both render and navigate; an unrelated parent origin remains blocked.

### 2.3 Validate the release candidate

Locations: existing frontend checks; new focused demo tests and browser smoke coverage as appropriate.
Dependencies: 2.1, 2.2.

Run frontend typecheck, lint and production build; relevant backend tests for static serving, mutation blocking and baseline integrity. In the container, inspect desktop/mobile layouts, direct page refreshes, photos, GLB/point-cloud assets, fonts and any prebuilt report links. Verify browser network traffic has no localhost or external inference requests. The existing mutation-heavy end-to-end smoke is not suitable unchanged for a read-only demo.

Sprint demo: clean container launched on a local port, with browsing and restart checks passing.

## Sprint 3: Publish and smoke-test

Goal: a working public Space with a reproducible update path.

### 3.1 Resolve deployment identity and prepare upload

Locations: deployment staging directory outside the source tree; optional small staging script under `scripts/`.
Dependencies: Sprint 2.

Load only `HF_TOKEN` from `.env` into the deployment process without displaying it. Use authenticated identity discovery, verify write access, account eligibility and availability of `step-wise-demo`. Check installed `hf --help` before selecting CLI syntax. Prepare an explicit file allowlist: deployment Dockerfile/README, dependency locks, necessary frontend/backend/guidance sources, and reviewed demo bundle. Exclude `.env*`, `.aws`, `.codex`, `.agents`, `.git`, working data, model-cache, virtual environments, node_modules and unrelated pitch/evaluation artifacts. Docker ignore rules do not protect Hub uploads.

Acceptance/validation: review the staged manifest and size, scan for accidental credentials, and confirm public redistribution rights for every included asset. No secrets are needed in the Space settings.

### 3.2 Create and upload the public Space

Dependencies: 3.1; requires execution authorization beyond this planning request.

Create a public Docker Space on CPU Basic and upload only the staged directory. Typical commands, subject to installed CLI help:

```sh
hf repos create OWNER/step-wise-demo --type space --space-sdk docker --public
hf upload OWNER/step-wise-demo STAGED_DIRECTORY . --type space
```

Use the token through the process environment, never a literal token argument. Record the deployed revision and inspect build/runtime logs. Do not commit or push the source repository without explicit confirmation, per `/home/jacopodardini/AGENTS.md`.

### 3.3 Verify public access and document updates

Locations: `docs/huggingface-demo.md` (new).
Dependencies: 3.2.

Test logged out on the Space page and its direct `hf.space` URL, on desktop and mobile. Repeat sample navigation, 3D rendering, blocked writes and restart checks. Record source revision, asset provenance, deployment command and restart behavior. Explain that CPU Basic may sleep when idle and the first visit may wait for startup.

Sprint acceptance: public access works without credentials, no visitor writes or inference calls occur, and a fresh restart restores the same sample.

## Static Space alternative: no paid account plan

If Docker creation is unavailable or a fully static deployment is preferred, keep the same demo scope but replace runtime API reads with a small build-time-selected fixture adapter in `frontend/src/lib/api.ts`. Export the exact read responses consumed by the UI into versioned JSON and copy reviewed assets into `frontend/public/demo/`, rewriting asset URLs. Audit direct fetches outside the shared client. Unsupported mutations remain disabled; optional simulated interactions would live only in browser memory. Build and validate `frontend/out`, then publish a Static Space with `sdk: static` and `app_file: index.html`. Verify exported route navigation and refresh on the static host; use supported paths instead of assuming FastAPI's extension fallback exists. No Python runtime, SQLite service, worker or token is shipped. This route avoids Docker's account-plan requirement but adds frontend adapter work.

## Risks, decisions and rollback

- Public visibility means both code and bundled demo data are public. Publish only the reviewed fixture/staged bundle.
- The exact case shown at localhost may differ from the checked-in synthetic case. Compare screenshots during implementation before claiming visual parity.
- Read-only UI scope is an assumption, not confirmation that review/release interactions must be removed forever. If those are desired, prefer browser-local simulation for this demo over exposing the unauthenticated write API.
- Token-owner identity, plan eligibility and final Space slug remain to be resolved. Never print the token while checking them.
- Frozen DB initialization and asset paths must survive a fresh container and seed relocation; test this before upload.
- Reset-on-restart storage is intentional. A future writable app needs a separate persistence and access-control design.
- Rollback: retain the previous deployment manifest/revision; restore its files in a new Space commit and rebuild. Pause the Space if the public release fails validation. Source application changes stay isolated from deployment data.

## Official references checked

Context7 is unavailable in this session; official Hugging Face documentation was consulted directly.

- [Docker Spaces: ports, user permissions, runtime and storage](https://huggingface.co/docs/hub/spaces-sdks-docker)
- [Spaces overview: account eligibility, CPU Basic, visibility and sleep](https://huggingface.co/docs/hub/spaces-overview)
- [Space README configuration](https://huggingface.co/docs/hub/spaces-config-reference)
