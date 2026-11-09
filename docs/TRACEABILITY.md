# Requirements traceability and verification status

Baseline: the Milestone 2 SRS v2.0, as changed by Milestone 3 section 3 (final, October 23,
2026). That gives 37 functional requirements (34 + FR-216, FR-405, FR-406) and 26 non-functional
requirements (25 active + NFR-718). Milestone 1 is not in the repository or the workspace, so it
is traced only through the product scope that Milestones 2 and 3 carry forward.

**Status**

| Status | Meaning |
|---|---|
| **V** | Implemented, and an automated test in this repository verifies it. The test was run on this branch (section 4). |
| **V\*** | Implemented and verified, but only with a test double for an external service. The real service has not been exercised. |
| **P** | Implemented in part, or measured only in part. The gap is named. |
| **B** | Blocked by something this environment cannot provide (credentials, deployment, people, real data). The blocker is named. |

## 1. Functional requirements

| ID | Requirement (short) | St. | Evidence (code → test) |
|---|---|---|---|
| FR-101 | JPEG/PNG ≤ 10 MiB, decoded type matches, with a sales date | V | `main.extract`, `privacy.detect_image_type` → `test_ingestion`, `test_invalid_inputs` |
| FR-102 | Typed text (1–5,000 characters) with a date | V | `privacy.text_problem` → `test_ingestion`, `test_invalid_inputs` |
| FR-103 | Reject no input, both inputs, bad files, missing/invalid/future dates; name the field; no AI call | V | → `test_invalid_inputs` (30 cases: 400, field named, `model.calls == []`) |
| FR-104 | Manual rows with no AI request; same validation as drafts | V | `POST /manual-entries`, `ManualEntry.jsx` → `test_final_contract`, `ManualEntry.test`, `smoke_http` |
| FR-201 | Send input via the backend; turn the response into draft rows | V\* | `ClaudeVisionModel`, `InferenceService` → `test_ingestion` (mock). **Real Claude extraction not run: no authorized key.** |
| FR-202 | Flag invalid fields: nonblank item, quantity > 0 (2 dp, ≤ 99,999.99), price ≥ 0 | V | `SalesLine`, `draftRules.js`, 005 constraints → `test_database`, `SalesReview.test` |
| FR-203 | Correct or remove rows before confirming | V | `DraftReview.jsx` → `SalesReview.test` |
| FR-204 | Block empty or invalid drafts and name the problem | V | → `SalesReview.test`, `test_database` |
| FR-205 | Failure or no-records message; history unchanged | V | `apply_confidence_bands` (REJECTED, "No sales lines found.") → `test_ingestion` |
| FR-206 | Confidence 0–1 per field and per submission, banded before display | V | `ExtractionResult` bounds, `band_for` → `test_ingestion` (boundaries 0.40/0.60/0.85/null) |
| FR-207 | ≥ 0.85 prefilled with no warning; whole draft still confirmed | V | → `test_ingestion`, `SalesReview.test` |
| FR-208 | 0.60–0.85 or unscored: "Please verify", tick or edit required | V | `DraftReview.jsx` and server `verifiedFields` check → `SalesReview.test`, `test_database` |
| FR-209 | < 0.60: blank, "Could not read - enter value" | V | → `SalesReview.test`, `test_ingestion` |
| FR-210 | Legibility < 0.40: no rows, retake guidance, manual option | V | REJECTED message, `ReviewPage` retake link → `test_ingestion` |
| FR-211 | 30 s cancel, one retry, unavailable message, input kept, manual entry, no records | V | `_call_with_deadline`, `MAX_ATTEMPTS=2` → `test_ingestion`, `test_resilience` (20 trials). Input kept 30 days (M3 7.4; the M3 contract has no "retry extraction" endpoint). |
| FR-212 | Mask phones and emails before the AI call | V | M3 9.5 pattern (team decision over the SRS "7+ digits") → `test_masking` (50 cases) |
| FR-213 | Strip EXIF/GPS/device/time before the AI call | V | `sanitize_image` → `test_privacy` |
| FR-214 | Input notice on the capture form | V | Exact M3 9.7 text → `SalesCapture.test` |
| FR-215 | Delete raw input after confirm/discard | V | 30-day purge (M3 section 3 clarification; the SRS says 24 h) → `test_final_contract` |
| FR-216 | Pending drafts: list, reopen, confirm, discard | V | `GET/DELETE /submissions` → `test_final_contract`, `App.test`, `smoke_http` |
| FR-301 | Save confirmed rows with ID, submission, date, item, quantity, price | V | → `test_database` |
| FR-302 | One transaction; a repeat creates no duplicates | V | `FOR UPDATE`, deferred triggers → `test_database` (rollback, concurrency), `smoke_http` |
| FR-303 | Show confirmed history | V | `GET /sales`, `SalesHistory.jsx` → `test_final_contract`, `SalesReview.test` |
| FR-304 | Delete after typing DELETE; report the count; cancel deletes nothing | V | Per-submission delete (M3 section 3) → `SalesReview.test`, `test_final_contract`. "Delete everything at once" is not offered (M3 clarification). |
| FR-401 | Daily quantity per item, using the matching rule | V | `build_analysis` → `test_analysis_math` |
| FR-402 | Average over the latest 7 logged days in a 28-day window, exact, shown to 1 dp | V | `decimal_text`, `one_decimal` → `test_analysis_math`, `test_insights_api` |
| FR-403 | Fewer than 7 logged days: no estimate | V | → `test_insights_api`, `ResultsDashboard.test` ("N of 7 so far") |
| FR-404 | Confirmed rows only; recalculated on open/refresh | V | → `test_insights_api` (drafts excluded, refresh) |
| FR-405 | Trend ±10% over 14 logged days | V | `classify_trend` → `test_analysis_math` (exact thresholds) |
| FR-406 | Suggested stock = ceil(avg × 7), labelled, with the stock notice | V | → `test_insights_api`, `ResultsDashboard.test` (exact label and notice) |
| FR-501 | AI guidance from calculated figures only | V\* | `ClaudeLLMService` → `test_llm_provider` (fake client), `test_grounding`. **Real Claude wording not run: no authorized key.** |
| FR-502 | Guidance beside the figures, with the stock-check notice | V | → `ResultsDashboard.test` |
| FR-503 | On failure, template advice; no request with too little history | V | M3: "Standard advice"; 409 with no provider call → `test_insights_api`, `test_grounding` |
| FR-504 | Grounding check; fallback when it fails | V | `check_grounding` (all 7 M3 rules plus a closed vocabulary) → `test_grounding` (incl. template-passes-by-construction) |
| FR-505 | 15 s cancel, figures stay, can retry | V | → `test_insights_api`, `test_resilience` (20 trials) |
| FR-601 | Chart/table of daily quantities, date range, logged dates, estimate label | V | `SalesTrendChart.jsx` → `ResultsDashboard.test` |
| FR-602 | No-data message and a path to the input form; nothing fabricated | V | → `ResultsDashboard.test` |

**37 of 37 implemented. 35 V, 2 V\* (FR-201, FR-501), which need an authorized AI provider key.**

## 2. Non-functional requirements

| ID | Requirement (short) | St. | Evidence / gap |
|---|---|---|---|
| NFR-701 | Validation result ≤ 2.0 s (p95) | V | `test_invalid_inputs` times all 30 cases (< 2.0 s each) |
| NFR-702 | Draft rows ≤ 8 s after an AI response | P | Backend banding is instant (mock). Not measured end to end with a real provider in a browser. |
| NFR-703 | Analysis ≤ 3.0 s p95 at 5,000 records | V | `test_performance`: **p95 0.159 s** (28 days × 100 items, PostgreSQL 16) |
| NFR-704 | Dashboard renders ≤ 3.0 s on the 4G profile | B | Needs a throttled-network browser run against a deployed stack. |
| NFR-705 | Progress ≤ 1 s; outcome ≤ 65 s (extraction) / 16 s (guidance) | V | Spinner on submit (`SalesCapture.test`); 30+1+30 s and 15 s asserted (`test_resilience`) |
| NFR-711 | TLS 1.2+, plain HTTP refused or redirected | P | HSTS on API and nginx (tested). Render terminates TLS. **TLS and redirect not verified until deployed.** |
| NFR-712 | No secrets in the browser bundle or the repository; automated scan with 0 findings | V | gitleaks: 0 leaks in all history and branches; CI job `static-checks` |
| NFR-713 | Guidance payload has calculated figures only | V | `test_grounding`, `test_llm_provider` |
| NFR-714 | 30-case invalid/malicious set, 100% rejected server-side | V | `test_invalid_inputs` |
| NFR-715 | No raw input older than the retention limit | V | `test_final_contract` purge; 30 days per M3 (SRS says 24 h) |
| NFR-716 | Provider terms: no training, ≤ 30 days retention | B | Account agreement not checked (`docs/privacy.md` §3) |
| NFR-717 | 50-case masking set, 100%; < 7 digits unchanged | V | `test_masking` |
| NFR-718 | Access code; 401; 429 after 10 failures per IP | V | `test_security`, including the spoofed-XFF regression (bypass fixed) |
| NFR-721 | ≥ 99% availability in demo windows | B | Needs the Render deployment and a 5-minute health monitor |
| NFR-722 | Non-AI functions work with the AI down | V | `test_resilience` (manual entry, history, analysis, guidance fallback, deletion); smoke stack runs with no provider |
| NFR-723 | Error logs with UTC time, request ID, component, operation, category, duration; no secrets; 30 days | P | JSON logs tested (`test_final_contract`). The 30-day retention depends on the Render plan. |
| NFR-724 | 20 forced-timeout trials each, 100% fallback, 0 orphan rows | V | `test_resilience` |
| NFR-725 | 0 lost or partial records over 10 forced restarts during confirmation | P | Atomic rollback and the deferred integrity triggers are tested; the 10-restart trial was not run |
| NFR-731 | Messages: what failed, one action, ≤ 30 words, grade ≤ 8, no codes | P | Fixed plain-English messages; word count checked for the API envelope. No readability (Flesch-Kincaid) measurement. |
| NFR-732 | 360–1920 px, no horizontal scroll, ≥ 16 px text, ≥ 44 px targets | V | Playwright + Microsoft Edge (Chromium): 5 pages × 360/768/1280/1920 px, 0 overflow, 0 text under 16 px, 0 targets under 44 px (section 4) |
| NFR-733 | Usability test: 4 of 5 new users in ≤ 3 minutes | B | Needs 5 participants |
| NFR-734 | Guidance ≤ 150 words, grade ≤ 8 | P | ≤ 40 words per item enforced. With many items the total can pass 150 words; no readability measurement. |
| NFR-751 | ≥ 95% of unflagged fields correct | B | Harness ready (`benchmarks/extraction_benchmark.py`); needs 20 real pages and a key |
| NFR-752 | ≥ 9 of 10 unreadable images give no rows | B | Same harness and blocker |
| NFR-753 | 0 ungrounded guidance texts; ≥ 80% pass the check | P | 0 ungrounded is enforced by the grounding check (tested). The ≥ 80% pass rate needs the real provider. |
| NFR-761 | Two latest versions of Chrome, Safari, Firefox and Edge; Android and iOS | P | Only current Microsoft Edge (Chromium) was checked; Safari, Firefox, Android and iOS were not run |

**26 total: 13 V, 7 P, 6 B.**

## 3. Milestone 3 design conformance

| Area (M3 section) | Status | Notes |
|---|---|---|
| REST contract (4.2) | V | Every endpoint, status code and the error envelope (`test_final_contract`, `smoke_http`). Deviations D-07 (decimal strings), D-08 (`/sales` object) and D-09 (extra statuses) are in INTEGRATION_DECISIONS. |
| Frontend routes and behaviour (4.3) | V | Photo/Text/Enter manually; review rules; drafts with Discard; history with Delete; analysis labels. Tailwind not used (D-17, needs sign-off). |
| Class/interface design (5) | V | VisionModel/LLMService with Claude and Mock realizations; InferenceService; RetentionService; GuidanceEngine; BaselineAnalyzer. Controllers are FastAPI route modules (D-18). |
| Lifecycle (5.6) | V | All 36 state pairs tested |
| Sequences SD-01 to SD-04 (6) | V | SD-01/02/04 in `smoke_http`, `test_ingestion`, `test_resilience`; SD-03 in `test_insights_api` (snapshots saved) |
| Data design (7) | V | `DATA_DICTIONARY.md`; differences D-11 |
| Deployment and DevOps (8) | P | Render blueprint, CI gates, deploy hooks, pinned images. **Not deployed; branch protection not verifiable from here.** Alembic and ruff: D-18 / ruff added. |
| AI pipeline, privacy, grounding, notices (9) | V\* | Everything except real-provider behaviour |
| Risks (12) | P | Free database plan still in `render.yaml` (paid plan recommended for the demo) |

## 4. Verification run on this branch

The runs below used isolated Docker projects with temporary in-memory PostgreSQL. No persistent
volume was touched.

| Suite | Result |
|---|---|
| Backend pytest (`compose.test.yml backend-tests`, image rebuilt from this tree) | **508 passed, 1 skipped**. The skip is the Dockerfile proxy-header guard, which needs the repository file: it passes natively and runs as a CI step. Baseline on `main` was 300. |
| Frontend ESLint, Vitest, Vite build (`frontend-checks`) | Lint clean; 96 tests passed; build OK |
| HTTP smoke through the Vite proxy (`--profile smoke`) | PASS (envelope, markup rejection, fallback, idempotency, manual entry, drafts, deletion, ledger, request IDs, auth) |
| Production images (CI job reproduced) | Backend and nginx images build; SPA route, `config.js`, HSTS; backend refuses to start without settings |
| ruff 0.17.4 | 0 findings |
| gitleaks v8.21.2 | 0 leaks (all commits and branches, plus the working tree) |
| Browser (Playwright 1.49 driving Microsoft Edge) | 20/20 page × width checks pass: no horizontal overflow, all text ≥ 16 px, all targets ≥ 44 × 44 px, **0 axe-core 4.10 WCAG 2.1 AA violations**, 0 console errors. The first run found the masked-text `<pre>` at 13 px; it was fixed and re-checked. |
