# Gabriel Marte: implementation report

## 1. Completed responsibilities and evidence

`WorkDivision.docx` assigns Gabriel the PostgreSQL Owners/Submissions/Sales/Baselines schema, transactional `POST /api/v1/confirm`, database rollback/duplicate-confirmation tests, interactive Draft Review, and confirmed Sales History. These portions are implemented. The Milestone 3 data dictionary, lifecycle discussion, ERD, and state diagram guided the design; Milestone 2 supplies the baselined numeric and confidence rules. Milestone 1 supplies the original product context. All four DOCX files were parsed directly from their Word XML; no Word installation was needed.

The original repository contained FastAPI endpoint placeholders and the default React/Vite page. Existing access-code protection and analysis/guidance placeholders remain. Development is on `gabriel/data-state-implementation`. The implementation is prepared for a reviewed local commit; publishing and merging are separate actions. The user-provided task file and DOCX files remain untouched and untracked.

## 2. Files created

| File | Purpose |
| --- | --- |
| `backend/app/database.py` | PostgreSQL connections; ordered, advisory-locked transactional SQL migration runner. |
| `backend/migrations/001_data_state.sql`, `002_nonblank_labels.sql` | Four domain tables, keys, indexes, lifecycle/consistency triggers and whitespace-only label protection. |
| `backend/app/repository.py` | Submission persistence/recovery, row-locked confirmation, confirmed-only history. |
| `backend/app/seed.py` | Opt-in, repeatable synthetic bread/milk history plus a review draft. |
| `backend/tests/test_database.py` | Real PostgreSQL integration tests in isolated per-test schemas. |
| `backend/tests/smoke_http.py` | HTTP checks through the frontend proxy into the isolated database. |
| `backend/pytest.ini` | Backend test discovery and import path. |
| `backend/.dockerignore` | Excludes local Python environments, caches and optional tools from builds. |
| `frontend/src/components/DraftReview.jsx` | Editable rows, confidence verification, validation, transactional confirmation feedback. |
| `frontend/src/components/SalesHistory.jsx` | Confirmed ledger with pagination, refresh, loading, empty and error states. |
| `frontend/src/components/SalesReview.test.jsx` | Automated interaction tests for both components. |
| `frontend/src/test-setup.js`, `frontend/vitest.config.js` | React DOM test setup with Vitest/jsdom. |
| `compose.test.yml` | Separate Compose project with temporary PostgreSQL, test runners and HTTP smoke services. |
| `README.md`, `docs/GABRIEL_IMPLEMENTATION.md` | Setup, validation, contracts, ownership and implementation report. |

## 3. Existing files modified and shared-change rationale

- `backend/app/main.py`: wires migrations into application startup, implements confirmation, exposes history/draft/recovery routes, validates confirmation inputs and preserves the existing access dependency. Adds a safe database-error response without logging submitted values.
- `backend/requirements.txt`: adds psycopg, pytest and httpx for PostgreSQL persistence and automated tests.
- `backend/Dockerfile`: includes migrations and test files so startup and verification work inside Docker.
- `docker-compose.yml`: supplies the frontend's internal backend proxy target and waits for backend health after migrations before starting the frontend. Original services, ports, credentials and named data volumes are preserved.
- `frontend/src/App.jsx`: replaces the default Vite counter with a minimal mount point for Gabriel's components, an in-memory access code and saved-draft lookup. This is an integration harness for Luis's future shell/client and Jose's capture UI, not a replacement implementation of their assigned work.
- `frontend/src/App.css`, `frontend/src/index.css`: styles Gabriel's review/ledger, field warnings, responsive scrollable tables and accessible controls using the scaffold's plain CSS approach.
- `frontend/vite.config.js`: same-origin `/api` proxy for local and container development; explicitly configured allowed host for isolated HTTP tests.
- `frontend/package.json`, `frontend/package-lock.json`: adds the frontend test command and locked test dependencies.
- Source encoding was normalized where needed for Linux builds. The frontend Docker ignore rules retain their original `node_modules`/`dist` exclusions.

## 4. Database and lifecycle

Logical ERD entities use conventional lowercase PostgreSQL names:

- `owners`: UUID primary key. Each submission and baseline belongs to one owner; cascades support eventual owner-data deletion.
- `submissions`: UUID, owner FK, immutable sales date, nullable private image key, creation timestamp, nullable JSONB draft payload, and one of the six documented states. `(id, sale_date)` is unique for the composite sales FK.
- `sales_records`: UUID, submission/date FK with cascade, line number unique within the submission, nonblank item label, exact finite positive NUMERIC quantity, exact finite nonnegative NUMERIC unit price, and `AI_EXTRACTED` or `MANUAL` source. The date must match the parent. Updates are blocked; corrections happen before confirmation. Line totals are not stored.
- `baseline_estimates`: owner FK, item, window/logged days, exact nonnegative average, constrained trend, nonnegative suggested quantity, and computation timestamp. This stores snapshots; it does not implement Diushen's calculations or choose an analysis formula.

Indexes cover owner/status/date submission lookup, per-submission sales via the unique line index, normalized item/date lookup, and owner/normalized item/latest baseline lookup. Matching uses trimmed, case-insensitive labels; synonyms are not merged.

Allowed transitions:

```text
UPLOADED -> CLASSIFYING
CLASSIFYING -> NEEDS_VERIFICATION | AI_UNAVAILABLE | REJECTED
AI_UNAVAILABLE -> NEEDS_VERIFICATION | CONFIRMED
REJECTED -> NEEDS_VERIFICATION
NEEDS_VERIFICATION -> CONFIRMED
```

Same-state updates support retry/review without introducing another state. New submissions start UPLOADED and cannot have future server-calendar dates. Confirmed status is terminal. Deferred consistency triggers require confirmed submissions to contain sales rows and other submissions to contain none at transaction commit. Invalid transitions and mismatched parent dates are rejected by PostgreSQL.

Migrations and their ledger entries commit together. `001_data_state.sql` initializes the schema; additive `002_nonblank_labels.sql` rejects labels consisting solely of tabs/newlines as well as spaces. Startup migration execution is serialized by an advisory transaction lock. Re-running migrations skips applied versions; no destructive down migration or volume deletion is used.

**Documented ambiguity:** Milestone 3 section 3 explicitly identifies integer-only quantities as an unapproved change. Milestone 2 and the existing API accept positive fractional quantities. This implementation preserves decimals, rather than silently adopting an unapproved restriction. The confirmation API limits each number to 14 integer digits and 6 decimal places, and drafts to the scaffold's existing 1-100 rows. Item labels are capped at 200 characters. Analysis-policy differences remain for the team to resolve; neither competing analysis formula was implemented here.

## 5. API contract and transaction behavior

All new routes use the existing `Authorization: Bearer <APP_ACCESS_CODE>` protection and configured `OWNER_ID`; no new authentication/account system was introduced.

### POST /api/v1/confirm

Preserves the scaffold's `submissionId`/`lines` contract:

```json
{
  "submissionId": "10000000-0000-4000-8000-000000000003",
  "lines": [{
    "draftLineId": "seed-bread",
    "itemName": "Bread",
    "quantity": "3",
    "unitPrice": "1.50",
    "verifiedFields": ["unitPrice"]
  }]
}
```

`draftLineId` is optional for newly entered manual rows. `verifiedFields` is an additive optional field naming `itemName`, `quantity` or `unitPrice`. For stored draft lines, unchanged uncertain/unscored fields must be explicitly verified; edited values satisfy review. Unknown/repeated draft line IDs are rejected. AI-assisted corrections retain AI_EXTRACTED source; new manual rows use MANUAL.

The repository locks the owner's submission with `SELECT ... FOR UPDATE`, checks eligibility, inserts all lines, updates status to CONFIRMED and clears the persisted draft in one transaction. Exceptions during insertion, status change, or commit roll back the complete operation. UUIDs are generated only for rows in that save. Concurrent requests serialize on the submission lock.

First success: HTTP 201, `{submissionId, recordIds, created: true}`. Repeated validated confirmation: HTTP 200 with the same IDs and `created: false`, without any additional inserts. This implements Milestone 3's explicit idempotency rule even though the task wording calls for duplicate confirmations to be "blocked."

Invalid input/verification: 422. Unknown or differently owned submission: 404. Ineligible state: 409. Authentication failure: 401. Database failure: 500 with a retry message; submitted values are not returned in database logs. Invalid requests leave rows/status/draft unchanged.

### Added integration endpoints

- `GET /api/v1/sales?limit=100&offset=0`: `{records, limit, offset}`, with 1-100 rows per page. Records include id, submissionId, saleDate, itemName, quantity, unitPrice and source. Exact decimal values serialize as strings. Reads join the parent submission and filter owner and CONFIRMED status; drafts never enter the ledger.
- `GET /api/v1/submissions/{id}`: reopens the stored draft's date, state, lines and manual-entry flag for the current owner.
- `POST /api/v1/submissions/{id}/manual-review`: explicitly moves REJECTED or AI_UNAVAILABLE into NEEDS_VERIFICATION, clearing any unusable draft. Already-reviewing requests are harmless; other states return 409. This is Gabriel's lifecycle/review boundary, not an AI/capture implementation.

The existing extraction placeholder now creates a real submission and returns AI_UNAVAILABLE with empty lines and an explicit unimplemented message. It neither pretends to extract sample products nor stores unsanitized raw inputs. Analysis/guidance remain unchanged placeholders.

## 6. Frontend behavior

DraftReview pre-fills readable values, blanks LOW fields, highlights MEDIUM/missing/unrecognized confidence, requires explicit verification or edits, and displays field-specific validation errors. Owners can add/remove rows and review fractional quantities and zero prices. While saving or after successful confirmation, editing is disabled. Failed saves retain the current draft for retry; rejected inputs have an explicit manual-review action.

SalesHistory displays date/item/quantity/unit price/source from PostgreSQL through the API, with loading/empty/error states, retry/refresh and pagination. Successful confirmation reloads the first page, making new sales visible. Reopening a submission resets the component to the retrieved state. Access codes are kept in component memory rather than persisted in browser storage.

The two components accept an injected `request(path, options)` client. DraftReview additionally accepts the captured `draft` and `onConfirmed` callback. Luis can replace the temporary mount point with the shared shell/API client; Jose can pass the extraction response directly instead of requiring ID lookup. No provider keys are sent to React.

## 7. Executed verification

Docker Desktop Linux engine and Compose were used for actual execution:

| Check | Result |
| --- | --- |
| PostgreSQL backend integration suite | 29 passed; none skipped or failed. |
| React interaction suite | 8 passed. |
| Frontend ESLint | Passed. |
| Frontend Vite production build | Passed. |
| Normal `docker compose up --build -d --wait --wait-timeout 120` | Passed; PostgreSQL, MinIO, backend and frontend started. |
| Backend health and frontend HTTP checks | HTTP 200; startup completed. |
| Application migration ledger inspection | `001_data_state.sql` and `002_nonblank_labels.sql` applied. No synthetic records inserted into the application database during verification. |
| Isolated HTTP smoke | Passed: frontend HTML, Vite proxy, real persisted draft, confirmation, duplicate protection, decimal ledger and unauthorized access. |
| Test schema cleanup | No per-test schemas remained in the isolated test database. |

The backend suite covers migration repeatability, relationships/cascades, SQL constraints, confirmation/history, invalid input consistency, ineligible/unknown/foreign-owner submissions, manual recovery, exact decimal serialization, partial-save rollback, injected database failure rollback, concurrent/repeated confirmation, immutable records, baseline constraints and seed repeatability.

Two failures found during verification were fixed: a Windows-encoded JSX source rejected by the Linux production build, and Vite rejecting the isolated test container's hostname. The relevant checks subsequently passed.

The installed Starlette emits an httpx TestClient deprecation warning; the tests still pass. `npm ci` reports six moderate dependency advisories, also present before adding the test dependencies; no broad forced dependency upgrades were made. These checks do not establish the SRS's performance/AI-quality targets, ten forced-restart benchmark, visual browser compatibility or usability benchmarks.

## 8. Remaining team integration

- **Jose Javier Otazu:** real image/text ingestion, privacy preprocessing, object storage, provider retry/timeouts, capture UI and independent manual-entry creation. Call `create_submission`, `update_status`, and store only masked draft rows/confidence bands. Image retention/discard cleanup needs the real storage integration. The current placeholder discards raw input immediately and does not simulate AI success.
- **Diushen Cabrera:** analysis/guidance implementations and dashboard; the baseline table is ready, but computation, grounding, snapshot repository operations and the unapproved SRS formula changes belong to that work.
- **Luis Heyaime:** complete routing/shell/global client, CI/CD, deployment, configuration/secret management and branch protection. The local proxy and minimal component mount are necessary integration changes only.
- Owner deletion of raw input plus derived data, retention for abandoned inputs and device-vs-server sales-date validation need alignment with the wider ingestion/storage workflow. No user-facing bulk deletion endpoint or storage cleanup job is claimed here. The schema supplies cascades, but does not pretend to delete remote image objects.

## 9. Exact run and test commands

From the repository root with Docker Desktop running:

```sh
docker compose up --build -d --wait
# Migrations run automatically; explicit repeatable equivalent:
docker compose exec backend python -m app.database
# Optional synthetic local data, never automatic:
docker compose exec backend python -m app.seed
```

Open http://localhost:5173, connect with `local-development-code-123` (existing development Compose value), and open draft `10000000-0000-4000-8000-000000000003` after optional seeding. API docs: http://localhost:8000/docs. The existing Compose file supplies local settings directly; copying `.env.example` alone does not override those hardcoded development values.

```sh
docker compose -f compose.test.yml build
docker compose -f compose.test.yml run --rm backend-tests
docker compose -f compose.test.yml run --rm --no-deps frontend-checks
docker compose -f compose.test.yml --profile smoke up -d --build backend-smoke frontend-smoke
docker compose -f compose.test.yml --profile smoke run --rm --build http-smoke
docker compose -f compose.test.yml --profile smoke down
```

The test project uses its own network and an unexposed, in-memory PostgreSQL service. Tests create/drop only random schemas there. Neither test command targets the application's persistent PostgreSQL volume. No `down -v` command is needed.

Optional native development (Python 3.12+ and a running PostgreSQL server): create a virtual environment, run `pip install -r backend/requirements.txt`, set `DATABASE_URL`, `OWNER_ID` and `APP_ACCESS_CODE`, change to `backend`, run `python -m app.database`, then `uvicorn app.main:app --reload`. For native backend tests, set `TEST_DATABASE_URL` to a disposable database and run `python -m pytest -q` from `backend`; without that variable database tests explicitly skip. In `frontend`, run `npm ci` then `npm run dev`; the proxy defaults to http://localhost:8000. `npm run lint`, `npm test` and `npm run build` are the native frontend checks.

## 10. Review state

The application services are left running for inspection. The isolated test stack was removed with its documented `down` command, without application volume deletion. The implementation is maintained on the Gabriel branch. The local commit includes source, migrations, reusable regression tests, dependency manifests, integration configuration and documentation. Reference DOCX files, the task input, environment files, caches and generated artifacts are excluded. No push or merge was performed.
