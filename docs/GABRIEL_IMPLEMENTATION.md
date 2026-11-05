# Gabriel-only local commit scope — November 5, 2026

This packaging review takes precedence over the historical session reports below when describing the contents of Gabriel's local commit. It includes only backend/data responsibilities and their validation/configuration/documentation. All frontend changes, environment files/templates, archives, imported specification documents and teammate implementations remain unstaged. No implementation working file was rewritten to prepare the commit; shared files were partially staged.

## Ownership boundary

- `main.py`: stage data validation/error envelopes, exact decimal responses, confirmation, manual entries, confirmed history, pending submissions, retention startup and post-commit deletion. Extraction, analysis and guidance handlers remain identical to their previous committed integration placeholders. The newer teammate implementations remain in the working tree.
- `schemas.py`: stage only the `BaselineEstimate` persistence input schema; analysis/guidance response models remain unstaged.
- `test_workflows.py`: stage storage fixtures and the six data-owned owner-isolation, deletion, forced-restart, private-bucket, durable-retry and safe-log tests. AI, privacy and analysis integration tests remain unstaged. The storage-expiry test that imports Jose's untracked sanitizer also remains unstaged.
- Leave the extraction-compensation HTTP regression and fallback-initialization HTTP regression unstaged with the extraction integration they exercise. Their low-level durable orphan-discovery and transaction coverage remains included.
- Leave `analysis.py` and `test_analysis_business_date.py` unstaged: the small business-date correction is inside Diushen's currently untracked analysis implementation and cannot be committed independently without absorbing that component. No completed local work was discarded.

## Verification of the staged snapshot

The staged index was exported into a separate temporary directory, built as `bodega-gabriel-commit-backend`, and tested without unstaged teammate modules. **199 backend tests passed, zero failures/skips, in 27.46 seconds**, with one existing Starlette/httpx deprecation warning. PostgreSQL and MinIO were real isolated services. This includes all four new migrations, populated upgrade/rollback checks, confidence and state boundaries, mixed draft IDs, canonical snapshot lookup, confirmation idempotency/concurrency, ten forced process restarts, durable deletion/crash recovery, orphan discovery, R2 compatibility mocks, MinIO signed retrieval, and business-timezone confirmation/manual validation.

```powershell
docker build -t bodega-gabriel-commit-backend <temporary-staged-snapshot>/backend
docker run --rm --network bodega-gabriel-validation_default -e TEST_DATABASE_URL=postgresql://bodega_test:test-only-password@postgres-test:5432/bodega_test -e TEST_S3_ENDPOINT=http://minio-test:9000 bodega-gabriel-commit-backend python -m pytest -q -ra --tb=short
git diff --cached --check
```

The earlier **286 passing backend tests** covered the complete working tree, including unstaged teammate integrations. That result is not presented as the test count for this isolated commit. Frontend changes are excluded entirely; previous frontend results remain historical working-tree evidence.

Staged-file review found no private keys, live provider tokens, credentials or environment files. The only credential literals are explicitly disposable test fixtures. R2's S3 `GetBucketPolicy` incompatibility is addressed by supported managed/custom-domain privacy checks; uploads require a backend `CLOUDFLARE_API_TOKEN` with read access to those settings and fail closed if verification is unavailable. No live R2 credentials/connectivity were available. MinIO retains its private-policy guard.

Formal `verifiedFields` contract approval and SQLAlchemy/Alembic coordination with Luis remain outstanding. Team extraction, analysis/guidance, frontend shell/API integration and final deployment are not certified by this commit. Migrations 001–003 are untouched; 004–007 preserve compatible records and refuse incompatible historical data without destructive remediation. Nothing is pushed, merged or submitted as a PR by this packaging operation.

## Exact included files

```text
README.md
backend/Dockerfile
backend/app/clock.py
backend/app/data_lifecycle.py
backend/app/database.py
backend/app/image_cleanup.py
backend/app/lifecycle.py
backend/app/main.py                         (partial staging)
backend/app/repository.py
backend/app/schemas.py                      (partial staging)
backend/app/seed.py
backend/app/storage.py
backend/migrations/004_baseline_v1.sql
backend/migrations/005_final_integrity.sql
backend/migrations/006_strict_final_schema.sql
backend/migrations/007_image_deletion_outbox.sql
backend/requirements.txt
backend/tests/test_database.py              (partial staging)
backend/tests/test_final_contract.py
backend/tests/test_image_cleanup.py         (partial staging)
backend/tests/test_review_regressions.py
backend/tests/test_storage_compatibility.py
backend/tests/test_workflows.py             (partial staging)
compose.test.yml
docs/DATA_STATE_COMPLETION.md
docs/GABRIEL_IMPLEMENTATION.md
```

# Historical session reports

# Current closure review: durable image deletion retry

November 5, 2026. This review supersedes earlier statements below that image-deletion retries are unfinished. Authority: final Milestone 3, Sections 5.4 (delete after database commit; retry on next purge) and 7.4 (retention and deletion), `docs/Milestone_3_Final.pdf`, October 23, 2026. Its SHA-256 remains `9efabad91d280952c282325a46bc1657c60c033b2eb82c4d1d68ac77f025f646`. Review basis is the reported post-commit key-loss finding and the final PDF; no separate audit attachment was accessible in this turn.

## Outcome and scope

**Gabriel's durable image-deletion retry implementation is complete and verified.** A failed storage request no longer loses the image key after the submission transaction commits. Confirmation and uncertain-field validation are unchanged. No teammate component, frontend source, dependency manifest, existing migration, persistence framework, or public API contract was changed in this closure review.

Overall final submission remains conditional on the formal approvals and teammate integrations listed below. Passing this suite does not establish whole-project Milestone 3 compliance.

## Durable mechanism

New migration `007_image_deletion_outbox.sql` adds an operational `image_deletion_jobs` outbox. It changes no existing rows or migrations 001-006. The queue has no FK to submissions or owners, so deleting a parent cannot discard its cleanup obligation.

| Column | Definition / purpose |
| --- | --- |
| bucket_name | Nonblank text; part of PK; pins the intended bucket |
| image_key | Nonblank text; part of PK; private storage identifier |
| created_at | UTC timestamptz, database default now |
| attempt_count | Nonnegative integer, initially zero |
| last_attempt_at | Nullable UTC timestamptz; last committed attempt |

The submission delete, sales cascade, owner-snapshot invalidation and outbox insertion use the **same PostgreSQL transaction**. Failure of any database step rolls them all back. No image delete is attempted before that commit. An unknown bucket prevents the destructive transaction and preserves the submission/key for correction.

After commit the endpoint attempts the queued delete and still returns its documented 204 when storage is unavailable. Failed jobs persist. Startup and the existing daily RetentionService sweep drain pending jobs, including keys whose original submission is already gone. Retention cleanup also queues keys atomically before clearing raw references or removing expired submissions, then accesses storage after commit.

`image_cleanup.process_pending` attempts each selected job once per invocation. Each attempt locks its job using `FOR UPDATE SKIP LOCKED`; concurrent workers cannot process the same locked job. A failed key does not prevent attempts for other keys. Only jobs for the configured bucket are processed, and any key still referenced by a live submission is protected. Success removes the job; failure commits attempt metadata and leaves it retryable indefinitely. No deadline silently discards obligations.

If the worker crashes after storage accepts DELETE but before the job acknowledgement commits, the job remains. The next process safely repeats S3 DELETE; deleting a missing key is idempotent. No database row is recreated and no sales confirmation is repeated. The daily task now also survives transient PostgreSQL failures, logs a sanitized message, and attempts the next scheduled sweep.

Queue contents contain only private object identity and operational timestamps/counters. Raw input, signed URLs, access codes, provider error bodies and credentials are not persisted in the outbox or written to cleanup logs. No cleanup endpoint or queue data was exposed to the frontend.

## Traceability and regression evidence

| Requirement / finding | Implementation | Verification | Status |
| --- | --- | --- | --- |
| Section 5.4: delete the object after DB commit | `data_lifecycle.delete_submission`, same-transaction enqueue, then `main.delete_one` processing | A storage observer opens a different DB connection and sees the committed deletion and job before S3 is called | COMPLETE |
| Section 5.4: retry a failed post-commit delete on next purge | `image_cleanup.py`, `data_lifecycle.sweep`, existing startup/daily callbacks | Failed DELETE returns 204; key persists after submission and owner deletion; later sweep removes real MinIO object/job | COMPLETE |
| Crash between commit and first storage attempt | Durable outbox PK and transaction | Delete helper commits with no immediate attempt; a later worker processes the persisted job | COMPLETE |
| Crash after S3 success but before DB acknowledgement | Per-job transaction and idempotent DELETE | Real subprocess exits with code 23 after deleting from MinIO; another subprocess completes the persisted job | COMPLETE |
| Application restart recovery | Existing lifespan invokes sweep and queue drain | New TestClient lifespan consumes an already-committed job | COMPLETE |
| Preserve transactional failure guarantees | Enqueue uses caller's connection | Injected database delete failure preserves sales, snapshots and object, rolls back job, and makes no storage call | COMPLETE |
| Section 7.4: retention retries without losing confirmed history | Queue insertion with raw-reference clearing/open-submission removal | Expired confirmed history remains; expired open submission is removed; both failed keys persist and succeed on later sweep | COMPLETE |
| Concurrent cleanup workers | Per-job `FOR UPDATE SKIP LOCKED` | Two workers attempt one job; only one storage call succeeds | COMPLETE |
| Bucket identity / referenced-object safety | Bucket-qualified PK/query; live-reference check | Wrong bucket makes no attempt; shared live key is protected; duplicate obligations coalesce and later complete | COMPLETE |
| Outage isolation | Storage errors handled per job; daily task catches storage and DB failures | One failed key does not block another; simulated daily ticks continue through both kinds of outage without private values in logs | COMPLETE |
| Preserve populated database and repeatable upgrade | Additive migration 007 | Populated v6 sales, snapshot rows and previous ledger timestamps compare identically after upgrade/reapplication | COMPLETE |
| Explicit verification of uncertain fields | Existing repository/ConfirmLine/DraftReview behavior retained | Full uncertain-field, duplicate-link, rollback and confirmation suites still pass | Implementation preserved; formal `verifiedFields` contract approval pending |

There are **13 new tests** in `backend/tests/test_image_cleanup.py`. Real PostgreSQL and isolated MinIO exercise persistence and object deletion. External failures are injected through the existing storage interface. No paid AI or production R2 integration is claimed.

## Exact final results

| Check | Result |
| --- | --- |
| Full backend suite | **239 passed**, zero failures/skips, **85.36 seconds**, one existing Starlette/httpx TestClient deprecation warning |
| Full frontend suite | **30 passed**, 3 files, **1.65 seconds** |
| Frontend ESLint | Exit 0 |
| Vite production build | Exit 0, 22 modules, **133 ms** |
| Docker Engine | **29.9.2**, connection verified |
| Normal and isolated Compose configuration | Both `config --quiet` checks passed |
| Isolated runtime | PostgreSQL and backend healthy; frontend and MinIO running |
| HTTP smoke | Passed existing capture/history regressions and canonical manual-entry, confirmation replay, numeric history, validation envelope, pending drafts and submission deletion |
| PostgreSQL migrations | All seven versions **001-007** applied in the isolated database; explicit reapplication exit 0 |
| Isolated integrity inspection | **0** pending image jobs, **0** leftover test schemas, **0** inconsistent confirmed submissions, **0** orphan sales |
| Git | `git diff --check` passed; nothing staged, committed, pushed or merged |

The full backend suite includes existing real-time 30-second x2 extraction deadlines, 15-second guidance deadlines and ten forced backend-process restarts. The old analysis/guidance regression tests check that existing teammate behavior is preserved; they do not certify that behavior against the approved seven-day/template rules. Frontend tests for the frozen capture/shell use mocks and do not certify live integration of their outdated endpoints.

Commands executed from the repository root:

```powershell
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke build backend-tests backend-smoke http-smoke frontend-checks frontend-smoke
# Targeted first pass, before the final worker-outage regression was added:
docker compose -p bodega-gabriel-validation -f compose.test.yml run --rm backend-tests python -m pytest -q -ra tests/test_image_cleanup.py
# Final complete validation:
docker compose -p bodega-gabriel-validation -f compose.test.yml run --rm backend-tests
docker compose -p bodega-gabriel-validation -f compose.test.yml run --rm --no-deps frontend-checks
docker compose config --quiet
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke config --quiet
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke up -d --wait backend-smoke frontend-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke run --rm http-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke exec -T backend-smoke python -m app.database
docker version --format '{{.Server.Version}}'
docker compose -p bodega-gabriel-validation -f compose.test.yml exec -T postgres-test psql -v ON_ERROR_STOP=1 -U bodega_test -d bodega_test -c "SELECT version FROM schema_migrations ORDER BY version; SELECT count(*) AS pending_image_deletions FROM image_deletion_jobs; SELECT count(*) AS leftover_test_schemas FROM information_schema.schemata WHERE schema_name LIKE 'test_%'; SELECT count(*) AS inconsistent_submissions FROM submissions s WHERE (s.status='CONFIRMED') <> EXISTS(SELECT 1 FROM sales_records r WHERE r.submission_id=s.id); SELECT count(*) AS orphan_sales FROM sales_records r LEFT JOIN submissions s ON s.id=r.submission_id WHERE s.id IS NULL;"
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke ps
git diff --check
```

All new writes/deletions during validation target disposable test schemas, uniquely named test buckets or this isolated synthetic smoke dataset. Normal application volumes and unrelated containers were preserved and not migrated/restarted by this review. Isolated services remain running. The outbox captures new deletion transactions and retention operations; it does not reconstruct object keys already lost before migration 007.

## Remaining work by owner

### Gabriel-owned implementation

The requested durable retry defect is closed. No known failure remains in this closure's authorized implementation or executed regressions. The existing incompatible-data upgrade guard remains in effect: if legacy values or required snapshot provenance prevent migration 006, keep the database unchanged and obtain approved remediation. No historical dates or rounded values were invented.

Two broader Gabriel sign-off dependencies remain: formal agreement on `verifiedFields`, and coordination with Luis on the final SQLAlchemy/Alembic architecture. Neither was silently waived or rewritten.

### Explicit team contract approval

Section 4.2's ConfirmLine list omits a verification marker, while Sections 3/4.3 require explicit review of unchanged MEDIUM/UNSCORED values. `verifiedFields` remains enabled end to end. The team must explicitly approve this field as an addendum or supply another official transport contract. Until then, this is an approval-dependent interface, not a reason to remove validation or mark full compliance.

### Luis / Gabriel architecture coordination

The approved architecture names SQLAlchemy models, Alembic migrations, package layout, and Alembic startup/CI commands. The current repository uses psycopg and the existing SQL migration ledger. No persistence-layer rewrite was authorized or performed.

Agree on a forward migration/ORM plan that preserves applied ledger entries and populated rows. The plan must include the operational outbox: declare it in ORM metadata or explicitly protect it from Alembic autogeneration. Never accept an autogenerated DROP that would erase pending image jobs. Baseline the known existing schema, test populated upgrades/reapplication, then coordinate startup and CI changes. Changes to Luis's startup/deployment files require approval.

### Jose - exact integration changes

- `frontend/src/components/SalesCapture.jsx`: replace removed `/api/v1/submissions/manual` with the documented standalone manual form. Generate one UUID v4 per form, collect/validate the rows before submission, POST `/api/v1/manual-entries` with `{submissionId,saleDate,lines}`, keep the ID across retries, and consume ConfirmResponse rather than an extraction draft. Do not restore the removed compatibility endpoint.
- Keep image/text capture on multipart `/api/v1/extract`; integrate the approved `/review/:submissionId` handoff and exact input notice from Section 9.7. Coordinate successful manual-save refresh/navigation with Luis.
- `backend/app/inference.py`, `providers.py`, `privacy.py`, and storage integration: finish Claude Messages/tool adapters and configured models, SDK retries disabled, the one-second wait between extraction attempts, fatal handling for non-retryable 4xx, all-null-line dropping, LOW on missing values, and exact unreadable/no-lines messages.
- Apply the final Section 9.5 masking patterns and image orientation/RGB/JPEG q85/max2048/40-megapixel rules, with the prescribed JPEG content type/key convention. The current generic gateway/older privacy behavior is not approved-Claude compliance.
- Continue using the storage interface's `bucket` identity and idempotent `delete`. Post-commit deletion retry is now Gabriel's durable outbox; do not replace it with a frontend retry or an in-memory list. Live Claude/R2 credentials and provider-term evidence remain external prerequisites.

### Luis - exact integration changes

- `frontend/src/App.jsx` and the shared router/client: implement the approved `/login`, `/capture`, `/review/:submissionId`, `/drafts`, `/history`, `/analysis` workflow; sessionStorage access-code handling, 401 login redirection and the 90-second extraction timeout. Preserve the existing common-error/204 handling and finish field highlighting from `error.fields`.
- Pending drafts use GET `/api/v1/submissions`, reopen via GET `/submissions/{id}`, and discard via DELETE `/submissions/{id}` (204). Replace App's obsolete POST `/discard`. `DeleteData.jsx` still calls the removed bulk DELETE `/data`; replace that flow with documented per-submission deletion. Gabriel's SalesHistory already supplies its in-page confirmation/cancel/retry flow.
- Connect Jose's standalone manual success to history/analysis refresh and route navigation. These shared/capture files remain frozen by the user's instruction; passing mocked tests does not resolve their live integration gap.
- Complete NFR-718 constant-time access-code checking/rate limiting, CORS/configuration validation, approved logging/retention, static nginx/config.js/frontend deployment and CI/TLS requirements. Coordinate SQLAlchemy/Alembic adoption as above; existing migration 007 and pending jobs must survive that transition.

### Diushen - exact integration changes

- `backend/app/analysis.py`: replace the current `SRS_ALL_HISTORY`/two-item-date policy with asOf minus 27 days through asOf, common logged business dates, missing-item zeros, casefold/collapsed item keys, latest display spelling, minimum seven logged days, last-seven averages, preceding-seven trend rules (including prior=0), and Decimal ceil(average x7) stock quantities. Use half-up display rounding to one decimal and snapshot storage to four decimals.
- Supply the approved AnalysisResult fields/statuses. Coordinate `backend/app/schemas.py` analysis/guidance response models and the analysis/guidance sections of `main.py`; the current `requiredDays=2`, `NO_SALES`/old estimate schema is not the final contract. Validate asOf against APP_TIMEZONE and reject future dates with 400.
- Save READY snapshots using `repository.replace_baselines(as_of_date, estimates)`; do not insert undated snapshots or read stale latest snapshots for guidance. The adapter atomically replaces only that owner's requested date. Guidance must recompute, then return 409 with the common error envelope for insufficient history without calling the model.
- `backend/app/guidance.py` and the LLM adapter: send the top20 items/approved calculated fields, enforce all seven Section 9.6 grounding rules, retain the single 15-second/no-retry request, and return TEMPLATE items with failedRule and the exact stock notice on failure. The current withholding/UNAVAILABLE behavior must be replaced in the teammate implementation.
- `frontend/src/components/SalesAnalysis.jsx` (or the team's dashboard): consume the final result, display average/trend/suggested stock and seven-day progress/labels, label fallback as Standard advice, and retain the exact stock-check notice. Integrate changes at the owned sections; do not overwrite Gabriel's canonical routes, validation, repository or outbox work wholesale.

No messages were sent to teammates and none of their assigned components were modified in this closure review.

## Files changed in this closure and readiness

Created: `backend/migrations/007_image_deletion_outbox.sql`, `backend/app/image_cleanup.py`, `backend/tests/test_image_cleanup.py`.

Modified: `backend/app/data_lifecycle.py` (atomic enqueue and sweep integration), `backend/app/main.py` (post-commit processing and resilient cleanup task), `backend/tests/test_database.py` (migration count), `backend/tests/test_workflows.py` (storage-interface fixture and durable-job assertion), and this report. All pre-existing uncommitted work, including `.Rhistory`, remains preserved. Migrations 001-006 and frontend/provider/analysis/deployment files were not rewritten.

Branch remains `gabriel/data-state-implementation`; nothing staged, committed, pushed, merged or submitted as a PR. **Gabriel's durable cleanup fix is ready for review. Overall final submission remains pending the stated formal contract approval, architecture coordination and teammate integrations; no 100% project-completion claim is made.**

---

# Previous review (historical results; superseded where updated above)

# Gabriel Marte - final-specification implementation review

Current review: November 5, 2026. This section supersedes all historical source, test, percentage and readiness claims below.

## Authority and approved scope

Read all 45 pages, including Sections 1-13 and both appendices, of `docs/Milestone_3_Final.pdf`: Version 1.0 implementation baseline, October 23, 2026. SHA-256: `9efabad91d280952c282325a46bc1657c60c033b2eb82c4d1d68ac77f025f646`. The PDF is unchanged. Its accepted Section 3 changes now govern over the earlier SRS/drafts. There are three official milestones; "Milestone 4" denotes the current implementation assignment/branch label. No separate official instruction document for that assignment was located; the user task and final PDF govern this review.

Branch remains `gabriel/data-state-implementation`, HEAD `fdd3bfd`. Preserve all prior uncommitted work. No commit, push, merge, cherry-pick, branch switch, PR or persistent-data deletion was performed. Remote branches were inspected read-only after fetch.

User decisions during this review:

- **Approved:** stop an incompatible populated-database upgrade with a clear error and preserve every original record for approved remediation. Do not round old sales or invent snapshot dates.
- **Not approved:** changes to Jose's capture form or Luis's shared UI integration. Those files were frozen at that decision; their pre-existing modifications remain untouched. Gabriel's review/history components and their integration tests were updated within his ownership.
- **Still unresolved:** the PDF requires explicit verification but Section 4.2 does not name the existing `verifiedFields` wire extension. Approval or an authoritative replacement contract is pending. Backend review protections remain enabled; this is not claimed to be a settled final contract.

## Implemented corrections

- Added forward migrations 004, 005 and 006; committed migrations 001-003 remain unchanged. The final schema uses quantity NUMERIC(10,2), unit price NUMERIC(12,2), averages NUMERIC(12,4), one-based `line_no`, `item_key`, required snapshot `as_of_date`, owner metadata and submission input/timestamp fields. Sales no longer duplicate the parent sale date. Required snapshot uniqueness, row-count, timestamp, status and confirmed-draft constraints are enforced.
- Populated upgrades flush deferred triggers before DDL/backfills, preserve record IDs/quantities/prices/order, and perform Unicode casefolding in Python inside the migration transaction. Migration 006 refuses incompatible values or unknown snapshot dates. All migration ledger changes roll back with the DDL on failure. An older checkout also rejects newer unknown ledger versions.
- Confirmation locks its submission, validates known/unique draft links and uncertain fields, saves all rows atomically and clears the draft. All three open states confirm directly. A confirmed replay returns existing IDs with 200 and ignores invalid/nonexistent lines in its body. Source is derived on the server; the unsupported client `source` field is rejected.
- Added canonical `POST /manual-entries`, `GET /submissions`, `GET /submissions/{id}`, `DELETE /submissions/{id}`, and `GET /sales`. Removed the unsupported compatibility endpoints `/sales/legacy`, `/submissions/manual`, `/manual-review`, `/discard` and `/data`; no compatibility rule overrides the approved contract.
- Canonical history returns the documented record fields as an array, uses `start`/`end`, defaults to today minus 27 days through today, rejects reversed/ranges over 366 days, joins the parent date, and emits exact decimal JSON number tokens including line totals. No undocumented pagination envelope or old date aliases remains.
- Added business-timezone capture/manual date checks, maximum age 365 days, collapsed item labels with a 100-character limit, quantity max 99,999.99, price max 9,999,999.99 and at most two effective decimal places. Validation returns 400 and the common sanitized `error` envelope. New extraction draft IDs are positive integers.
- Added startup and daily stale/retention sweeps, pending-draft listing, owner-scoped deletion and snapshot invalidation, and atomic per-date baseline replacement. Stale processing uses the 120-second cutoff. Thirty-day cleanup retains confirmed history but removes its raw-input references; expired open submissions and old snapshots are purged.
- Gabriel's DraftReview now supports direct rejected-state confirmation, integer draft IDs, documented field limits, server-derived source and numeric JSON values. SalesHistory uses canonical filters/arrays and an in-page delete confirmation, cancellation, failed-delete recovery and refresh after success.

## Requirement-by-requirement traceability (Gabriel and adjacent interfaces)

COMPLETE means the behavior in this row has executed verification. PARTIAL/BLOCKED rows are not counted as final compliance. The matrix is scoped to Gabriel and his interfaces; it is not a fabricated project-wide completion percentage.

| Requirement / final PDF reference | Required behavior | Implementation / tests | Status |
| --- | --- | --- | --- |
| Data dictionary OWNERS, Sections 7.1-7.2 | UUID, display name, UTC creation timestamp; seeded configured owner | 004; `database.migrate`; PostgreSQL schema/ownership tests | COMPLETE behavior; SQLAlchemy architecture remains below |
| SUBMISSIONS, Sections 7.1-7.3 | Input type, six statuses, raw references, masked draft, timestamps; confirmedAt on confirmation | 004-006; `repository.py`; all-state and timestamp tests | COMPLETE for persistence |
| SALES_RECORDS, Sections 7.1-7.2 | Parent FK/cascade; line 1-100 unique; normalized key; numeric types; date from parent | 004-006; repository; raw SQL/FK/count/constraint and maximum-total tests | COMPLETE |
| BASELINE_ESTIMATES, Sections 7.1-7.2 | Required asOf, item identity, unique owner/date/item, exact four-decimal snapshots | 004-006; `data_lifecycle.replace_baselines`; snapshot replacement/rollback tests | COMPLETE persistence; calculation caller integration blocked below |
| Migrations, Sections 7 and 8.5 | Safe initialization and populated upgrade, no historical data loss | `database.py`; upgrade, refusal, reapplication and ledger/data-preservation regressions | COMPLETE SQL upgrade behavior; Alembic architecture remains below |
| FR-104, Sections 4.2, 5.4, SD-04 | Atomic standalone manual entry with client UUID v4; 201 then 200 replay; no AI | `main.final_manual`, `data_lifecycle.manual_entry`; canonical API tests and live HTTP smoke | COMPLETE backend; capture UI BLOCKED |
| FR-202, Section 5.4 | Finite signed/ranged numbers, two decimals, nonblank collapsed label <=100 | `ConfirmLine`, `DraftReview`; backend invalid-input and UI tests | COMPLETE for confirmation/review |
| FR-203 | Edit and remove draft rows before confirm | `DraftReview`; interaction regressions | COMPLETE |
| FR-204 | Empty/invalid confirmation rejected atomically with field errors | common error handler, `ConfirmRequest`, repository transaction; rollback/UI tests | COMPLETE |
| FR-207 | HIGH still requires owner confirmation | repository lifecycle + DraftReview; confidence boundary/review tests | COMPLETE at data/review boundary |
| FR-208 | MEDIUM/UNSCORED require edit or explicit verification | repository and DraftReview; equivalent-decimal and uncertain-field regressions | PARTIAL: verification wire extension requires decision |
| FR-209 | LOW fields blank and required before confirm | normalization and DraftReview; confidence/required-field tests | COMPLETE for LOW-band input; provider/null-value banding is Jose integration |
| FR-211 / state diagram, Section 5.6 | Recover stale UPLOADED/CLASSIFYING after 120 seconds | `mark_stale`, pending list, startup sweep; fixed-clock stale tests | COMPLETE for Gabriel's recovery boundary |
| FR-215, Section 7.4 | Retain input 30 days; clear draft on confirmation; remove expired references | lifecycle sweep, repository, storage retention setting; retention and real MinIO tests | PARTIAL: post-delete orphan retry remains unresolved |
| FR-216 | List/reopen pending drafts, open states only/newest first | pending/detail endpoints; canonical API/HTTP tests | COMPLETE backend; routed pending page BLOCKED |
| FR-301 | Only confirmed rows become persistent history; derived source | repository, manual endpoint, database triggers; persistence/source tests | COMPLETE |
| FR-302 | Locked transaction, rollback, no duplicate/repeated/concurrent confirmation | repository; PostgreSQL concurrency, injected failures, ten forced-process restarts | COMPLETE |
| FR-303 / Section 4.2 | Canonical record array, numeric values and lineTotal; start/end default28/max366; sort date desc/line | `final_sales`, `public_record`, exact JSON renderer, SalesHistory; boundary and HTTP tests | COMPLETE |
| FR-304 / Section 7.4 | Delete one submission, cascade rows, invalidate owner snapshots, delete image after commit | `delete_submission`, `delete_one`, SalesHistory; owner-scope/cascade/MinIO/cancel/failure tests | PARTIAL: durable retry after storage failure not complete |
| Snapshot method contract, Section 5.4 | Replace only requested owner's requested date atomically | `repository.replace_baselines`, `data_lifecycle.py`; two-date uniqueness and rollback tests | COMPLETE adapter; Diushen must call it |
| State diagram, Section 5.6 | Exactly six states; direct open->CONFIRMED; terminal repeat; no invented manual-review transition | 006, lifecycle map, repository, UI; all 36 database state pairs | COMPLETE |
| NFR-725 | No partial/lost/duplicate confirmed records across ten forced restarts | `test_ten_backend_process_restarts_during_confirmation` | COMPLETE executed test-server scenario |
| REST errors, Section 4.2 | `{error:{code,message,fields}}`; validation 400; no raw input | canonical handlers; schema/HTTP/validation/privacy regressions | COMPLETE for tested data-layer failures; shared security/logging requirements remain below |
| Diagram/source consistency, Section 8.6 | Matching Mermaid source and class/module architecture | Final PDF read completely; repository still lacks matching .mmd sources and required ORM/Alembic package structure | INCOMPLETE; architecture integration needed |

## Fresh execution evidence

Final full backend: **226 passed**, no failures/skips, **83.30 seconds**, one existing Starlette/httpx TestClient deprecation warning. The final two added tests cover exact maximum-value JSON totals and the absence of undocumented capture/history endpoints. Real 30-second x2 extraction and 15-second guidance timeout trials and ten spawned test-server crashes remain in the full suite. Providers are stubbed; PostgreSQL and MinIO are real isolated services. These tests do not establish live Claude/R2 integration or final analysis/guidance compliance.

Final frontend: **30 passed**, 3 files, **1.75 seconds**; ESLint exit 0; Vite production build exit 0, 22 modules, **145 ms**. The three new history tests cover cancellation, confirmed delete/refresh and failed-delete retry. Existing capture/shared-shell component tests use mocks; their passing results do not prove compatibility with the removed legacy endpoints.

Docker Engine 29.9.2 connectivity passed. Both normal and isolated Compose configurations passed. Isolated PostgreSQL and backend are healthy; frontend and MinIO are running. Live HTTP smoke passed frontend/proxy, canonical manual entry, ignored replay body, persisted confirmed data, exact decimal history, authentication, common error envelope, pending listing and synthetic submission deletion. The smoke script also keeps existing teammate-analysis regression checks; those old SRS checks are explicitly not final-policy compliance evidence.

The isolated populated HTTP database upgraded successfully through **001-006** and explicit migration reapplication exited 0. Inspection found **zero leftover test schemas**, **zero inconsistent confirmed/record relationships**, one-based `line_no`, no sales `sale_date` column, quantity precision/scale **10/2**, price **12/2**. Incompatible legacy fixtures fail and preserve their original sales, snapshots and migration ledger. No application volumes or unrelated containers were removed or reset. Isolated test services remain running.

Exact commands from the repository root:

```powershell
docker compose config --quiet
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke config --quiet
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke build backend-tests backend-smoke http-smoke frontend-checks frontend-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml run --rm backend-tests
docker compose -p bodega-gabriel-validation -f compose.test.yml run --rm --no-deps frontend-checks
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke up -d --wait backend-smoke frontend-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke run --rm http-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke exec -T backend-smoke python -m app.database
docker compose -p bodega-gabriel-validation -f compose.test.yml exec -T postgres-test psql -v ON_ERROR_STOP=1 -U bodega_test -d bodega_test -c "SELECT version FROM schema_migrations ORDER BY version; SELECT count(*) AS leftover_test_schemas FROM information_schema.schemata WHERE schema_name LIKE 'test_%'; SELECT count(*) AS inconsistent_submissions FROM submissions s WHERE (s.status='CONFIRMED') <> EXISTS(SELECT 1 FROM sales_records r WHERE r.submission_id=s.id); SELECT column_name,data_type,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema='public' AND table_name='sales_records' ORDER BY ordinal_position;"
git diff --check
```

The temporary PDF parser ran in a disposable container, read all 45 pages, and did not change dependencies in the repository or modify the PDF. Initial intermediate failures (deferred-trigger DDL, duplicate local constraint, outdated old-contract fixtures, one test Unicode encoding issue and a standalone smoke import) were corrected; do not treat earlier failed or older passing runs as final results.

## Remaining discrepancies and required decisions

1. **Verification contract (team decision):** `verifiedFields` is still required by the server for unchanged uncertain values. The final request table omits the field while the review requirement needs explicit verification. The user was asked to approve an addendum or supply the official transport mechanism; no answer has been received. Do not silently remove backend review protection or certify this extension as approved.
2. **Capture/shared UI integration (explicitly not authorized):** Jose's `SalesCapture.jsx` still uses the removed `/submissions/manual`; Luis's App/shared deletion integration still uses removed `/discard` or `/data` paths. The final manual page, pending route and shared routing/auth behavior need their owners' changes. Those workflows can now receive errors; the strict backend does not preserve contradictory endpoints to conceal this. Gabriel's own history/review components use the canonical contract. No capture/shared-shell code was changed after the user's refusal.
3. **Analysis/guidance (Diushen):** local code still calculates the old all-history/two-date baseline and withholds guidance on failure. Sections 3, 5.5, 9.6 now require 7 logged days in a 28-day window, zero filling, trend/stock calculations, snapshot replacement, top20 evidence, seven grounding rules and TEMPLATE fallback. The adapter is ready, but the teammate implementation/endpoint/response schema must be integrated. Do not count old analysis tests as proof of those requirements.
4. **Provider/privacy (Jose):** final Claude SDK/tool contracts, one-second retry delay/fatal-4xx policy, exact masking pattern, null-value banding, no-lines messages, resized/oriented JPEG sanitization and exact capture notice are not fully integrated. Existing generic gateway and old privacy functions remain earlier-session work. Live Claude credentials, R2 configuration and provider-term review are external prerequisites.
5. **Durable post-delete image retry (Gabriel/storage integration):** deletion commits the database first, as required. A failed subsequent object delete is logged, but the deleted row no longer supplies its key to the next database purge. A durable retry mechanism is still needed. Approve an operational outbox/queue or coordinate an equivalent safe bucket reconciliation mechanism before accepting FR-304 as fully complete. Retention failures on still-existing rows remain retryable because their keys stay in the transactionally preserved rows.
6. **ORM/migration/deployment integration (Gabriel with Luis):** the final architecture explicitly names SQLAlchemy/Alembic and an Alembic startup/CI path; the preserved project uses psycopg plus an ordered SQL ledger. Its storage behavior and upgrades are tested, but that architectural discrepancy is not waived. Coordinate the migration-tool/ORM and startup integration; no speculative rewrite of committed migrations or teammate Docker/CI work was performed in this phase.
7. **Historical remediation (approved stop policy):** any real database failing 006 must remain untouched until authoritative snapshot dates and a non-lossy plan for incompatible historical values are supplied. Do not delete snapshots, round old sales, fabricate asOf dates or bypass the guard. Successful upgrade of compatible data does not imply every populated database can be upgraded without this review.
8. **Shared infrastructure/security and final acceptance:** rate limiting/constant-time auth, final logging schema/retention, supported browser/routed page checks, deployment/TLS, benchmark extraction quality and performance/usability acceptance still need the responsible owners and their environments. Do not alter those team features without approval or claim those NFRs passed.

There is no longer a source-document approval conflict over the seven-day formula or 30-day retention: the October 23 PDF explicitly approves both. Remaining questions are implementation/integration and the verification-wire omission, not permission to keep the earlier SRS policy.

## Files and Git scope

Current Gabriel work is in `backend/app/database.py`, `repository.py`, `main.py` (data/capture-history contracts), `schemas.py` (snapshot input), `lifecycle.py`, `clock.py`, `data_lifecycle.py`, `seed.py`; migrations `004_baseline_v1.sql`, `005_final_integrity.sql`, `006_strict_final_schema.sql`; backend data/final-contract/review/workflow/smoke tests; Gabriel's `DraftReview.jsx`, `SalesHistory.jsx`, their review/history-deletion tests and only the history case of `Workflow.test.jsx`; and this documentation. Earlier uncommitted files remain prior-session work; the final Git status distinguishes tracked modifications and new files. `.Rhistory` is untouched. The shared capture, App, analysis, provider and infrastructure files already had earlier modifications; they were preserved and not rewritten following the user's restriction.

`git diff --check` passes. Nothing staged. No commit, push, merge, force-push or pull request. **Readiness: tested Gabriel storage/API changes are ready for review, but Gabriel's complete final submission is NOT certified ready while the listed contract and integration gaps remain. No 100% claim or invented completion percentage is made.**

---

# Historical implementation notes (superseded by the current review above)

## Current Gabriel review ? November 5, 2026

This section supersedes historical verification and readiness claims below. There are three official milestones; ?Milestone 4? is used only as the user's implementation-work label / historical API-contract branch name.

### Scope and checkout findings

- Current branch: `gabriel/data-state-implementation`, HEAD `fdd3bfd`. All prior uncommitted implementation work was preserved. No switch, merge, cherry-pick, staging, commit, push, or PR action occurred. Remote references were refreshed with `git fetch origin`; this revealed Diushen's analysis/guidance feature branch, inspected read-only.
- `004_baseline_v1.sql` is absent from the workspace and refreshed available branches. Only migrations 001?003 can be reviewed or applied. No committed migration was changed, no speculative 004 was created, and no historical records were deleted.
- The final approved October 23 Milestone 3 document was not found. Available local sources are the older Formal Analysis and Design compilation and October 19 update. Therefore final approved-contract compliance cannot be certified.
- `uncertain_line()` already exists in `backend/tests/test_database.py`. Unknown/duplicate `draftLineId` rejection, linked-row provenance, uncertain-field review, partial-insert rollback, idempotency and concurrency coverage are already present and pass; these were preserved rather than duplicated or rewritten.

### Corrections completed in this review

1. `backend/app/database.py`: after acquiring the existing advisory migration lock, startup now rejects migration-ledger versions absent from this checkout before applying any SQL scripts. An older application therefore cannot silently operate against a newer baseline schema. The runner preserves the ledger and data and requests the matching application version. Existing migration SQL remains unchanged.
2. `backend/tests/test_review_regressions.py`: five new real-PostgreSQL cases. One populates confirmed sales and baseline snapshots, simulates a ledger entry named `004_baseline_v1.sql` without inventing its DDL, and proves startup refusal leaves IDs, snapshots and migration timestamps unchanged. Four cases prove equivalent decimal formatting (`1.20` versus `1.2000`) cannot bypass MEDIUM, UNSCORED, unknown or missing confidence verification. Explicit verification then permits confirmation.
3. This report records fresh evidence and the remaining source/contract blockers. No frontend, provider, analysis, storage or other teammate-owned implementation was changed during this review. The many pre-existing uncommitted files listed by Git remain prior-session work, including `.Rhistory`, which was not edited.

### Fresh verification

| Check | Actual result |
| --- | --- |
| Docker connectivity | Engine 29.9.2 available after `docker desktop start`; elevated access is required by this environment. |
| Backend | **213 passed**, zero failures/skips, **78.84 seconds**, one existing Starlette/httpx deprecation warning. Includes full timeout and forced test-process-restart regressions. |
| Frontend | **27 passed**, 2 files; **2.19 seconds** suite duration. |
| ESLint | Exit 0. |
| Production build | Exit 0, 22 modules, 184 ms. |
| Compose configurations | Normal and isolated configurations both pass `config --quiet`. |
| Isolated services | PostgreSQL and backend smoke service healthy; frontend and MinIO running. Frontend/API networking verified by live HTTP smoke; storage tested against real isolated MinIO. |
| HTTP smoke | Passed frontend/proxy, draft persistence, confirmation/idempotency, exact decimals, authentication, validation contracts, manual capture, current SRS analysis, date filtering and disabled-provider guidance. |
| Migrations | 001, 002 and 003 applied; explicit reapplication exits 0. Existing populated 001/002 ? 003 upgrade regression passes. Actual 004 upgrade **blocked: file absent**. |
| Integrity | Zero leftover test schemas, inconsistent submission/record states, or mismatched parent/record sales dates. |
| Diff | `git diff --check` exits 0; only an existing Git LF/CRLF advisory for the prior test file. Existing files were not reformatted. |

Commands executed from the repository root:

```powershell
git fetch origin
docker desktop start
docker version --format '{{.Server.Version}}'
docker compose config --quiet
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke config --quiet
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke build backend-tests backend-smoke http-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml run --rm backend-tests
docker compose -p bodega-gabriel-validation -f compose.test.yml run --rm --no-deps frontend-checks
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke up -d --wait backend-smoke frontend-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke run --rm http-smoke
docker compose -p bodega-gabriel-validation -f compose.test.yml --profile smoke exec -T backend-smoke python -m app.database
docker compose -p bodega-gabriel-validation -f compose.test.yml exec -T postgres-test psql -v ON_ERROR_STOP=1 -U bodega_test -d bodega_test -c "SELECT version FROM schema_migrations ORDER BY version; SELECT count(*) AS leftover_test_schemas FROM information_schema.schemata WHERE schema_name LIKE 'test_%'; SELECT count(*) AS inconsistent_submissions FROM submissions s WHERE (s.status='CONFIRMED') <> EXISTS(SELECT 1 FROM sales_records r WHERE r.submission_id=s.id); SELECT count(*) AS mismatched_dates FROM sales_records r JOIN submissions s ON s.id=r.submission_id WHERE r.sale_date<>s.sale_date;"
git diff --check
```

Initial Docker attempts failed because Desktop was stopped; CLI startup resolved it. Native `npm` attempts were blocked by PowerShell execution policy; the existing Docker frontend runner completed all three checks without changing that policy. Application volumes and unrelated containers were not removed or reset. Tests write only disposable PostgreSQL schemas and uniquely named test storage buckets. Isolated services remain running.

### API and lifecycle assessment / submission blockers

- Available implementation: six states, transactional confirmation (201 first save, 200 idempotent repeat), controlled 409 lifecycle conflicts, private owner-scoped history, atomic baseline writes/latest-per-item reads, guarded deletion/discard, and existing raw-object retention cleanup all retain their passing coverage.
- Current history contract is `startDate` / `endDate`, optional inclusive bounds, with unbounded defaults plus pagination. It does **not** implement the requested final-contract `start` / `end` names or a default 28-day range. Existing validation errors use sanitized `detail` arrays; business/auth errors retain their prior forms. A universal final error envelope cannot be verified from available documents.
- Baseline snapshot operations are implemented for the existing schema, but the migration to the requested baseline-v1 schema cannot be reviewed without the actual 004 file. Do not run this older checkout against a database where 004 is already applied; the new guard rejects it safely.
- Retention/deletion regression tests pass for the available implementation. The exact final approved lifecycle/retention requirements and operational cleanup during prolonged service outages still require the October 23 source and deployment evidence. Passing tests do not establish that missing document's requirements.
- Required next input: the final October 23 specification and the checkout/path containing `004_baseline_v1.sql`. Then reconcile the API names, default range, error envelope and baseline schema within Gabriel's ownership, preserving existing clients and historical data. Any departure from that final specification requires team approval; this review invents none.
- **Readiness:** the available implementation passes all executed checks and is suitable for code review. Gabriel's final submission is **not yet certified ready**, because 004 upgrade safety and final API-contract compliance remain unverified. No commit, push, merge or PR was performed.

---

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
