# Gabriel Marte: implementation report

## 1. Completed responsibilities and evidence

`WorkDivision.docx` assigns Gabriel the PostgreSQL Owners/Submissions/Sales/Baselines schema, transactional `POST /api/v1/confirm`, database rollback/duplicate-confirmation tests, interactive Draft Review, and confirmed Sales History. These portions are implemented. The defects reproduced during the initial section 11 audit are corrected by the approved scoped patch in section 12. The local patch is ready for team review; team contract acceptance and wider integration gaps remain before merge. The Milestone 3 data dictionary, lifecycle discussion, ERD, and state diagram guided the design; Milestone 2 supplies the baselined numeric and confidence rules. Milestone 1 supplies the original product context. All four DOCX files were parsed directly from their Word XML; no Word installation was needed.

The original repository contained FastAPI endpoint placeholders and the default React/Vite page. Existing access-code protection and analysis/guidance placeholders remain. Development is on `gabriel/data-state-implementation`. Implementation commit c61ed711efddbd3b27764d87d66116e46a10d181 was previously committed and pushed with user approval. The current review makes no new commit, push, or merge. The user-provided task file and DOCX files remain untouched and untracked.

## 2. Files created

| File | Purpose |
| --- | --- |
| `backend/app/database.py` | PostgreSQL connections; ordered, advisory-locked transactional SQL migration runner. |
| `backend/migrations/001_data_state.sql`, `002_nonblank_labels.sql` | Four domain tables, keys, indexes, lifecycle/consistency triggers and whitespace-only label protection. Both existing migrations are preserved. |
| `backend/migrations/003_remove_label_length_limits.sql` | Additive upgrade removing the undocumented label-length caps while retaining nonblank checks and existing data. |
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

This section describes the complete feature branch relative to main, including the original implementation. Section 12 lists only files changed by the approved review patch; Compose and frontend shell changes below were already present and were not edited again.

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

Migrations and their ledger entries commit together. `001_data_state.sql` initializes the schema; additive `002_nonblank_labels.sql` rejects labels consisting solely of tabs/newlines as well as spaces. `003_remove_label_length_limits.sql` removes only the old upper-length constraints; the nonblank checks from 002 remain. Startup migration execution is serialized by an advisory transaction lock. Re-running migrations skips applied versions; no destructive down migration or volume deletion is used.

**Documented ambiguity:** Milestone 3 section 3 explicitly identifies integer-only quantities as an unapproved change. Milestone 2 and the existing API accept positive fractional quantities. This implementation preserves decimals, rather than silently adopting an unapproved restriction. The approved review removed the implementation-only 14-integer-digit/6-decimal and 200-character caps. Finite/sign/nonblank checks and PostgreSQL's inherent storage limits remain. The scaffold's existing 1-100 confirmation-row limit remains. Analysis-policy differences remain for the team to resolve; neither competing analysis formula was implemented here.

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

`draftLineId` is required for an extracted row. On first confirmation, if stored extracted rows exist, an unlinked row must explicitly declare `source: "MANUAL"`; omission alone no longer bypasses verification. Empty-draft/manual-fallback callers remain compatible without a source declaration. Linked rows are AI_EXTRACTED, and declaring them MANUAL is rejected. `verifiedFields` is an additive optional field naming `itemName`, `quantity` or `unitPrice`. The user approved this scoped implementation, but these wire details still need team acceptance because the supplied Milestone 3 document does not specify them. For stored draft lines, unchanged uncertain/unscored fields must be explicitly verified; edited values satisfy review. Unknown/repeated draft line IDs are rejected. AI-assisted corrections retain AI_EXTRACTED source; new manual rows use MANUAL.

The repository locks the owner's submission with `SELECT ... FOR UPDATE`, checks eligibility, inserts all lines, updates status to CONFIRMED and clears the persisted draft in one transaction. Exceptions during insertion, status change, or commit roll back the complete operation. UUIDs are generated only for rows in that save. Concurrent requests serialize on the submission lock.

First success: HTTP 201, `{submissionId, recordIds, created: true}`. Repeated validated confirmation: HTTP 200 with the same IDs and `created: false`, without any additional inserts. This implements Milestone 3's explicit idempotency rule even though the task wording calls for duplicate confirmations to be "blocked."

Confirmation input/verification: 422. Extraction input validation: 400, including missing/malformed form dates, with `detail: [{loc, msg, type}]`; raw input is excluded from that validation response. OpenAPI describes extraction 400 without advertising an automatic 422. Other routes keep their existing validation handling. Unknown or differently owned submission: 404. Ineligible state: 409. Authentication failure: 401. Database failure: 500 with a retry message; submitted values are not returned in database logs. Invalid requests leave rows/status/draft unchanged.

### Added integration endpoints

- `GET /api/v1/sales?limit=100&offset=0`: `{records, limit, offset}`, with 1-100 rows per page. Records include id, submissionId, saleDate, itemName, quantity, unitPrice and source. Exact decimal values serialize as strings. Reads join the parent submission and filter owner and CONFIRMED status; drafts never enter the ledger.
- `GET /api/v1/submissions/{id}`: reopens the stored draft's date, state, lines and manual-entry flag for the current owner.
- `POST /api/v1/submissions/{id}/manual-review`: explicitly moves REJECTED or AI_UNAVAILABLE into NEEDS_VERIFICATION, clearing any unusable draft. Already-reviewing requests are harmless; other states return 409. This is Gabriel's lifecycle/review boundary, not an AI/capture implementation.

Repository create/update operations assign and persist missing draft IDs while preserving valid supplied IDs. Legacy payloads are normalized deterministically on read and confirm without rewriting them during GET. Duplicate/malformed identifiers or payload containers return controlled 422 errors. Ingestion should return `get_submission(submission_id)` after storing draft rows so the client receives the normalized IDs.

The existing extraction placeholder now creates a real submission and returns AI_UNAVAILABLE with empty lines and an explicit unimplemented message. It neither pretends to extract sample products nor stores unsanitized raw inputs. Analysis/guidance remain unchanged placeholders.

## 6. Frontend behavior

DraftReview pre-fills readable values, blanks LOW fields, highlights MEDIUM/missing/unrecognized confidence, requires explicit verification or edits, and displays field-specific validation errors. Owners can add/remove rows and review fractional quantities and zero prices. Added rows declare MANUAL source; loaded rows retain extracted provenance. Missing/duplicate IDs do not suppress confidence warnings and block confirmation with a reopen message. Each row has a separate UI key, so duplicate server IDs cannot couple edits or removals. Decimal strings are validated without JavaScript Number conversion, avoiding rounding/underflow and the removed precision caps. While saving or after successful confirmation, editing is disabled. Failed saves retain the current draft for retry; rejected inputs have an explicit manual-review action.

SalesHistory displays date/item/quantity/unit price/source from PostgreSQL through the API, with loading/empty/error states, retry/refresh and pagination. Successful confirmation reloads the first page, making new sales visible. Reopening a submission resets the component to the retrieved state. Access codes are kept in component memory rather than persisted in browser storage.

The two components accept an injected `request(path, options)` client. DraftReview additionally accepts the captured `draft` and `onConfirmed` callback. Luis can replace the temporary mount point with the shared shell/API client; Jose can pass the extraction response directly instead of requiring ID lookup. No provider keys are sent to React.

## 7. Executed verification

Current post-fix verification: **58 backend tests and 20 frontend tests passed; lint, production build, live HTTP smoke, migration upgrade/reapplication, and integrity inspection passed.** Exact post-fix results and commands appear in section 12.

The initial implementation session recorded the following execution results. These are historical results; the initial November 2 audit and its failing diagnostic cases are recorded separately in section 11:

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

The implementation remains on gabriel/data-state-implementation at c61ed711efddbd3b27764d87d66116e46a10d181, previously pushed with user approval. The approved review patch is uncommitted and unstaged; its eight changed/new files and verification are listed in section 12. The local fixes have not been published to PR #1. No new commit, push, merge, rebase, reset, or action on Pull Request #1 was performed. The disposable review stack was removed without deleting application volumes. Reference DOCX files and task prompts remain untouched and untracked; no environment files, generated artifacts, or temporary test files were added.

## 11. Initial review before patch approval - November 2, 2026

**Historical audit snapshot:** this section preserves the pre-fix reproductions and test evidence. Its pending-approval and unresolved-defect statements describe that earlier stage. The user subsequently approved the scoped patch; section 12 provides the current implementation and post-fix results.

### Scope, evidence, and current status

Read all sections 1-13 and Appendix A of `Milestone_3_Formal_Analysis_and_Design_Updated.docx`, including its explicit design-alignment notes. Rechecked `WorkDivision.docx`, the relevant Milestone 2 baseline requirements, current application/repository/schema/component/test files, and shared-file changes. The DOCX text was read directly from Word XML; no reference documents were altered.

Fetched `origin/main` with `git fetch origin main`. It remains `630c5df3303e4536e299d26ab71863a589bc2dbc`, an ancestor of feature-branch HEAD `c61ed711efddbd3b27764d87d66116e46a10d181`. The feature branch is one commit ahead and matches its remote tracking branch. There are no divergent commits on fetched main. This does not establish the state of teammates' unmerged branches or local work, and no merge was attempted.

**Ready for team review of findings and proposed contracts; not ready to merge.** Two defects reproduced despite the existing suites passing. The user's current instructions require approval before changes that affect teammates, including shared API contracts. A scoped approval request is pending. No source fixes have been applied during this resumed review.

### Confirmed defects and proposed corrections

| Finding | Actual reproduction | Correction proposed, not yet applied |
| --- | --- | --- |
| Verification bypass and incorrect provenance | A stored row with MEDIUM unit-price confidence returns 422 when linked and unverified, and 201 when linked with `verifiedFields: ["unitPrice"]`. Omitting `draftLineId` from the same unchanged values returns 201 and stores MANUAL. | Preserve linkage for AI-assisted rows. When a submission has extracted rows, require an explicit `source: "MANUAL"` declaration for newly entered manual rows instead of treating absent metadata alone as manual intent. Reject inconsistent source/link combinations. Add regressions for omitted linkage, verified/edited extracted rows, mixed rows, and manual fallback. This is a proposed shared contract, not an existing Milestone 3 requirement. |
| Missing persisted line ID causes an unhandled 500 | A draft containing the documented item, quantity, unit-price and bands fields but no `lineId` produces a plain-text HTTP 500 on confirmation. The transaction correctly preserves NEEDS_VERIFICATION and zero sales records. | Generate stable IDs at the repository boundary, preserve valid existing IDs, handle older drafts consistently on read/confirm, and return controlled errors for malformed/duplicate identifiers. DraftReview must not suppress confidence warnings when an ID is absent. Add backend and frontend regressions. |
| Extraction date validation disagrees with documented HTTP 400 | Malformed `saleDate` returns 422 with a list under `detail`; missing image/text returns 400 with an object under `detail`. | Scope FastAPI request-validation handling to extraction so invalid upload/date input returns 400 with field information. Retain confirmation 422 unless the team approves a different contract. Document the distinction and test invalid extraction creates no submission. |

The first two failures were reproduced using synthetic data in a fresh `audit_...` schema in the disposable PostgreSQL database. The script was supplied through stdin, not saved in the repository. The schema was dropped in a `finally` block. This is diagnostic evidence, not a claim that new regression tests exist or that either defect has been fixed.

### Milestone 3 compliance and unresolved decisions

- **Database and lifecycle:** Four domain tables, owner/submission relationships and cascades, six state values, positive/nonnegative finite numeric constraints, parent sales-date consistency, confirmed-only history, locked transactional confirmation, rollback and repeat-confirm idempotency agree with the data and lifecycle design. Existing migrations `001_data_state.sql` and `002_nonblank_labels.sql` were preserved. All readable drafts belong in NEEDS_VERIFICATION until confirmed, including fully high-confidence drafts, as the state explanation clarifies.
- **Quantity type:** Section 3 explicitly calls integer-only quantities a proposal without documented approval. Milestone 2 FR-202 permits finite positive quantities. Keeping fractional quantities is defensible until the team baselines a change; do not silently change the database to integers.
- **Undocumented limits:** The implementation added a 200-character item-name cap and 14-integer-digit/6-decimal API/UI limits. These are not in FR-202 or the supplied Milestone 3 contract. Proposed correction: remove those extra product limits, preserving nonblank/finite/sign validation and existing migrations; use an additive migration to relax the two label-length constraints. The scaffold's existing 1-100 confirmation-row limit is a separate inherited contract. Limit changes await approval.
- **`verifiedFields`:** FR-208 requires an edit or explicit verification for medium/missing confidence. It does not prescribe this JSON field, and Milestone 3 never names it. The extension and manual-source/linkage protocol require team agreement before being treated as the shared contract. Existing `lineId`/`draftLineId` fields appear in the main scaffold, but ID generation/persistence and required linkage are not fully formalized by the document.
- **HTTP 400 versus 422:** Milestone 3 explicitly requires 400 for invalid extraction input, including a bad sales date. It does not specify a confirmation-validation HTTP status. Therefore extraction's automatic 422 is a discrepancy, while confirmation 422 is a current implementation choice requiring contract agreement, not proof of a violated global 400 rule. The malformed-date behavior also exists in the original scaffold. First confirmation 201 and database-save failure 500 match the document; repeated confirmation returns existing IDs as required, with HTTP 200 as the implementation's repeat-success choice.
- **Error response shape:** `detail` currently varies between string, object and list. Milestone 3 requires field-level extraction errors but supplies no agreed universal JSON error envelope. Coordinate any normalization with Luis's API client and Jose's ingestion handler. Do not claim an undocumented envelope is already approved.
- **API naming:** Keep the scaffold's wire `submissionId`/`lines`; conceptual `confirm(submissionId, records)` method notation does not establish that the JSON key must be renamed to `records`. New draft/history/recovery routes and decimal-string responses still need shared contract acceptance.
- **Frontend:** DraftReview and SalesHistory implement the assigned review/history behavior and accept an injected client. The missing-ID warning gate is defective. The app shell is a temporary mount using plain CSS rather than the proposed Tailwind shell; integration and styling belong with Luis. Capture/photo reference/manual-entry creation and the results dashboard remain teammate integration work.
- **Architecture and repository API:** React/FastAPI/PostgreSQL boundaries are present. The code uses modules rather than completing the documented controller/service classes and dependency injection. Avoid a unilateral shared architecture rewrite. `get_confirmed_records` currently serves paginated UI history; the documented owner/date-range query and baseline save/latest repository operations still need agreement and implementation with Diushen. The default 100-row page must not be used as complete analysis history.
- **Other incomplete team features:** Real AI ingestion, signature validation, EXIF stripping, contact masking, object storage, timeout/retry logic, analysis/guidance and their calculations are not implemented by the placeholder routes. The review does not claim whole-project Milestone 3 compliance. Owner deletion, raw-input/draft retention and cleanup, analysis formula changes and guidance fallback policy remain open as the document itself records.

### Shared-file coordination

| File or boundary | Existing feature-branch change compared with fetched main | Coordination needed before another edit |
| --- | --- | --- |
| `docker-compose.yml` | Frontend backend-proxy target; backend health check; frontend waits for healthy backend. Existing services, ports and named volumes remain. | Luis: deployment/configuration ownership. No additional Compose change is proposed for the defect fixes. |
| `backend/app/main.py` | Migration startup; confirmation models/handler; database exception handling; draft/history/manual-review routes; persisted AI_UNAVAILABLE extraction placeholder. Analysis/guidance placeholders and access dependency remain. | Jose: extraction validation and returned IDs; Luis: shared error/client behavior and startup; Diushen: later controller integration. Current proposal changes confirmation provenance validation and extraction validation handling only, plus removal of unapproved numeric/name limits if approved. |
| Frontend shell (`App.jsx`, `App.css`, `index.css`) and Vite configuration | Replaces the default Vite page with component mounting, temporary client/access-code form and global styles; adds local proxy configuration. | Luis: shell/client/styles; Jose: capture handoff; Diushen: dashboard mounting. No new shell or Vite edits are proposed in the scoped defect patch. |
| `repository.py` / `DraftReview.jsx` | Gabriel-owned components also define data exchanged with capture and analysis. | Agree stable IDs, explicit manual intent, verification metadata and future repository adapters. Ownership of a file alone does not remove contract impact on teammates. |

No current conflict from divergent main commits was found. Overlap in shared integration areas remains a coordination risk; unmerged teammate work was not overwritten or presumed absent.

### Fresh verification executed in this resumed review

Existing test images from the preceding audit were reused because tracked source was unchanged; no full project rebuild was performed. All execution below used the isolated Compose project `bodega-gabriel-review`, with unexposed PostgreSQL in tmpfs and no source bind mounts. Application PostgreSQL volumes were not targeted.

| Check | Actual result |
| --- | --- |
| Development Compose configuration | `docker compose -f docker-compose.yml config --quiet`: exit 0. |
| Isolated Compose configuration | `docker compose -p bodega-gabriel-review -f compose.test.yml config --quiet`: exit 0. |
| PostgreSQL backend suite | 29 passed, 1 Starlette/httpx deprecation warning, 15.89 seconds; zero failed/skipped. |
| Frontend lint | ESLint exited 0. |
| Frontend tests | 1 test file, 8 tests passed; 9.61 seconds total. |
| Frontend production build | Vite 8.3.5, 19 modules, exit 0; build completed in 773 ms. |
| HTTP smoke | Passed frontend HTML, Vite proxy, persisted draft, first confirmation 201, repeat 200 with identical IDs, exact-decimal ledger and unauthorized 401. |
| Migration reapplication | `python -m app.database` exited 0 in backend-smoke. Ledger still contains exactly `001_data_state.sql` and `002_nonblank_labels.sql`. |
| Database consistency | Zero submissions violated the relationship between CONFIRMED state and existence of sales rows. |
| Isolated schema cleanup | Zero schemas matching `test_%` or `audit_%` after suites/probes. |
| Dependency lock | Node parsed lockfile v3 and verified all 16 direct dependencies match package.json and have package entries. No lock regeneration or dependency changes. |
| Defect probes | Two confirmed failures described above; passing existing suites do not cover these cases. |

Exact Compose commands executed from the repository root:

```sh
docker compose -f docker-compose.yml config --quiet
docker compose -p bodega-gabriel-review -f compose.test.yml config --quiet
docker compose -p bodega-gabriel-review -f compose.test.yml run --rm backend-tests
docker compose -p bodega-gabriel-review -f compose.test.yml run --rm --no-deps frontend-checks sh -c "npm run lint && npm test && npm run build"
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke up -d backend-smoke frontend-smoke
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke run --rm http-smoke
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke exec -T backend-smoke python -m app.database
docker compose -p bodega-gabriel-review -f compose.test.yml exec -T postgres-test psql -U bodega_test -d bodega_test -c "SELECT version FROM schema_migrations ORDER BY version; SELECT count(*) AS leftover_test_schemas FROM information_schema.schemata WHERE schema_name LIKE 'test_%' OR schema_name LIKE 'audit_%'; SELECT count(*) AS inconsistent_submissions FROM submissions s WHERE (s.status = 'CONFIRMED') <> EXISTS (SELECT 1 FROM sales_records r WHERE r.submission_id = s.id);"
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke down
```

The temporary Python diagnostic used `docker compose -p bodega-gabriel-review -f compose.test.yml run --rm --no-deps -T backend-tests python -` with stdin. All containers and the network for this review project were removed successfully. No application volume deletion was requested or performed. Normal application-stack startup and npm clean installation were verified in earlier sessions, not rerun in this review. An initial PowerShell lockfile parser failed on the empty package-root key; the subsequent Node JSON check succeeded. This was an audit-tool limitation, not a lockfile defect.

### Changes made and approval boundary

This resumed review updates only `docs/GABRIEL_IMPLEMENTATION.md`: corrects stale pre-commit/no-push statements, distinguishes historical tests from fresh evidence, records reproduced defects and compliance gaps, and describes the proposed patch and integration risks. It does not claim any source defect has been corrected.

Pending approval covers stable draft IDs, explicit manual-source declaration alongside extracted rows, continued `verifiedFields` support as a documented proposal, extraction HTTP 400 handling with confirmation 422 retained, and optional removal of undocumented label/numeric limits. The patch would include backend/frontend regressions and an additive migration for label limits if approved, without altering existing migrations, Compose, the frontend shell, main branch or PR #1. After approval and implementation, rerun the affected suites/build/smoke and update this report with actual post-fix results before considering merge readiness.

## 12. Approved scoped patch and shared-contract coordination

The user approved the scoped feature-branch patch after the section 11 review. Before editing shared application code, the following contract changes were communicated and recorded here. This is user authorization to implement and present the patch for team review; it is not a claim that Jose, Luis, or Diushen have separately approved the wire contract.

- Keep `submissionId`/`lines`, `draftLineId`, and `verifiedFields`. Linked rows retain AI_EXTRACTED provenance, including corrections. An explicit MANUAL source with a draft link is invalid. An AI_EXTRACTED source without a valid link is invalid.
- For a submission with stored extracted rows, a row without `draftLineId` must explicitly declare `source: "MANUAL"`. Existing empty-draft/manual-fallback requests remain compatible without that declaration. The Add row control declares manual intent; existing extracted rows cannot silently become manual because their IDs are missing.
- Preserve valid supplied draft IDs and generate stable IDs for missing ones at the repository boundary. Legacy drafts receive the same deterministic IDs when reopened and confirmed. Malformed or duplicate supplied IDs produce controlled validation errors. Ingestion should return the repository's normalized draft to the frontend after persistence.
- Extraction validation, including missing/malformed form dates, returns HTTP 400 with field details in the existing FastAPI-style `detail` list (`loc`, `msg`, `type`). Confirmation input and verification errors retain 422. Authentication, unknown-submission, lifecycle-conflict, and database-error statuses remain unchanged. No application-wide error-envelope rewrite is planned.
- Remove the undocumented 200-character label and 14-integer-digit/6-decimal product limits. Preserve finite/positive quantity, finite/nonnegative price, nonblank labels, and the scaffold's existing confirmation row limit. PostgreSQL's inherent storage limits still apply. Add migration 003 to relax label-length checks; do not rewrite 001 or 002.
- Files planned for implementation: `backend/app/main.py`, `backend/app/repository.py`, one additive migration, Gabriel's DraftReview component, backend/frontend regression tests, HTTP smoke assertions, and this report. No Compose, frontend shell/global styles/client, dependency, or main-branch changes are authorized by this patch.

The approved changes and completed verification results follow.


### Completed patch

| File | Review change |
| --- | --- |
| `backend/app/repository.py` | Validates draft structure and IDs, generates stable missing IDs at persistence/read/confirm boundaries, and enforces explicit manual intent alongside extracted rows while preserving AI provenance and transactional behavior. |
| `backend/app/main.py` | Adds the optional constrained source field, removes undocumented numeric/name caps, scopes form-validation 400 handling to extraction, supplies field details, and aligns extraction's OpenAPI responses. Existing authentication, analysis/guidance placeholders, and route availability are preserved. |
| `backend/migrations/003_remove_label_length_limits.sql` | Drops the two original upper-length constraints. Existing 002 nonblank constraints, data, indexes, foreign keys and lifecycle rules remain. No existing migration is edited. |
| `frontend/src/components/DraftReview.jsx` | Keeps confidence warnings independent of missing IDs, blocks incomplete/duplicate linkage, isolates row edit keys, declares manual source for added rows, and validates exact decimal text without artificial length/precision caps or Number rounding. |
| `backend/tests/test_database.py` | Adds 29 regression cases; updates the migration count, the mixed-manual fixture's explicit intent, and replaces the obsolete precision-rejection case with a nonfinite-price rejection. Total: 58 cases. |
| `frontend/src/components/SalesReview.test.jsx` | Adds 12 regression cases for missing/duplicate IDs, independent edits, source declarations, long labels, exact fractional/large/tiny values, and invalid quantities. Total: 20 cases. |
| `backend/tests/smoke_http.py` | Adds live extraction-400 field-detail and confirmation-422 assertions to the existing proxy/persistence/idempotency/authentication smoke test. |
| `docs/GABRIEL_IMPLEMENTATION.md` | Records the authorization and contract boundary before shared-code edits, updates current behavior, preserves pre-fix evidence, and documents post-fix results and remaining integration decisions. |

The omitted-link reproduction now returns 422 and saves no records. Explicit verification or an edited linked row succeeds as AI_EXTRACTED. A newly added row can still save as MANUAL by explicitly declaring that intent; existing empty-draft manual fallback continues to work. This protocol records the owner's declaration, not an independently provable fact about how the value was entered.

The missing-ID reproduction now reopens with stable generated IDs and confirms through those IDs without an unhandled 500. Existing supplied IDs are preserved. Unknown/duplicate request links and malformed stored draft structures return controlled errors and leave state/data unchanged. The frontend never silently treats an incomplete extracted row as manual.

Migration-upgrade regression starts from the unchanged 001/002 SQL in a separate temporary schema containing confirmed sales and a baseline. It applies 003 twice through the migration runner, checks the original record identity and state, accepts labels longer than 200 characters, and confirms blank labels remain invalid. Existing rollback, concurrency, idempotency, ownership, state-transition, exact-decimal and cascade tests remain passing.

### Actual post-fix results

All checks below ran successfully after the source/test patch; no test failures or skips occurred. The isolated images were refreshed with the changed source, while pip and npm dependency layers were cached. No dependency manifest or lockfile changed.

| Check | Result |
| --- | --- |
| Backend PostgreSQL suite | **58 passed**, 1 pre-existing Starlette/httpx deprecation warning, **30.96 seconds**. |
| Frontend interaction suite | **20 passed** in one file, **10.69 seconds** total. |
| Frontend ESLint | Exit 0. |
| Vite production build | Exit 0; **19 modules**, **467 ms**. Generated output stayed inside the disposable container. |
| Development and isolated Compose configurations | Both `config --quiet` commands exited 0. |
| Live HTTP smoke | Passed HTML/proxy, persisted draft, 201 confirmation, 200 repeat with identical IDs, exact decimal history, unauthorized 401, extraction validation 400 with field location, and confirmation validation 422. |
| PostgreSQL migrations | All three ledger versions present: 001, 002, 003. Explicit reapplication exited 0. Upgrade/data-preservation behavior also passed in the backend suite. |
| Database integrity inspection | **0** inconsistent CONFIRMED/record relationships; **0** mismatched parent/record sales dates. |
| Test cleanup | **0** leftover test/audit schemas. Disposable project removed without application-volume deletion. |
| Git scope/whitespace | Protected Compose, shell and existing migration paths unchanged from HEAD. `git diff --check` passed. Nothing staged. |

Exact post-fix commands, from the repository root:

```sh
git fetch origin main
docker compose -f docker-compose.yml config --quiet
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke config --quiet
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke build backend-tests frontend-checks backend-smoke frontend-smoke http-smoke
docker compose -p bodega-gabriel-review -f compose.test.yml run --rm backend-tests
docker compose -p bodega-gabriel-review -f compose.test.yml run --rm --no-deps frontend-checks
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke up -d backend-smoke frontend-smoke
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke run --rm http-smoke
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke exec -T backend-smoke python -m app.database
docker compose -p bodega-gabriel-review -f compose.test.yml exec -T postgres-test psql -v ON_ERROR_STOP=1 -U bodega_test -d bodega_test -c "SELECT version FROM schema_migrations ORDER BY version; SELECT count(*) AS leftover_test_schemas FROM information_schema.schemata WHERE schema_name LIKE 'test_%' OR schema_name LIKE 'audit_%'; SELECT count(*) AS inconsistent_submissions FROM submissions s WHERE (s.status = 'CONFIRMED') <> EXISTS (SELECT 1 FROM sales_records r WHERE r.submission_id = s.id); SELECT count(*) AS mismatched_sales_dates FROM sales_records r JOIN submissions s ON s.id=r.submission_id WHERE r.sale_date<>s.sale_date;"
docker compose -p bodega-gabriel-review -f compose.test.yml --profile smoke down
git diff --check
```

The `frontend-checks` service executes `npm run lint && npm test && npm run build`. Tests use only the isolated, unexposed PostgreSQL service and random schemas. The application database and its persistent volumes were not migrated or populated by this patch verification.

### Remaining issues and PR readiness

- The two reproduced defects, extraction 400 discrepancy and undocumented product caps are corrected. No known failure remains in this scoped patch's executed checks.
- Team acceptance of `verifiedFields`, stable draft IDs, explicit manual-source declarations, extraction field-error shape and confirmation 422 remains part of PR review. The user authorized their implementation; no separate teammate approval or external coordination message is claimed.
- The previously identified broader Milestone 3 gaps remain: date-range/complete analysis retrieval and baseline repository adapters with Diushen; ingestion/privacy/storage and normalized draft handoff with Jose; shell/client/Tailwind/deployment integration with Luis; and agreed retention/deletion policies. Integer-only quantities and analysis/guidance policy changes still require SRS alignment. This patch does not claim completion of teammates' features.
- Existing error shapes on other endpoints are preserved. A universal error-envelope change is deferred to shared-client coordination. The Starlette TestClient deprecation warning remains. This work did not rerun npm audit or change dependencies.
- A fresh fetch still shows main at `630c5df3303e4536e299d26ab71863a589bc2dbc`, an ancestor of feature HEAD `c61ed711efddbd3b27764d87d66116e46a10d181`. No main commits were replaced or changed, and no merge was attempted. Shared-file overlap described in section 11 still requires peer review; unmerged teammate branches were not altered.
- **The local patch is ready for team review. PR #1 itself is unchanged and does not yet contain these fixes.** Publishing them requires the user's separate commit/push authorization; merging still requires the team's contract/integration review. No commit, push, merge, rebase, reset, force-push, or PR action was performed.

## 13. Integration update (`integration/m3-completion`)

The final Milestone 3 (October 23) supersedes parts of this report. Changes on the integration branch:

- **Limits restored.** The final M3 defines the limits that sections 4 and 12 call "undocumented": 2 decimals, 99,999.99, 1–100 characters. Migration 003 is unchanged; migration 005 re-adds them `NOT VALID` (decision D-03).
- **Lifecycle per Diagram 4.** REJECTED and AI_UNAVAILABLE confirm directly; `manual-review` is removed (D-04).
- **Unmerged branch work ported.** The substance of `cb55fce` is now in `005_final_data_design.sql`, `repository.py` and `retention.py`, rebuilt on current `main` instead of merged (D-01, D-02): manual entries, pending drafts, deletion, the date-range ledger, the 30-day purge, the image outbox, and snapshots.
- **Your work is preserved.** The original branch and stash are kept in `backup/gabriel-data-state-cb55fce` and `backup/gabriel-stash-2026-11-08`.
- **Current status:** `docs/DATA_DICTIONARY.md` and `docs/TRACEABILITY.md`.

## 14. Milestone 4 review update (2026-11-10)

Reviewed against `WorkDivision.docx` and the final Milestone 3; the full mapping is in `docs/TRACEABILITY.md`.

- **Deliverables complete locally:** the four-table schema (Alembic head `006`), transactional and idempotent `POST /confirm`, the rollback and duplicate-confirmation tests, the Draft Review table and the Sales History ledger.
- **Withdrawn:** migration 007 and the 24-hour input deadline (D-27) and the retry transition (D-29). Retention is M3 7.4 again: `draftPayload` cleared on confirmation, input kept 30 days. A local development database that already applied the unreleased 007 must be recreated (`docker compose down -v`).
- **Added:** `backend/seeds/synthetic_sales.sql`, the SQL seed script WorkDivision's execution steps ask for (D-34); `/sales` paging (D-32); confirmation errors carry field paths that the review table shows on the row.
- **Open:** 422 or 400 for invalid confirmation rows (D-30).
