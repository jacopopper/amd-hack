# Step Wise

**First beta · 16 October 2026.** The verified beta uses CPU fixtures with a complete upload → review → Italian web/PDF release workflow. Live adapters are experimental; real capture, AMD validation and P1/P2 work remain later milestones.

**Local 3D backend · 17 October 2026.** The [local implementation plan](docs/piano-3d-locale.md) is implemented with 74 passing backend tests, cited clinical intake, typed review bundles, selective queued-stage reuse, scale-aware metric room links/calibration and MPFB2 avatars. A local API → RTX 5090/VGGT → MPFB2 → PDF draft check passed with fixture hazards and no external calls. Full live Jev/Gemma integration and real connected-room capture remain acceptance gates. Frontend and AMD work are outside this backend pass.

Staged-data prototype implementing [the implementation plan](step-wise-implementation-plan.md). The CPU path runs from upload through a persisted worker, evidence review, Italian HTML/PDF draft and restricted family release. **The default demo's findings and point clouds are synthetic fixtures.** Separate live Jev and single-image NVIDIA/VGGT experiments are recorded in [runtime evidence](docs/runtime.md#local-single-image-vggt-experiment); no full live pipeline or AMD result is claimed. The historical [Jev evaluation](eval/jev/README.md) is preserved separately.

## Local setup

Step Wise uses the `step_wise` Python package, the `step-wise` CLI and `STEP_WISE_*` configuration names. Existing `SAFE_HOME_*` variables remain supported as fallbacks; the new names take precedence. Compose retains the `safe-home-data` volume key to preserve existing assessments.

Tested with Python 3.12.3 and Node 22.22.3 on Linux. Native PDF rendering needs Pango, HarfBuzz and DejaVu fonts (the worker Dockerfile lists packages).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run build
export REVIEWER_USER=reviewer
export REVIEWER_PASSWORD='choose-a-long-random-local-password'
.venv/bin/python -m step_wise.cli init
.venv/bin/python -m step_wise.cli seed-controls
```

Run the API and worker in separate terminals with the same environment and local data directory:

```bash
.venv/bin/uvicorn step_wise.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
.venv/bin/python -m step_wise.worker
```

Open <http://localhost:8000>. Log in as the configured reviewer; create a fictional profile and upload `data/demo/synthetic-1.png` and `synthetic-2.png`. Their controls are deliberately generated drawings, not overlapping photographs. In fixture mode, arbitrary uploads receive the same synthetic control findings, **not an assessment of their contents**. After all four decisions, generate and inspect the draft, then release it. Reviewer identity defaults to “Team member — simulated reviewer”. A high-entropy family link grants only the released HTML/PDF, expires after seven days and is shown once. Deleting an assessment revokes access; released assessments cannot otherwise be edited.

Vite development is available with `npm --prefix web run dev` after starting the API on port 8000. Its `/api` proxy keeps requests on one origin.

## CPU verification

```bash
.venv/bin/python -m unittest discover -s tests
python3 eval/jev/evaluate.py self-check
.venv/bin/python scripts/smoke_e2e.py --mode fixture --base-url http://localhost:8000
```

The smoke command uses the fixed `room-001` manifest through normal HTTP/queue endpoints and records a **simulated** approve/edit/reject/defer review. It checks rejected-action exclusion, unknown scale, stale release and private assets. It leaves its released demonstration assessment available for inspection. API and worker must already be running. It never prints the family token.

Optional browser QA uses a fresh headless browser, without a real user profile:

```bash
.venv/bin/pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/browser_smoke.py --base-url http://localhost:8000
```

Screenshots stay under gitignored `data/browser-smoke/`. TestClient can stall at its local async bridge in the restricted execution sandbox used during implementation; the same fixture tests passed outside that sandbox. This is recorded separately from application failures.

## One-case development loop

```bash
.venv/bin/python -m step_wise.cli case run demo/cases/room-001.json --mode fixture
.venv/bin/python -m step_wise.cli case inspect --run RUN_ID
.venv/bin/python -m step_wise.cli case check --run RUN_ID
.venv/bin/python -m step_wise.cli case run demo/cases/room-001.json --mode fixture --stage gemma --reuse-run RUN_ID
.venv/bin/python -m step_wise.cli case compare --baseline RUN_ID --candidate NEW_RUN_ID
```

Each attempt has an isolated SQLite database, stage inputs/outputs/manifests, checks and local evidence gallery in `data/case-runs/room-001/RUN_ID/`. `inspect` prints the command for the normal reviewer app against that database. Prerequisite reuse checks fingerprints and artifact hashes; targeted reruns mark downstream stages stale. Live mode refuses the synthetic dataset. A fixture run exits `4` while manual evidence/scene/report checks remain pending; this is intentional. Exit `1` means execution error, `2` unmet expectation and `3` missing prerequisite. A successful stage does not establish model quality.

The runner supports all six stage names, including `--stage report --reuse-run RUN_ID` after a compatible review has been saved in that run’s database. Rerunning Jev preserves compatible reconstruction; changing only the profile reuses unchanged visual stages. Saved live outputs are labelled replay in the new attempt, with origin manifests retained. Missing or changed prerequisite artifacts are refused before a dependent model call.

Manual evidence checks and baseline selection are explicit:

```bash
.venv/bin/python -m step_wise.cli case attest --run RUN_ID --check photo-evidence --note 'Concrete inspection evidence'
.venv/bin/python -m step_wise.cli case attest --run RUN_ID --check scene-and-pins --note 'Concrete inspection evidence'
# final-report requires a compatible released report in this isolated database
.venv/bin/python -m step_wise.cli case attest --run RUN_ID --check final-report --note 'Concrete inspection evidence'
.venv/bin/python -m step_wise.cli case baseline --run RUN_ID
```

Only attest checks actually inspected. Attestations retain reviewer identity, revisions and scope. Frozen baselines reject app/worker mutations; later checks verify their records and artifact hashes. A fixture baseline is still a fixture. No real inspected baseline has been selected. `compare` never selects or freezes a baseline. Keep `STEP_WISE_WORKER_LOCK` from the inspect command when starting a worker against an isolated case database, so it shares the main worker/GPU lock.

## Configuration and operation

See [.env.example](.env.example) for variable names. The Python app reads environment variables, not `.env` automatically. Docker Compose reads local `.env`. Never copy credentials into the frontend. Local defaults are `RECON_BACKEND=fixture`, `GEMMA_BACKEND=fixture`, `JEV_ENABLED=false`. Live Jev/Gemma HTTP and VGGT adapters are implemented but have not been validated against real services/GPU hardware. Live reconstruction requires a locally installed, exact VGGT commit and checksum-verified checkpoint; it never downloads weights implicitly. Gemma requires an explicit revision. Backend modes are captured when queueing; a differently configured worker refuses the attempt. External photo acknowledgement is enforced again at the Jev call boundary. Fixture execution performs no model calls. A configured backend name is not evidence of actual hardware execution.

```bash
# CPU containers; fixture build and HTTP smoke verified.
docker compose -f compose.yaml up --build
# Dry-run retention, then explicit execution if desired:
.venv/bin/python -m step_wise.cli cleanup --older-than-days 30
.venv/bin/python -m step_wise.cli cleanup --older-than-days 30 --execute
# Explicitly remove only a seeded synthetic development assessment:
.venv/bin/python -m step_wise.cli reset --assessment ASSESSMENT_ID
```

API restart preserves the queue. Stop the normal worker before running a case or benchmark against its shared worker/GPU lock. A worker holds one file lock; exclusive restart marks abandoned running jobs interrupted. Retry submits a new analyze request and reuses compatible, intact stages from that assessment. Heartbeat age never authorizes a second worker. Local SQLite WAL and filesystem storage are intended for one host. Back up database and assets together before schema changes. Schema v1/v2 databases migrate transactionally to v3 without rewriting released reports.

The API requires reviewer authentication for assessment/media/review routes, rejects cross-origin mutations, uses HttpOnly SameSite session cookies and marks private responses `no-store`. Bind loopback for local development. `STEP_WISE_PORT` changes the Compose host port (default 8000). Before any hosted demo, use HTTPS, suppress/redact proxy access logs (family paths contain capabilities), keep worker/model services internal, complete the hosted-demo access and deployment audit. This prototype is not ready for real patient intake.

## Remaining scope

[Runtime readiness](docs/runtime.md) records exact tested CPU versions, blockers and pending gates. Real staged capture, live client/GPU execution, visually checked real anchors, calibration/measurements, full GPU smoke/co-residency checks, real benchmark/ablation/cost results, a checked real baseline and submission evidence remain unfinished. The GPU preflight and benchmark collector report missing prerequisites; they do not substitute fixture results. `compose.rocm.yaml` is an unvalidated recipe requiring explicit GPU image/revision inputs. P1/P2 extensions are deferred beyond this first beta.
