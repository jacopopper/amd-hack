# Step Wise reviewer app

This Next.js app is the reviewer UI for the Step Wise FastAPI backend. It supports fictional patient intake, room-photo upload, queued analysis, evidence review, draft inspection, and controlled report release. See the [root README](../README.md) for backend setup and the fixture/live model boundary.

For local development, start the API and worker as described in the root README, allowing the UI origin on the API. Then run:

```bash
npm ci
STEP_WISE_BACKEND=http://127.0.0.1:8000 npm run dev -- --port 3010
```

Open <http://127.0.0.1:3010> to use the reviewer app. The development server proxies `/api` to `STEP_WISE_BACKEND`; the API needs `STEP_WISE_ALLOWED_ORIGINS=http://127.0.0.1:3010` when started. There is no reviewer login, so only run it with the API bound to a trusted local interface.

For the API-served production build:

```bash
npm run typecheck
npm run build
```

The build exports static files to `out/`. FastAPI serves them on its own origin alongside `/api`; Docker builds and copies the same export. The UI does not contain model credentials. Its 3D room view loads the point-cloud asset from the reviewer API and retains photo evidence when metric scale or 3D hazard anchors are unavailable.
