# Luis Heyaime: Milestone 4 implementation

**Areas:** DevOps and boilerplate, CI/CD pipeline, application shell.
**Specification:** Milestone 3, Sections 4 (architecture, REST contract, frontend structure) and 8 (deployment and DevOps).

## Summary

| Area | Status |
|---|---|
| Repository scaffold and local `docker-compose.yml` (React, FastAPI, PostgreSQL, MinIO) | Done |
| `.env` handling: `.env.example` lists every variable; `.env` files are ignored | Done |
| Render deployment configuration (`render.yaml`, production frontend image, `$PORT`) | Done. Render account setup is manual (below) |
| Access-code security (NFR-718): constant-time check, 401, 429 after 10 failures | Done |
| CORS restricted to `FRONTEND_URL`; the backend refuses to start without its required settings | Done |
| GitHub Actions: tests on every pull request, production image build, deploy on `main` only | Done |
| Branch protection on `main` with required reviews | Settings below; a repository admin applies them |
| React application shell: routing, navigation, sign-in, global API client | Done |

## Application shell (frontend)

The routes follow M3 Section 4.3. Every route except `/login` requires the access code.

| Route | Page | Uses |
|---|---|---|
| `/login` | Access-code form. It verifies the code against the API before saving it. | `auth/` |
| `/capture` | Photo or text capture. On success it opens the review page. | José's `SalesCapture` |
| `/review/:submissionId` | Draft table beside the photo or masked text, with fallback messages. | Gabriel's `DraftReview` |
| `/drafts` | Pending drafts captured in this tab, plus "open by ID". | Shell |
| `/history` | Confirmed sales ledger | Gabriel's `SalesHistory` |
| `/analysis` | Estimates, trends and advice | Diushen's `ResultsDashboard` |
| `/` and unknown paths | `/` redirects to `/capture`. Unknown paths show a not-found page. | Shell |

### Global API client (`src/api/client.js`)

Every component receives the same `request(path, options)` function, so none of them needed changes.

- **Base URL:** read from `window.__BODEGA_CONFIG__.apiBaseUrl` (`/config.js`). It is empty locally, where Vite proxies `/api`.
- **Auth header:** sends `Authorization: Bearer <code>` with the code from the current session.
- **Body encoding:** plain objects are sent as JSON. `FormData` (photo upload) keeps the browser's multipart boundary.
- **Errors:** every error becomes an `ApiError {status, code, message, fields}`. The client reads both the M3 error body `{error: {code, message, fields}}` and FastAPI's `{detail}`, and returns plain-English messages for HTML or empty gateway pages, network failures and timeouts.
- **Timeouts:** the default is 30 s. A caller can pass its own `signal`, as the capture page does for its 90 s limit, and then gets its own `AbortError` back.
- **Expired codes:** a **401** from any call signs the owner out and returns them to `/login` with "Please sign in again."

### Session (`src/auth/`)

The access code is kept only in `sessionStorage`, so it is cleared when the tab closes (M3 4.3). If browser storage is blocked, the shell falls back to memory. Sign-in uses its own client, so a wrong code on the form does not trigger the global sign-out.

### Layout

- **Layout:** mobile-first, with navigation that wraps under the brand on phones and touch targets of at least 44 × 44 px.
- **Accessibility:** a skip link, and the active page is marked in the navigation.

## DevOps

### Local environment

```sh
docker compose up --build -d --wait   # frontend :5173, backend :8000, postgres :5432, minio :9000/:9001
```

The frontend `Dockerfile` has three stages:

| Stage | Used by | Contents |
|---|---|---|
| `dev` | `docker-compose.yml` and `compose.test.yml` (both pin `target: dev`) | Vite dev server |
| `build` | the `prod` stage | `npm run build` |
| `prod` (default) | Render | `nginx:1.27-alpine` serving the bundle |

The `prod` stage serves the bundle with an SPA fallback, long caching for hashed assets, and `no-store` on `config.js` and `index.html`. It also sends security headers. `docker-entrypoint.d/40-bodega-config.sh` writes `/config.js` from `API_BASE_URL` at start, so one image works in every environment. The script adds `https://` to a bare host and refuses any value that is not a plain http(s) URL.

The backend image listens on `$PORT` (default 8000) with `--proxy-headers`, so the rate limit sees real client IPs behind Render's proxy. Migrations run in the app lifespan before `/health` answers.

### Backend security (`backend/app/security.py`)

- **Access code:** checked with `hmac.compare_digest`. A wrong or missing code returns **401**.
- **Rate limit:** after 10 wrong codes from one IP within 15 minutes, every request from that IP gets **429**, even with the right code, until the window passes. The counter is in memory and resets when the app restarts, which suits the single instance.
- **CORS:** only the origins in `FRONTEND_URL` (comma-separated; a bare host gets `https://`).
- **Startup check:** the backend refuses to start if `DATABASE_URL`, `APP_ACCESS_CODE` or `OWNER_ID` is missing (M3 8.3).
- **Tests:** `backend/tests/test_security.py`.

### CI/CD (`.github/workflows/ci.yml`)

| Job | Runs on | What it does |
|---|---|---|
| Backend Tests | every PR to `main`, every push | pytest against a disposable PostgreSQL 16 (`compose.test.yml`), mock AI only |
| Frontend Tests, Lint and Build | same | `npm run lint`, `vitest`, `vite build` in the `dev` image |
| Production Images | same | builds both Render images and smoke-tests the nginx image (SPA route, `config.js`) |
| Deploy to Render | push to `main` only, after all three pass | `POST` both Render deploy hooks |

The deploy job prints a warning and skips if the hook secrets are not set yet, so `main` stays green before Render exists. Runs for a newer push cancel older ones, except on `main`; deploys never overlap.

### Render setup (one time, manual)

Render auto-deploy is off in `render.yaml`. Production only changes through the deploy job.

1. **Create the R2 bucket.** In Cloudflare R2, create the private bucket `sales-images` and a bucket-scoped API token. Note its endpoint `https://<account-id>.r2.cloudflarestorage.com`.
2. **Apply the Blueprint.** In Render, choose **New → Blueprint** and select this repository. Render reads `render.yaml` and asks for the secrets:

   | Variable | Value |
   |---|---|
   | `AI_API_KEY` | the Anthropic API key |
   | `APP_ACCESS_CODE` | at least 12 characters |
   | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | from R2 |
   | `FRONTEND_URL` | the frontend's public URL, e.g. `https://bodega-frontend.onrender.com` |
   | `API_BASE_URL` (frontend service) | the backend's public URL, e.g. `https://bodega-backend.onrender.com` |

3. **Add the deploy hooks to GitHub.** For each service, open **Settings → Deploy Hook** and copy the URL. Add them as GitHub repository secrets `RENDER_DEPLOY_HOOK_BACKEND` and `RENDER_DEPLOY_HOOK_FRONTEND`.
4. **Choose the database plan.** `render.yaml` uses the free plan, and free databases expire after 30 days (M3 12.1). Switch to a paid plan for the demo period, or export with `pg_dump` weekly.

### Branch protection for `main`

A repository admin applies these under **Settings → Branches**, or with `gh api`:

- **Pull requests:** required before merging, with at least **1 approving review**. Stale approvals are dismissed when new commits are pushed.
- **Status checks:** required to pass, and the branch must be up to date. The required checks are Backend Tests, Frontend Tests, Lint and Build, and Production Images.
- **Conversations:** must be resolved before merging.
- **Force pushes and deletions:** blocked.

## Verification (local, Docker)

| Check | Result |
|---|---|
| `compose.test.yml` backend tests | 300 passed (includes 14 new security tests) |
| Frontend in the `dev` container: lint, tests, build | Clean; 75 tests passed (includes the new API client and shell tests) |
| HTTP smoke test (`--profile smoke`) | Passed |
| nginx image | SPA routes serve `index.html`; `config.js` written with `no-store`; an injected `API_BASE_URL` is refused |
| Backend image | Started without the required variables, it exits with `Missing required environment variables: …` |

## Known gaps and handoffs

- **No list endpoint for Pending drafts.** `GET /api/v1/submissions` (FR-216) does not exist yet, so `/drafts` lists drafts captured in the current tab and can open any draft by ID. When the endpoint lands, the page should read from it. `DELETE /submissions/{id}` and `POST /manual-entries` are not implemented yet either.
- **No real AI guidance in production.** `llm.py` has no Claude guidance provider yet. Production falls back to template ("Standard advice") wording until one is added. `AI_GUIDANCE_MODEL` is already configured for it.
- **ruff is not in CI yet.** M3 8.5 lists `ruff check`, but the existing backend code has 38 ruff findings. Add the step once the owners have cleaned them up.
- **Migrations are custom.** M3 mentions Alembic; the backend uses its own ordered SQL migrations run at startup. The effect is the same (`/health` answers only after migrations).

## Integration update (`integration/m3-completion`)

- **Security fix.** `--forwarded-allow-ips='*'` let clients bypass the NFR-718 lockout by rotating `X-Forwarded-For`. The limiter now counts the right-most hop (`TRUSTED_PROXY_HOPS`, 1 on Render), and uvicorn runs with `--no-proxy-headers` (D-12). Regression tests and a CI guard are included.
- **New CI job "Lint and Secret Scan":** ruff (`backend/ruff.toml`) and gitleaks over the full history. Deploy waits for it.
- **Pinned dependencies.** `requirements.txt` is pinned to the verified versions.
- **HSTS** is set on the API and every nginx location.
- **Logging and errors.** Structured JSON request logs with `X-Request-ID` (NFR-723), and the M3 error envelope with 400 validation.
- **Pending drafts** now read `GET /api/v1/submissions`, with Discard.
- **Browser check (Edge, 360–1920 px):** no overflow, text ≥ 16 px, targets ≥ 44 px, 0 axe WCAG 2.1 AA violations.
- **Still manual:** Render provisioning, deploy hook secrets, the database plan and branch protection.

## Milestone 4 review update (2026-11-10)

Reviewed against `WorkDivision.docx` and the final Milestone 3; the full mapping is in `docs/TRACEABILITY.md`.

- **Your deliverables are complete locally:** one-command Compose stack, `.env.example`, `render.yaml`, CI on every pull request, the `main` ruleset (1 approving review, 3 required checks) and the application shell. The live Render deployment still needs the account.
- **Changes in your files, for your review:** `backend/Dockerfile` copies `seeds/` (SQL seed script, D-34). `ci.yml` and `compose.test.yml` gain an HTTP Smoke Test job with MinIO and a second backend using the mock provider. `main.py` adds the envelope for unexpected errors (`INTERNAL_ERROR`) and 405, `/sales` paging (D-32), and 400 for every validation error including confirmation (D-30, resolved by team review on 2026-11-11). `DraftsPage.jsx` and `ReviewPage.jsx`: the review page gains "Discard this draft".
- **Please decide:** add "Lint and Secret Scan" and "HTTP Smoke Test" to the required checks of the ruleset.
- **2026-11-11:** the focused skip link is now 44 px or larger (D-37). `scripts/prodcheck.py` checks the D-30 status codes and reads `total` from the paged ledger. New `scripts/browser_a11y.mjs` (Edge, axe-core 4.10.1) records the accessibility and layout check that was previously run by hand.
