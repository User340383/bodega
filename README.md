# Bodega

A web app for small shops to capture sales from a photo or text, confirm them, and get restocking estimates. Built for COMP 425, Team Bodega.

## Team

Each member's report lists the details of their part:

- [Luis Heyaime](docs/LUIS_IMPLEMENTATION.md): application shell, Docker, CI/CD, Render deployment
- [José Javier Otazu](docs/JOSE_IMPLEMENTATION.md): AI ingestion and capture
- [Diushen Cabrera](docs/DIUSHEN_IMPLEMENTATION.md): analysis, guidance and results dashboard
- [Gabriel Marte](docs/GABRIEL_IMPLEMENTATION.md): data and state, draft review, sales history

The app's pages are `/login`, `/capture` (Photo, Text, Enter manually), `/review/:submissionId`, `/drafts`, `/history` and `/analysis`.

**Integration status:** this branch integrates all four members' Milestone 4 work against `WorkDivision.docx` (the Milestone 4 deliverables) and the final Milestone 3 design. See the [Milestone 4 deliverable mapping](docs/TRACEABILITY.md#milestone-4--workdivision-deliverables-review-of-2026-11-10) for each deliverable's implementation, test evidence and open items, the [integration decisions](docs/INTEGRATION_DECISIONS.md), [requirements traceability](docs/TRACEABILITY.md), the [data dictionary](docs/DATA_DICTIONARY.md) and [privacy and retention](docs/privacy.md). Production runs on Render (see `render.yaml` and the deployment section of Luis's report); code reaches production only through the GitHub Actions deploy job after every check passes on `main`.

## Start locally

Requires Docker Desktop with Linux containers and Docker Compose.

```sh
docker compose up --build -d --wait
```

Open http://localhost:5173. The development owner access code is `local-development-code-123`, matching the existing Compose configuration. API documentation is at http://localhost:8000/docs. Schema migrations run automatically during backend startup.

To capture sales, use **Capture sales** at the top of the page: choose **Text** and paste lines such as `Bread 3 1.50`, or upload a JPEG/PNG photo. With the default `AI_PROVIDER=mock` no paid key is needed; see [the ingestion report](docs/JOSE_IMPLEMENTATION.md) for privacy controls, timeouts and how to enable real Claude extraction. To try draft review with explicitly synthetic local data instead:

```sh
docker compose exec backend python -m app.seed
```

Drafts are stored in PostgreSQL, so **Pending drafts** lists them in any session, with **Open** and **Discard**. When the AI could not read a capture, open the draft and type the sales; the saved photo or text is shown beside the form. **History** pages through confirmed sales (last 28 days by default) and has **Delete all my sales data**, which needs the word DELETE.

Sign in, go to **Pending drafts**, and open the seeded draft (submission `10000000-0000-4000-8000-000000000003`). Verify or edit its highlighted unit price, then confirm; the sale then appears under **History**. Seeding is optional and never runs at startup.

## Results dashboard

The Results dashboard shows, per item, the average sold per logged day over the latest 7 logged days (inside a 28-day window), the trend (UP, DOWN or STABLE once 14 days are logged), the suggested stock for the next 7 selling days, and restocking advice checked against those figures. If the AI wording fails or cannot be verified, the same figures are shown as labeled **Standard advice**. See [the analysis and guidance report](docs/DIUSHEN_IMPLEMENTATION.md) for formulas, assumptions and contracts. To try it with synthetic history, use the Python seed (through the real confirmation code) or the SQL seed script (bread and milk on the last 14 days, straight into PostgreSQL):

```sh
docker compose exec backend python -m app.seed_analysis
docker compose exec -T postgres psql -U bodega -d bodega -v ON_ERROR_STOP=1 < backend/seeds/synthetic_sales.sql
```

`AI_PROVIDER=mock` needs no paid key. Mock wording demonstrates the integration only, not real-provider quality.

## Project structure

```text
bodega/
├── backend/                  FastAPI API (Python 3.12)
│   ├── app/                  routes (main.py), ingestion, privacy, analysis, guidance, repository, storage
│   ├── alembic/              Alembic revisions; they apply the reviewed SQL files in migrations/
│   ├── migrations/           SQL schema migrations
│   ├── seeds/                synthetic SQL seed data
│   ├── tests/                pytest suites and the HTTP smoke test (smoke_http.py)
│   ├── benchmarks/           extraction benchmark harness
│   ├── ruff.toml             Python lint rules
│   ├── pyrightconfig.json    Python type-check rules
│   └── Dockerfile
├── frontend/                 React + Vite single-page app, styled with Tailwind CSS
│   ├── src/pages/            one component per route (/login, /capture, /review, /drafts, /history, /analysis)
│   ├── src/components/       capture, draft review, manual entry, history, results dashboard, delete-all
│   ├── src/auth/, src/api/, src/layout/   access-code session, API client, application shell
│   ├── eslint.config.js      JavaScript lint rules
│   └── Dockerfile            dev server for Compose; nginx image for production
├── docs/                     team reports, SRS, traceability, privacy, architecture diagrams, manual tests
├── scripts/                  test-all.ps1 (all tests), production-topology and requirement measurement scripts
├── .github/workflows/ci.yml  CI: tests, lint, type check, smoke test, image builds, Render deploy
├── docker-compose.yml        local application: PostgreSQL, MinIO, backend, frontend
├── compose.test.yml          isolated test stack (temporary PostgreSQL and MinIO)
├── compose.prodcheck.yml     production-topology checks
└── render.yaml               Render deployment blueprint
```

## Running the tests

### One command for all tests

From the repository root, with Docker Desktop running:

```powershell
.\scripts\test-all.ps1
```

This is the command that runs the complete automated suite. It runs the same checks as CI, in this order, and stops with a non-zero exit code at the first failure:

1. Backend tests: pytest against a temporary PostgreSQL database.
2. Alembic `upgrade head` on a clean database.
3. Backend lint and type check: Ruff and Pyright.
4. Frontend checks: ESLint, Vitest and the Vite production build.
5. The HTTP smoke test, run against the full test stack.

It prints a pass/fail summary of each step at the end. It needs no API keys and contacts no production service. It runs in the separate `bodega-gabriel-test` Compose project with in-memory databases, and the cleanup at the end removes only that project's containers. Your local application's data is not touched.

- **Scripts blocked:** if Windows blocks local scripts, run `powershell -ExecutionPolicy Bypass -File .\scripts\test-all.ps1`.
- **macOS or Linux:** use PowerShell 7: `pwsh ./scripts/test-all.ps1`.

CI also runs a secret scan, the Mermaid diagram render and the production image builds, which `test-all.ps1` leaves out.

### Lint and type checking

| Tool | Checks | Configuration | Run by |
| --- | --- | --- | --- |
| ESLint | Frontend JavaScript/JSX | `frontend/eslint.config.js` | `npm run lint`, `frontend-checks`, CI |
| Ruff 0.17.4 | Backend Python lint | `backend/ruff.toml` | CI "Lint and Secret Scan", `test-all.ps1` |
| Pyright 1.1.414 | Backend Python types (basic mode) | `backend/pyrightconfig.json` | CI "Lint and Secret Scan", `test-all.ps1` |

Pyright checks `app/`, `alembic/` and `benchmarks/` against the pinned requirements. It catches errors such as wrong return types, undefined names and unbound variables.
- **Seven rules are switched off** in `pyrightconfig.json`:
  - `reportAttributeAccessIssue`
  - `reportArgumentType`
  - `reportOptionalMemberAccess`
  - `reportOptionalOperand`
  - `reportOptionalIterable`
  - `reportCallIssue`
  - `reportFunctionMemberAccess`
- **Why:** with them on, the existing code has 95 findings on 44 lines. Most come from pandas and Anthropic SDK type stubs.
- **To tighten later:** turn a rule back on once its findings are fixed.

The frontend is plain JavaScript with no TypeScript, so its type-level checking comes from ESLint and the production build.

### Manual test checklist

[docs/manual-tests.md](docs/manual-tests.md) has seven end-to-end browser scenarios with steps, expected and actual results, and a pass/fail record:
- sign-in
- text and photo capture
- review and confirm
- manual entry and the AI outage
- the results dashboard
- deleting all data

### Individual steps

The isolated test stack uses a separate Compose project, an unexposed PostgreSQL test service backed by tmpfs, and a unique schema per backend test. It does not use application database volumes. To run its parts one at a time:

```sh
docker compose -f compose.test.yml build
docker compose -f compose.test.yml run --rm backend-tests
docker compose -f compose.test.yml run --rm --no-deps frontend-checks
docker compose -f compose.test.yml --profile smoke up -d --build --wait backend-smoke backend-smoke-ai frontend-smoke
docker compose -f compose.test.yml --profile smoke run --rm --build http-smoke
docker compose -f compose.test.yml --profile smoke down
```

The smoke stack uses PostgreSQL and MinIO on tmpfs and two backends on one database: one with no AI provider (manual entry during an outage, stored photos, discard) and one with the mock provider (capture → review → confirm → history → analysis → guidance). CI runs it as the HTTP Smoke Test job.

The test database is temporary. The normal application remains running. Use `docker compose stop` to stop the application while retaining its volumes.

## Production-topology checks and requirement measurements

These are additional, slower checks. Each uses its own Compose project and in-memory database.

```sh
# Render images on two origins (nginx bundle + API with CORS), bound to 127.0.0.1
docker compose -f compose.prodcheck.yml up -d --build --wait
python scripts/prodcheck.py              # deployment checks; NFR-701/703 at 10 concurrent sessions
node scripts/browser_nfr.mjs             # NFR-702/704/705 in Edge on the SRS 4G profile (npm i playwright@1.49.1)
docker compose -f compose.prodcheck.yml down

python scripts/nfr725_restart_trial.py   # NFR-725: 10 forced backend restarts during confirmation
```

Results and evidence: [docs/TRACEABILITY.md](docs/TRACEABILITY.md) and `docs/verification/`.

Migrations are Alembic revisions (`backend/alembic`) that apply the reviewed SQL files in
`backend/migrations`. They run automatically at startup; to run them by hand:

```sh
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current
```

`python scripts/live_ai_check.py` checks the real Anthropic integration once an authorized
`AI_API_KEY` is set (extraction with `AI_EXTRACTION_MODEL`, guidance with `AI_GUIDANCE_MODEL`, and
the NFR-713 payload). It makes a few billed calls with synthetic data, and none without a key.
`python scripts/live_storage_check.py` checks an authorized S3/R2 bucket (upload, read-back,
signed URL, listing, delete, repeat delete) with one synthetic image, and contacts nothing
without the four `S3_*` settings.
