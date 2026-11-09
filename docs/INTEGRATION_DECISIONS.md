# Integration decisions — branch `integration/m3-completion`

This branch merges the four members' Milestone 4 work into one system that follows the final
Milestone 3 document (`Milestone_3_Final.pdf`, October 23, 2026). Where the October 19 draft, the
Milestone 2 SRS and Milestone 3 disagree, Milestone 3 wins: its section 3 records the accepted
changes and their new IDs (FR-216, FR-405, FR-406, NFR-718).

Nothing here has been merged to `main` or pushed. Branches kept unchanged for comparison:

| Ref | Contents |
|---|---|
| `backup/origin-main-b684c07` | `main` at the start of this work |
| `backup/gabriel-data-state-cb55fce` | Gabriel's unmerged branch |
| `backup/gabriel-stash-2026-11-08` | Gabriel's saved stash (`stash@{0}` is also still in place) |

## Decisions

| ID | Decision | Why | Affects |
|---|---|---|---|
| D-01 | **Port Gabriel's `cb55fce`; don't merge it.** Its migrations, endpoints, retention and tests were reimplemented on top of current `main`. | `cb55fce` branches from before José's and Diushen's merges. Its `main.py` would replace José's real `/extract` with a placeholder and Diushen's analysis and guidance with stubs. It would also remove Luis's rate limit, constant-time compare, CORS and startup checks. It conflicts in 8 files. | All |
| D-02 | **Duplicate migration 004: keep José's `004_capture_inputs.sql`.** Gabriel's 004–007 become one additive `005_final_data_design.sql`. No applied migration is edited or renamed. | Running both 004s fails with "column already exists", and the backend would not start. 005 only tightens José's nullable `input_type` (backfill, then NOT NULL). | Gabriel, José |
| D-03 | **Final limits restored:** quantity > 0, at most 2 decimals, ≤ 99,999.99; price ≥ 0, ≤ 9,999,999.99; names 1–100 characters. Checked in the API, the UI, and the database (`NOT VALID` constraints). | M3 section 3 and 7.2 define them. Gabriel's migration 003 had removed them, based on the October 19 draft. José's ingestion already clamped to them, so the old code was inconsistent. | Gabriel, José, Diushen (tests now use the boundary values) |
| D-04 | **Lifecycle follows M3 Diagram 4.** All open states confirm directly; UPLOADED/CLASSIFYING go stale to AI_UNAVAILABLE; manual entry is inserted CONFIRMED. The `manual-review` endpoint and button are removed. | The final state diagram has no REJECTED/AI_UNAVAILABLE → NEEDS_VERIFICATION transition, and Gabriel's branch already matched it. | Gabriel, José (UI) |
| D-05 | **Error body is `{error: {code, message, fields}}` on every non-2xx; validation is 400 everywhere.** `detail` is kept beside it as a compatibility alias. | M3 4.2. The shared frontend client already read both shapes, so nothing broke. The alias keeps older clients and tests working, and never contains submitted values. | All |
| D-06 | **Integer draft line IDs** (M3 7.3). Older string IDs still match on read and confirm. | José emitted strings and Gabriel's branch required integers. Accepting both avoids breaking drafts saved before this change. | José, Gabriel |
| D-07 | **Exact decimals stay JSON strings**, not JSON numbers as M3 4.2 says. | Diushen's exactness design: a JSON number becomes a float in the browser. Every frontend component already uses strings. Recorded as a deviation for team sign-off. | Diushen, Luis |
| D-08 | **`GET /sales` returns `{start, end, records}`** with M3's record fields (`recordId`, `lineNo`, `lineTotal`, …), not a bare array. | Keeps the response extensible, and the client keeps reading `records`. The range rules are M3's (28-day default, ≤ 366 days). | Gabriel, Luis |
| D-09 | **Analysis status keeps `NO_DATA` and `ESTIMATE_ONLY`** in addition to M3's `READY`/`INSUFFICIENT_HISTORY`. | A superset: FR-602 needs a distinct no-data state, and 7–13 dates give estimates without a trend (FR-405). | Diushen |
| D-10 | **Snapshots are written, never read back.** Every analysis or guidance run replaces that date's `BASELINE_ESTIMATES` rows (SD-03 saveBaselines). Responses are always recomputed. | M3 section 10: "Guidance recomputes baselines"; a stale snapshot can never be shown. | Diushen, Gabriel |
| D-11 | **`sale_date` stays on SALES_RECORDS**, tied to the parent by the composite FK; `line_number` 0–99 is exposed as `lineNo` 1–100. | Same integrity as the ERD without rewriting immutable rows or teammates' queries (DATA_DICTIONARY §7). | Gabriel |
| D-12 | **Rate limit keys on the right-most trusted `X-Forwarded-For` hop** (`TRUSTED_PROXY_HOPS`, 1 on Render), and uvicorn no longer trusts proxy headers. | `--forwarded-allow-ips='*'` let any client bypass NFR-718 by rotating the left-most entry. Assumption to verify on first deploy: Render appends exactly one hop. | Luis |
| D-13 | **Retention service** runs at startup and every 6 hours. Images are deleted through an outbox, after commit. | M3 7.4; a storage outage can never block or roll back a database delete. Retention is 30 days per M3, not the SRS's 24 hours (`docs/privacy.md`). | Gabriel, José |
| D-14 | **Text containing markup or control characters is rejected (400).** | NFR-714 requires script-injection text to be rejected. Sales logs never need tags; a lone `<` or `&` is still accepted. | José |
| D-15 | **Claude wording provider uses a strict tool with `tool_choice: auto`**, `max_retries=0` and a 15 s timeout. Any refusal, truncation or missing call gives Standard advice. | Current models reject forced tool choice; M3 SD-03 allows one attempt and no retry. | Diushen |
| D-16 | **Gabriel's stash is not applied.** | It contains parallel copies of José's ingestion and Diushen's analysis; its analysis is the replaced M2 formula. The useful UI parts (date filter, typed DELETE, discard) were rebuilt against the final contract. It is kept in `backup/gabriel-stash-2026-11-08`. | Gabriel |
| D-17 | **Plain CSS stays; Tailwind is not adopted.** | M3 4.1/4.3 name Tailwind, but NFR-731–734 are met with the existing mobile-first CSS (browser-checked). Converting would rewrite every screen for no functional change. **Needs team sign-off.** | Luis |
| D-18 | **psycopg + SQL migrations stay**, not SQLAlchemy + Alembic, and the module layout is flat (`app/*.py`), not `app/api`, `app/capture`, … | Already in place before Milestone 4; same behaviour. **Needs team sign-off** to record as deviations. | All |

## Decisions that need the team

D-07, D-17 and D-18 are deviations from the wording of Milestone 3. Each is justified above, but
only the team can accept it. The SRS also needs the Milestone 3 changes applied to its text:
FR-212 (pattern), FR-215/NFR-715 (30 days) and FR-304 (per-submission delete).
