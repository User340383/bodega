# Integration decisions — branch `integration/m3-completion` (and `work/srs-completion`)

**Governing rule (since 2026-11-10).** Two documents are the references for this branch:

1. `WorkDivision.docx` defines the **Milestone 4 deliverables** of each member.
2. The final Milestone 3 (`Milestone_3_Formal_Analysis_and_Design-1.pdf`, Version 1.0, October 23,
   2026, "implementation baseline") defines their **design and integration**: endpoints, data
   model, lifecycle, analysis rules, error envelope, retention and deployment.

Milestones 1 and 2 are not references for this work. Where WorkDivision and Milestone 3 differ, a
documented team approval decides; where none exists the item is listed under "Decisions that need
the team" and the code follows Milestone 3.

On 2026-11-09 this branch had briefly been re-based on the Milestone 2 SRS (decisions D-26 to
D-32, uncommitted). Each of those was re-checked against WorkDivision and Milestone 3 on
2026-11-10; the result is the last decision table below. The uncommitted state before that review
is kept on the local branch `backup/srs-completion-wip-2026-11-10`.

Nothing here has been merged to `main`. Branches kept unchanged for comparison:

| Ref | Contents |
|---|---|
| `backup/origin-main-b684c07` | `main` at the start of this work |
| `backup/gabriel-data-state-cb55fce` | Gabriel's unmerged branch |
| `backup/gabriel-stash-2026-11-08` | Gabriel's saved stash (`stash@{0}` is also still in place) |
| `backup/srs-completion-wip-2026-11-10` | The SRS-governed work in progress (D-26 to D-32 as first written), local only |

## Decisions

| ID | Decision | Why | Affects |
|---|---|---|---|
| D-01 | **Port Gabriel's `cb55fce`; don't merge it.** Its migrations, endpoints, retention and tests were reimplemented on top of current `main`. | `cb55fce` branches from before José's and Diushen's merges. Its `main.py` would replace José's real `/extract` with a placeholder and Diushen's analysis and guidance with stubs. It would also remove Luis's rate limit, constant-time compare, CORS and startup checks. It conflicts in 8 files. | All |
| D-02 | **Duplicate migration 004: keep José's `004_capture_inputs.sql`.** Gabriel's 004–007 become one additive `005_final_data_design.sql`. No applied migration is edited or renamed. | Running both 004s fails with "column already exists", and the backend would not start. 005 only tightens José's nullable `input_type` (backfill, then NOT NULL). | Gabriel, José |
| D-03 | **Final limits restored:** quantity > 0, at most 2 decimals, ≤ 99,999.99; price ≥ 0, ≤ 9,999,999.99; names 1–100 characters. Checked in the API, the UI, and the database (`NOT VALID` constraints). | M3 section 3 and 7.2 define them. Gabriel's migration 003 had removed them, based on the October 19 draft. José's ingestion already clamped to them, so the old code was inconsistent. | Gabriel, José, Diushen (tests now use the boundary values) |
| D-04 | **Lifecycle follows M3 Diagram 4.** All open states confirm directly; UPLOADED/CLASSIFYING go stale to AI_UNAVAILABLE; manual entry is inserted CONFIRMED. The `manual-review` endpoint and button are removed. | The final state diagram has no REJECTED/AI_UNAVAILABLE → NEEDS_VERIFICATION transition, and Gabriel's branch already matched it. | Gabriel, José (UI) |
| D-05 | **(Confirmed by D-30: 400 everywhere.)** **Error body is `{error: {code, message, fields}}` on every non-2xx; validation is 400 everywhere.** `detail` is kept beside it as a compatibility alias. | M3 4.2. The shared frontend client already read both shapes, so nothing broke. The alias keeps older clients and tests working, and never contains submitted values. | All |
| D-06 | **Integer draft line IDs** (M3 7.3). Older string IDs still match on read and confirm. | José emitted strings and Gabriel's branch required integers. Accepting both avoids breaking drafts saved before this change. | José, Gabriel |
| D-07 | **Resolved: amounts are JSON numbers** (M3 4.2), written as exact Decimal tokens that never pass through float. Confidence bands and display labels stay text. | M3 4.2 "money and quantities are JSON numbers parsed as Decimal". The final limits bound every value to about 12 significant digits, so browsers show them exactly; tests parse with a Decimal hook. | Diushen, Luis, Gabriel |
| D-08 | **`GET /sales` returns `{start, end, records}`** with M3's record fields (`recordId`, `lineNo`, `lineTotal`, …), not a bare array. | Keeps the response extensible, and the client keeps reading `records`. The range rules are M3's (28-day default, ≤ 366 days). | Gabriel, Luis |
| D-09 | **Analysis status keeps `NO_DATA` and `ESTIMATE_ONLY`** in addition to M3's `READY`/`INSUFFICIENT_HISTORY`. | A superset: FR-602 needs a distinct no-data state, and 7–13 dates give estimates without a trend (FR-405). | Diushen |
| D-10 | **Snapshots are written, never read back.** Every analysis or guidance run replaces that date's `BASELINE_ESTIMATES` rows (SD-03 saveBaselines). Responses are always recomputed. | M3 section 10: "Guidance recomputes baselines"; a stale snapshot can never be shown. | Diushen, Gabriel |
| D-11 | **Superseded by migration 006.** SALES_RECORDS now follows the M3 7.1 ERD exactly: no per-row `sale_date` (read from the parent), `line_no` 1–100, and a generated, indexed `item_key`. | The final M3 text is explicit ("saleDate is not repeated on SALES_RECORDS"). Nothing is lost: the old composite FK had guaranteed the dates were equal. The upgrade test proves IDs, values, order and dates survive. | Gabriel, Diushen (queries join the parent) |
| D-12 | **Rate limit keys on the right-most trusted `X-Forwarded-For` hop** (`TRUSTED_PROXY_HOPS`, 1 on Render), and uvicorn no longer trusts proxy headers. | `--forwarded-allow-ips='*'` let any client bypass NFR-718 by rotating the left-most entry. Assumption to verify on first deploy: Render appends exactly one hop. | Luis |
| D-13 | **Retention service** runs at startup and every 6 hours. Images are deleted through an outbox, after commit. | M3 7.4; a storage outage can never block or roll back a database delete. Retention is 30 days (M3 7.4; `docs/privacy.md`). | Gabriel, José |
| D-14 | **Text containing markup or control characters is rejected (400).** | NFR-714 requires script-injection text to be rejected. Sales logs never need tags; a lone `<` or `&` is still accepted. | José |
| D-15 | **Claude wording provider uses a strict tool with `tool_choice: auto`**, `max_retries=0` and a 15 s timeout. Any refusal, truncation or missing call gives Standard advice. | Current models reject forced tool choice; M3 SD-03 allows one attempt and no retry. | Diushen |
| D-16 | **Gabriel's stash is not applied.** | It contains parallel copies of José's ingestion and Diushen's analysis; its analysis is the replaced M2 formula. The useful UI parts (date filter, typed DELETE, discard) were rebuilt against the final contract. It is kept in `backup/gabriel-stash-2026-11-08`. | Gabriel |
| D-17 | **Resolved: Tailwind CSS 4.3.5** (@tailwindcss/vite). Design tokens are in `@theme`; components use Tailwind utilities via `@apply` with the `md` breakpoint. | M1 stack and M3 4.1/4.3. Re-verified: axe and layout 20/20, cross-engine 5/5, screenshots reviewed. | Luis |
| D-18 | **Resolved: Alembic migrations and SQLAlchemy models.** Revisions 001–008 (D-36) apply the reviewed SQL files; startup and CI run `alembic upgrade head`; `app/models.py` is checked against the migrated schema; connections go through a SQLAlchemy engine. Pre-Alembic databases are stamped; foreign histories are refused. | M3 5 and 8.5. RecordRepository keeps hand-written SQL through the engine's connections (M3: "the only class that runs SQL"). | Gabriel, Luis |

| D-19 | **`render.yaml` ships with `AI_PROVIDER` empty.** Extraction falls back to manual entry and guidance to Standard advice until the team enables `claude`. | NFR-716: no sales data may reach a provider whose terms have not been checked, and nobody has recorded them yet (`privacy.md` §3). | Luis, José, Diushen |
| D-20 | **(See also D-28.)** **Phone masking boundaries are refined.** The M3 9.5 alternatives are unchanged, but `.`/`,` block a match only next to a digit. | The M3 boundaries let a number before a comma or full stop through unmasked, including the SRS AC-19 example itself. Decimals and thousands are still protected (tested). | José |
| D-21 | **(Replaced by D-33.)** **SRS literal texts are kept where M3 did not replace them:** "Image could not be read" (FR-210), "AI reading is temporarily unavailable" (FR-211), "Could not read — enter value" (FR-209, em dash). Each is followed by an action. | The SRS fixes these words; NFR-731 adds the action, word and grade limits. | José, Gabriel |
| D-22 | **Delete-all is added** (`POST /api/v1/data/delete-all`, typed DELETE, "N records deleted") next to M3's per-submission delete. | SRS FR-304, AC-21, UC6 and NFR-722 all require deleting all stored data; M3 added per-submission delete but did not remove this. | Gabriel |
| D-23 | **Readability is enforced, and the NFR-734 / M3 template conflict is resolved:** advice covers the items with the highest suggested stock that fit 150 words of the fixed template; only those are sent to the AI; `omittedItems` and a dashboard note report the rest, whose figures stay visible. | NFR-731 and NFR-734 with the M3 9.6 template and rule 1 all satisfied at once. | Diushen |
| D-24 | **Mermaid sources committed:** `docs/architecture/*.mmd` (M3 Appendix A) and `docs/requirements/SRS.md` (M2 §6), with a CI render check. | M1, M2 §6 and M3 Appendix A each say the sources live in the repository; they did not. | All |

| D-25 | **Known edge case: device timezone different from `APP_TIMEZONE`.** The capture and manual-entry forms default to the device's date (SRS 3.1). The server rejects dates after the business date. A device several hours ahead of the business (for example a UTC device after 20:00 in Santo Domingo) is therefore told the date is in the future, and can pick the business date instead. | M3 makes the business date authoritative; a single-business deployment shares one timezone. Found by the cross-engine run, whose container ran in UTC. | Luis, José |

## Milestone 4 review (2026-11-10): the 2026-11-09 decisions re-checked

Each decision made under the Milestone 2 rule was compared with WorkDivision and Milestone 3.
"Reviewer" is the member responsible for the affected area.

| ID | Outcome | What the code does now | Why | Reviewer |
|---|---|---|---|---|
| D-26 | **Withdrawn.** | Analysis, guidance, grounding, snapshots and the dashboard follow M3 5.5, 9.6 and 9.7 again: average over the latest 7 logged days in a 28-day window, trend UP/DOWN/STABLE from 14 logged days, suggested stock = ceil(average × 7), labeled "Standard advice" on any AI failure. | WorkDivision assigns Diushen "7-day moving averages and trend classifications" and a dashboard showing "trend directions (UP/DOWN/STABLE)". The Milestone 2 formula had removed both. | Diushen; Gabriel (snapshots) |
| D-27 | **Withdrawn.** | Retention is M3 7.4: `draftPayload` is cleared on confirmation; the sanitized photo and masked text are kept 30 days after upload; open submissions and snapshots are purged after 30 days. The sweep runs at startup and every 6 hours (D-13). Kept from that work because it is independent of the deadline: the sweep re-queues orphaned photos older than 1 hour, and `imagesPending` is logged. | M3 7.4, and M3 4.2 returns `imageUrl`/`maskedText` in SubmissionDetail after confirmation. | Gabriel, José |
| D-28 | **Narrowed.** | Unbroken digit runs are masked from 10 digits, as in M3 9.5 (the 7-digit rule is removed). Kept as an optional addition: all-space `809 555 1234`, `(809) 555 1234` and a leading `1-`. | A unit price may be up to 9,999,999.99 (M3 5.4), so masking 7 to 9 digit runs erased valid prices such as `Beef 1 1500000`; M3 9.5 requires prices to stay intact. The all-space form needs two single spaces around exactly 3-3-4 digits and is tested not to touch sales lines. | José |
| D-29 | **Withdrawn.** | `POST /submissions/{id}/retry`, the `AI_UNAVAILABLE → CLASSIFYING` transition, `retry_count`, `extraction_failed_at` and the "Try reading again" buttons are removed. The draft migration 007 (never committed or deployed) is replaced by D-36. | M3 Diagram 4 has no such transition and M3 4.2 no such endpoint. WorkDivision's "30-second timeout/retry logic" is the single in-request retry (M3 5.4), which is unchanged. | José, Gabriel |
| D-30 | **Resolved by team review (2026-11-11): 400.** | Every invalid request answers 400 with code `VALIDATION_ERROR`, the M3 4.2 envelope and field paths (`lines.0.quantity`), which the SPA shows on the row. This now includes `POST /confirm` and `POST /manual-entries`, for FastAPI/Pydantic request validation (`main.request_validation_error`) and the application's own checks (`repository.ConfirmationError`, the manual-entry sale-date check). Any 422 raised elsewhere is answered as 400; OpenAPI documents 400 and never 422. 401, 404 `NOT_FOUND`, 409 `INVALID_STATE`, the idempotent 200 and 500 are unchanged. | The reviewer pointed out that M3 4.2 lists 400 for both endpoints (`/confirm`: 400; 404; 409; 500. `/manual-entries`: 400; 409; 500). The earlier 422 came from a brief that predated the final M3. Tests: `test_integration_regressions.py` (13 invalid `/confirm` and 8 invalid `/manual-entries` requests, each 400 with no change to records, status, draft or timestamps; the other statuses keep their meanings), `test_database.py` (every validation error 400; OpenAPI has no 422), the HTTP smoke test and `prodcheck.py`. | All (Gabriel: confirm; José: manual entry; Luis: envelope) |
| D-31 | **Kept.** | Extraction no longer rounds or clamps numbers; a value outside the M3 7.2 limits is shown as read, is never banded HIGH and is rejected by confirmation until corrected. | Reproduced defect (1.234 shown as 1.23 before review). M3 5.4 places the limits in `validateLines`, not in extraction. | José |
| D-32 | **Kept (optional addition).** | `GET /sales` takes `limit` (1–500, default 200) and `offset` and returns `total` and `hasMore`. | M3 4.2 returns "200 records"; without paging, older records in a range could not be reached. | Gabriel, Luis |
| D-33 | **New.** | Owner-facing fallback texts are M3 9.4's: "We could not read this photo. Use good light, lay the page flat, and fit the whole page in the frame." and "We could not read this right now. You can type the sales below." | D-21 had kept two Milestone 2 literals; M3 9.4 defines what the owner sees. | José |
| D-34 | **New.** | `backend/seeds/synthetic_sales.sql`: a repeatable SQL seed (Bread and Milk on the last 14 days) run with `psql`; the backend image now copies `seeds/`. | WorkDivision "Immediate Execution Steps" 2 asks for a small SQL seed script so analysis and guidance can be tested from the command line; only Python seeds existed. | Gabriel (data), Luis (Dockerfile) |
| D-36 | **New (2026-11-11).** | Upgrade path for databases that ran the withdrawn draft 007: revision `007_reserved` (no schema change) keeps the number Alembic recognizes, and `008_remove_unreleased_007` removes the draft's columns, constraint, index and transition and its `SRS_RECORDED_DATES` snapshots, and restores `NOT NULL` on the M3 snapshot columns. Every statement is idempotent; on a database that never had the draft both revisions change nothing. Head is `008`. | The local development database was stamped `007` and could not start against code ending at 006. Tested: `test_models.py` (draft database upgraded with every submission, draft and sales row unchanged; schema equal to a fresh one; 008 replayed by hand is a no-op), a restored copy of the real backup, and the real local database (2 submissions and 1 sales record, row checksums equal before and after, two restarts). | Gabriel, Luis |
| D-37 | **New (2026-11-11).** | The "Skip to content" link is at least 44 × 44 px when focused (it was 107 × 24). | M3 4.3 touch targets; found by `scripts/browser_a11y.mjs`. | Luis |
| D-35 | **Kept.** | `ClaudeLLMService` requires exactly one `write_guidance` call whose input passes a strict schema, uses `max_tokens` 1024 (M3 9.2) and a 15-second per-call timeout; anything else gives Standard advice. | M3 9.6 rule 1 and SD-03. | Diushen |

**Optional additions kept for team review** (outside WorkDivision's wording, not in conflict with
Milestone 3): delete-all with typed DELETE (D-22); `/sales` paging (D-32); "Discard this draft" on
the review page (FR-216 allows discarding); the extra phone formats in D-28; analysis statuses
`NO_DATA` and `ESTIMATE_ONLY` (D-09); the `verifiedFields` and `source` fields on confirm lines;
the HTTP Smoke Test CI job with MinIO; `scripts/live_ai_check.py` and
`scripts/live_storage_check.py`.

**Shared files changed on 2026-11-10 and who should review them**

| Area | Files | Reviewer |
|---|---|---|
| Analysis, guidance, dashboard restored to M3 | `backend/app/analysis.py`, `guidance.py`, `grounding.py`, `llm.py`, `seed_analysis.py`; `frontend/src/components/ResultsDashboard.jsx`, `SalesTrendChart.jsx`; their tests | Diushen |
| Retention, lifecycle, snapshots, seed, migrations | `backend/app/repository.py`, `retention.py`, `models.py`, `backend/seeds/synthetic_sales.sql`, `backend/migrations/007_reserved.sql`, `008_remove_unreleased_007.sql` and their Alembic revisions | Gabriel |
| Masking, extraction messages, capture fallback text | `backend/app/privacy.py`, `ingestion.py`; `frontend/src/components/captureRules.js` | José |
| Routes, error envelope, CI, images, pages, checks | `backend/app/main.py`, `backend/Dockerfile`, `.github/workflows/ci.yml`, `compose.test.yml`, `frontend/src/pages/DraftsPage.jsx`, `ReviewPage.jsx`, `frontend/src/api/client.js`, `frontend/src/layout/AppLayout.css`, `scripts/prodcheck.py`, `scripts/browser_a11y.mjs` | Luis |

## Decisions that need the team

- Approve the decisions above in review (each member for the areas listed).
- **WorkDivision says "regex contact masking" and "7-day moving averages"; M3 fixes the exact
  rules.** No conflict was found between the two documents; if the team reads WorkDivision's
  "PostgreSQL schema (Owners, Submissions, Sales, Baselines)" differently from M3 7.1, say so.
- Whether to keep each optional addition listed above.
- `/capture`, `/history` and `/analysis` have no `h1` (their headings start at `h2`). axe reports this only as a best practice, not a WCAG 2.1 AA failure, so the check passes; adding one is a small UI change for the page owners (José, Gabriel, Diushen) to accept or decline.
- Record Anthropic's current API data-use and retention terms in `docs/privacy.md` before
  `AI_PROVIDER=claude` is enabled on Render (D-19, M3 12.2).
- Add "Lint and Secret Scan" and "HTTP Smoke Test" to the required status checks of the `main`
  ruleset (a repository setting; Luis). Today it requires Backend Tests, Frontend Tests, Lint and
  Build, and Production Images, plus one approving review.
