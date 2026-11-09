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
| D-11 | **Superseded by migration 006.** SALES_RECORDS now follows the M3 7.1 ERD exactly: no per-row `sale_date` (read from the parent), `line_no` 1–100, and a generated, indexed `item_key`. | The final M3 text is explicit ("saleDate is not repeated on SALES_RECORDS"). Nothing is lost: the old composite FK had guaranteed the dates were equal. The upgrade test proves IDs, values, order and dates survive. | Gabriel, Diushen (queries join the parent) |
| D-12 | **Rate limit keys on the right-most trusted `X-Forwarded-For` hop** (`TRUSTED_PROXY_HOPS`, 1 on Render), and uvicorn no longer trusts proxy headers. | `--forwarded-allow-ips='*'` let any client bypass NFR-718 by rotating the left-most entry. Assumption to verify on first deploy: Render appends exactly one hop. | Luis |
| D-13 | **Retention service** runs at startup and every 6 hours. Images are deleted through an outbox, after commit. | M3 7.4; a storage outage can never block or roll back a database delete. Retention is 30 days per M3, not the SRS's 24 hours (`docs/privacy.md`). | Gabriel, José |
| D-14 | **Text containing markup or control characters is rejected (400).** | NFR-714 requires script-injection text to be rejected. Sales logs never need tags; a lone `<` or `&` is still accepted. | José |
| D-15 | **Claude wording provider uses a strict tool with `tool_choice: auto`**, `max_retries=0` and a 15 s timeout. Any refusal, truncation or missing call gives Standard advice. | Current models reject forced tool choice; M3 SD-03 allows one attempt and no retry. | Diushen |
| D-16 | **Gabriel's stash is not applied.** | It contains parallel copies of José's ingestion and Diushen's analysis; its analysis is the replaced M2 formula. The useful UI parts (date filter, typed DELETE, discard) were rebuilt against the final contract. It is kept in `backup/gabriel-stash-2026-11-08`. | Gabriel |
| D-17 | **Plain CSS stays; Tailwind is not adopted.** | M3 4.1/4.3 name Tailwind, but NFR-731–734 are met with the existing mobile-first CSS (browser-checked). Converting would rewrite every screen for no functional change. **Needs team sign-off.** | Luis |
| D-18 | **psycopg + SQL migrations stay**, not SQLAlchemy + Alembic, and the module layout is flat (`app/*.py`), not `app/api`, `app/capture`, … | Already in place before Milestone 4; same behaviour. **Needs team sign-off** to record as deviations. | All |

| D-19 | **`render.yaml` ships with `AI_PROVIDER` empty.** Extraction falls back to manual entry and guidance to Standard advice until the team enables `claude`. | NFR-716: no sales data may reach a provider whose terms have not been checked, and nobody has recorded them yet (`privacy.md` §3). | Luis, José, Diushen |
| D-20 | **Phone masking boundaries are refined.** The M3 9.5 alternatives are unchanged, but `.`/`,` block a match only next to a digit. | The M3 boundaries let a number before a comma or full stop through unmasked, including the SRS AC-19 example itself. Decimals and thousands are still protected (tested). | José |
| D-21 | **SRS literal texts are kept where M3 did not replace them:** "Image could not be read" (FR-210), "AI reading is temporarily unavailable" (FR-211), "Could not read — enter value" (FR-209, em dash). Each is followed by an action. | The SRS fixes these words; NFR-731 adds the action, word and grade limits. | José, Gabriel |
| D-22 | **Delete-all is added** (`POST /api/v1/data/delete-all`, typed DELETE, "N records deleted") next to M3's per-submission delete. | SRS FR-304, AC-21, UC6 and NFR-722 all require deleting all stored data; M3 added per-submission delete but did not remove this. | Gabriel |
| D-23 | **Readability is enforced in code:** every owner message ≤ 30 words and grade ≤ 8 (tested); AI guidance over 150 words or above grade 8 is withheld. | NFR-731, NFR-734, using the SRS 7.3 verification method. Template advice for 6 or more items exceeds 150 words in total, which conflicts with the fixed M3 template (see "Decisions that need the team"). | Diushen |
| D-24 | **Mermaid sources committed:** `docs/architecture/*.mmd` (M3 Appendix A) and `docs/requirements/SRS.md` (M2 §6), with a CI render check. | M1, M2 §6 and M3 Appendix A each say the sources live in the repository; they did not. | All |

| D-25 | **Known edge case: device timezone different from `APP_TIMEZONE`.** The capture and manual-entry forms default to the device's date (SRS 3.1). The server rejects dates after the business date. A device several hours ahead of the business (for example a UTC device after 20:00 in Santo Domingo) is therefore told the date is in the future, and can pick the business date instead. | M3 makes the business date authoritative; a single-business deployment shares one timezone. Found by the cross-engine run, whose container ran in UTC. | Luis, José |

## Decisions that need the team

D-07, D-17 and D-18 are deviations from the wording of Milestone 3. NFR-734 (≤ 150 words in
total) cannot hold for the fixed M3 template when there are 6 or more eligible items (about 28
words per item). Either the SRS limit becomes per item, or the template becomes shorter. Each is justified above, but
only the team can accept it. The SRS also needs the Milestone 3 changes applied to its text:
FR-212 (pattern), FR-215/NFR-715 (30 days) and FR-304 (per-submission delete).
