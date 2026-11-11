# Requirements traceability and verification status

**Sources**, all read in full:

| Milestone | Document |
|---|---|
| M1 | `Milestone_1_Project_Proposal_and_Opportunity_Study.docx` |
| M2 | `Milestone_2_final.docx`: SRS v2.0, baselined; 99.7% identical to the earlier SRS text. Its only requirement change: FR-211 drops the "retained input deleted within 24 hours" clause. |
| M3 | `Milestone_3_Formal_Analysis_and_Design-1.pdf` (Version 1.0, October 23, 2026). Its section 3 changes and adds requirements; those win over M2. |
| M4 | `WorkDivision.docx`: the Milestone 4 deliverables per member. With M3 it is the reference for the Milestone 4 review below. |

The original documents were read, never modified.

**Status**

| Status | Meaning |
|---|---|
| **V** | Implemented, and verified by a test or measurement run on this branch. |
| **V\*** | Implemented and verified only against a stand-in for an external service, as the SRS allows for acceptance tests. The real service was not exercised. |
| **P** | Implemented in part, or verified in part. The gap is named. |
| **B** | Blocked by something this environment cannot provide (credentials, deployment, people, real data). |

## Milestone 4 — WorkDivision deliverables (review of 2026-11-10)

References: `WorkDivision.docx` (the Milestone 4 deliverables, read in full with both tables and the
execution steps) and the final Milestone 3 (design). Milestones 1 and 2 are not references for this
section. Status letters are defined above. Decisions are in `docs/INTEGRATION_DECISIONS.md`.

### Luis Heyaime — DevOps, CI/CD, application shell

| WorkDivision deliverable | Implementation | Test evidence | Status and blockers |
|---|---|---|---|
| Scaffold `/frontend` and `/backend`; `docker-compose.yml` starts React, FastAPI and PostgreSQL with one command | `docker-compose.yml` (frontend, backend, postgres:16, minio), `backend/Dockerfile`, `frontend/Dockerfile` | `docker compose up -d --build --wait` on empty volumes: 4 services up, `/health` 200, SPA served, 401 without the access code | V |
| Manage `.env` secrets | `.env.example` lists every variable; secrets only in backend environment; startup checks | `test_security.py`; gitleaks in CI | V |
| Render deployment configuration | `render.yaml`; deploy job posts the Render hooks only after every check passes on `main` | Production images build; frontend image serves `/capture`, `config.js` and HSTS | **B** for the live deployment: needs the Render account, deploy-hook secrets and a database plan |
| GitHub Actions runs the automated suites on every pull request | `.github/workflows/ci.yml` on every PR to `main`: Backend Tests, HTTP Smoke Test, Frontend Tests, Lint and Build, Lint and Secret Scan, Production Images | PR #7 checks green before this review; all jobs' commands re-run locally (section 4) | V. The new HTTP Smoke Test job has not run on GitHub yet |
| Branch protection requiring peer reviews | Ruleset "Protect main branch" (active): pull request with 1 approving review, stale approvals dismissed, threads resolved, 3 required checks, no force push or deletion | Read with `gh api repos/User340383/bodega/rulesets/26400400` | V. Open: add the two newer jobs to the required checks (repository setting) |
| React application shell, navigation routing, global API client | `frontend/src/App.jsx`, `layout/AppLayout.jsx`, `auth/*`, `api/client.js`, pages for `/login`, `/capture`, `/review/:id`, `/drafts`, `/history`, `/analysis` | `App.test.jsx`, `api/client.test.js` | V |

### José Javier Otazu — AI ingestion, security QA, capture UI

| WorkDivision deliverable | Implementation | Test evidence | Status and blockers |
|---|---|---|---|
| `POST /api/v1/extract` | `main.extract`, `ingestion.InferenceService`, `vision.py` | `test_ingestion.py`, `test_acceptance.py`, smoke | V\* (mock model). **B** for real Claude: no `AI_API_KEY` |
| File validation, 10 MiB limit | Magic bytes (JPEG/PNG), 10,485,760 bytes, 40 megapixels, sale-date and text rules; 400 and nothing stored | `test_invalid_inputs.py` (30 cases), boundary test at the exact byte limit | V |
| Pillow EXIF metadata stripping | `privacy.sanitize_image`: orientation applied, pixels re-encoded as JPEG q85, long edge 2,048 px | `test_privacy.py`: EXIF/GPS absent from what is stored and sent | V |
| Regex contact masking | `privacy.mask_text` (M3 9.5 pattern, D-20, D-28), applied before storage, before the AI call and to AI output | `test_masking.py` (50+ cases), `test_integration_regressions.py` (prices of 7 digits stay intact) | V |
| 30-second timeout and retry for the VisionModel call | 30 s per attempt, one retry after 1 s for timeout/429/5xx/network/invalid schema, then `AI_UNAVAILABLE` | `test_ingestion.py`, `test_resilience.py` (20 forced timeouts) | V |
| Security QA: unit tests proving EXIF and PII are stripped before AI processing | `test_privacy.py`, `test_masking.py`, `test_ingestion.py` (model and storage receive only sanitized or masked input) | Part of the 646-test run | V |
| Capture UI: photo upload, loading state, manual-entry fallback form | `components/SalesCapture.jsx`, `ManualEntry.jsx`, `captureRules.js`, `pages/CapturePage.jsx` | `SalesCapture.test.jsx`, `ManualEntry.test.jsx` | V |

### Diushen Cabrera — analysis, guidance, integration tests, dashboard

| WorkDivision deliverable | Implementation | Test evidence | Status and blockers |
|---|---|---|---|
| `GET /api/v1/analysis` with pandas 7-day moving averages and trend classification | `analysis.BaselineAnalyzer` (M3 5.5: latest 7 logged days in 28, trend over 14, suggested stock) | `test_analysis_math.py`, `test_insights_api.py`, `test_integration_regressions.py`, smoke (4.0 per logged day, 28) | V. Restored on 2026-11-10 (D-26 withdrawn) |
| `POST /api/v1/guidance` with grounding checks; no unsupported claims returned | `guidance.GuidanceEngine`, `grounding.check_grounding` (M3 9.6 rules 1–7), Standard advice fallback, 409 with fewer than 7 logged days | `test_grounding.py` (one failing case per rule), `test_llm_provider.py`, `test_readability.py` | V\* (mock and fake client). **B** for real Claude wording |
| Integration tests using MockVisionModel and MockLLMService | `tests/mock_vision.py`, `test_vision_output_to_guidance.py` (draft → confirm → analysis → guidance) | Part of the 646-test run | V |
| Results dashboard: baseline estimates, trend directions (UP/DOWN/STABLE), plain-text restocking alerts | `components/ResultsDashboard.jsx`, `SalesTrendChart.jsx`, `pages/AnalysisPage.jsx` | `ResultsDashboard.test.jsx` | V. Restored on 2026-11-10 |

### Gabriel Marte — data and state, database tests, draft review and history

| WorkDivision deliverable | Implementation | Test evidence | Status and blockers |
|---|---|---|---|
| PostgreSQL schema: OWNERS, SUBMISSIONS, SALES_RECORDS, BASELINE_ESTIMATES | `backend/migrations/001`–`006` applied by Alembic revisions 001–006; `app/models.py` | `test_database.py`, `test_models.py`; `alembic current` = `006 (head)` on a fresh database | V |
| `POST /api/v1/confirm`: transactional save and status update | `repository.save_sales_records`: row lock, all rows and CONFIRMED in one transaction, idempotent repeat | `test_database.py`, `test_final_contract.py`, smoke (201 then 200 with the same IDs) | V. Open decision D-30 (422 or 400 for invalid rows) |
| Database tests: transactions roll back on failure; duplicate confirmations are blocked | `test_database.py` (partial insert rolls back, concurrent and repeated confirmation), NFR-725 restart trial | Part of the 646-test run | V |
| Interactive Draft Review table with highlighted fields | `components/DraftReview.jsx`, `draftRules.js`, `pages/ReviewPage.jsx`, `DraftsPage.jsx` | `SalesReview.test.jsx`, `DraftPages.test.jsx` | V |
| Confirmed Sales History ledger | `components/SalesHistory.jsx`, `GET /api/v1/sales` | `SalesReview.test.jsx`, `test_final_contract.py`, `test_integration_regressions.py` (range and paging) | V |

### Immediate execution steps

| Step | Implementation | Evidence | Status |
|---|---|---|---|
| 1. One command starts frontend, backend and PostgreSQL | `docker compose up --build` | Run on empty volumes on 2026-11-10 | V |
| 2. Schema migrations and a small SQL seed script with synthetic sales (bread, milk) | Migrations run at backend start; `backend/seeds/synthetic_sales.sql` (new, D-34) | Run twice with `psql` (14 dates added, then 0); `/analysis` READY: Bread 4.0 UP 28, Milk 2.5 STABLE 18; regression test | V |
| 3. API contract stubs for `/extract`, `/confirm`, `/analysis`, `/guidance` | Superseded: all four endpoints have their real implementation | Smoke test calls each | V |

**Open items for Milestone 4 that cannot be closed locally:** live Claude extraction and wording
(authorized `AI_API_KEY`); R2 storage check (`S3_*` credentials); Render deployment and HTTPS;
the team decisions listed in `docs/INTEGRATION_DECISIONS.md`.

## 0. Milestone 1 — proposal commitments

| M1 commitment | Where it went | Status |
|---|---|---|
| Owner photographs a handwritten log, or pastes WhatsApp-style text | SRS IN-01, FR-101/102; capture Photo and Text tabs (pasted text only: SRS OUT-02 excludes a WhatsApp connection) | V |
| AI #1 extracts JSON (item, quantity, price) from images directly, with no separate OCR pipeline | `ClaudeVisionModel` with a strict tool schema | V\* (real Claude not run: no key) |
| Pandas baseline trends | `BaselineAnalyzer` (pandas with exact fractions) | V |
| AI #2 turns trends into plain-language restocking recommendations | `GuidanceEngine` and `ClaudeLLMService`, with grounding; Standard advice fallback | V\* |
| Stack: React, FastAPI, PostgreSQL, Docker, GitHub Actions | All present, with CI | V |
| React + **Tailwind CSS** | Tailwind 4.3.5; components styled with Tailwind utilities (D-17 resolved) | V |
| Data-visualization dashboard | Results dashboard with SVG chart and an exact-values table | V |
| "Full Mermaid.js files committed to the GitHub repository" | `docs/architecture/*.mmd`, `docs/requirements/SRS.md`; render-checked in CI | V |
| Target users: informal, cash-based merchants needing low-friction guidance | Single-owner access code; plain-language messages (NFR-731 tested) | V (the NFR-733 usability test is B) |
| Example advice "...grown 20% over 3 weeks, so please consider restocking by Friday" | Superseded. M2 FR-501/504 and M3 9.6 forbid unsupported claims, and calendar or weekday claims are not in the grounded evidence. | — |
| Roles: Luis and José on backend; Diushen and Gabriel on frontend | Superseded by the M3 section 13 assignment (Gabriel data/lifecycle; José AI/ingestion; Diushen analysis; Luis architecture/DevOps) | — |
| 10-minute presentation with all four speaking | Not a software requirement | — |

**Contradictions found across the milestones, and how each is resolved**

| Topic | M1 | M2 (SRS) | M3 final | Code follows |
|---|---|---|---|---|
| Baseline formula | Trends over weeks | All-history average, 2 dates minimum | Latest 7 logged days in 28 days; trend over 14 | M3 |
| Guidance on failure | — | Withhold: "Guidance could not be verified" | Standard advice template | M3 |
| Raw-input retention | — | 24 h after confirm/discard (FR-215) | 30 days after upload | M3 (the SRS text needs updating) |
| Phone masking | — | "7+ digits" (FR-212) | Precise NANP pattern (decision) | M3, with a boundary fix (D-20) |
| Delete | — | Delete *all* data (FR-304, AC-21) | Delete any submission | Both (D-22) |
| Quantity type | — | Finite positive number | 2 decimals, ≤ 99,999.99 | M3 |
| Styling | Tailwind | (problem space only) | Tailwind | Tailwind (D-17) |
| NFR-734 vs the M3 template | — | ≤ 150 words in total | Fixed ~28-word template per item | Both: advice covers the top items that fit 150 words (D-23) |
| "Today" for sale dates | — | Owner's device date | Business date in `APP_TIMEZONE` | M3. The form uses the device date and the server the business date; they match for the single business. |

## 1. Functional requirements (M2 + M3 section 3: 37)

| ID | St. | Evidence (code → test) |
|---|---|---|
| FR-101, FR-102 | V | `main.extract`, `privacy` → `test_acceptance` AC-01/02 (exactly 10,485,760 bytes accepted, +1 rejected), `test_invalid_inputs` |
| FR-103 | V | → `test_invalid_inputs` (30 cases, no AI call), AC-02 |
| FR-104 | V | `POST /manual-entries`, Enter-manually tab → AC-18, `ManualEntry.test`, smoke |
| FR-201 | V\* | `ClaudeVisionModel` → stubbed AI only (AC-03, AC-14). **Real Claude not run.** |
| FR-202–FR-204 | V | → AC-03, AC-04, `test_database` limits, `SalesReview.test` |
| FR-205 | V | → AC-04 (service error, unreadable response, no rows) |
| FR-206–FR-209 | V | → AC-14, AC-15 (boundaries 0.85 / 0.60 / 0.59), "Could not read — enter value" |
| FR-210 | V | → AC-16 (0.35 rejected, 0.40 accepted, "We could not read this photo" + retake + manual, M3 9.4) |
| FR-211 | V | → AC-17 (two timeouts → "We could not read this right now", text and date kept, manual entry), `test_resilience` |
| FR-212, FR-213 | V | → AC-19 exact output, `test_masking` (50+ cases), EXIF/GPS removal from what is sent and stored |
| FR-214 | V | → `SalesCapture.test` (exact M3 9.7 notice) |
| FR-215 | V | 30-day purge (M3) → `test_final_contract` |
| FR-216 | V | → `test_final_contract`, `App.test`, smoke |
| FR-301, FR-302 | V | → AC-05, AC-06, rollback/concurrency tests, NFR-725 restart trial |
| FR-303 | V | → AC-05, `test_final_contract` ledger |
| FR-304 | V | Delete all (AC-21: "5 records deleted"; other text or cancel deletes nothing) and per-submission delete → `test_acceptance`, `DeleteAllData.test`, `SalesReview.test` |
| FR-401–FR-406 | V | M3 formulas → `test_analysis_math`, `test_insights_api` |
| FR-501 | V\* | `ClaudeLLMService` → fake client only. **Real Claude not run.** |
| FR-502–FR-505 | V | → `test_grounding`, `test_insights_api`, `test_resilience`, `ResultsDashboard.test` |
| FR-601, FR-602 | V | → `ResultsDashboard.test`, AC-13 |

**35 V, 2 V\*.** Every functional requirement is implemented. The two V\* need an authorized AI key.

**SRS acceptance criteria.**
- In their SRS form (`test_acceptance.py`): AC-01–06, AC-13–19 and AC-21.
- In their M3 form: AC-07, 08, 12 (new averaging rule); AC-09, 10, 11, 22, 23 (Standard advice); AC-20 (30-day retention).

## 2. Non-functional requirements (26)

All timings follow the SRS 5 rule: at least 50 trials, the 95th percentile, and 10 concurrent sessions where load applies. Logs are in `docs/verification/`.

| ID | St. | Evidence / gap |
|---|---|---|
| NFR-701 | V | `prodcheck.py`: 50 invalid submissions over 10 concurrent sessions, production image: **p95 0.051 s** (≤ 2.0) |
| NFR-702 | V | `browser_nfr.mjs`: 50 trials of 50-row drafts in Edge on the 4G profile: **p95 0.087 s** after the AI response (≤ 8.0). The AI was the mock, which the SRS allows ("stubbed AI response"). |
| NFR-703 | V | `prodcheck.py`: 50 refreshes at 5,000 records over 10 concurrent sessions: **p95 2.389 s** (≤ 3.0); single-session p95 0.159 s. Analysis also writes the SD-03 snapshots on every refresh. **Risk: little headroom on a small Render CPU.** |
| NFR-704 | V | 50 dashboard opens, 5,000 records / 100 items; 10/5 Mbps, 100 ms, CPU 4×, cache off: **p95 1.323 s** (≤ 3.0; max 1.353 s). Emulated network and phone, not a physical phone. |
| NFR-705 | V | Indicators p95 0.068 s (extraction) and 0.598 s (guidance; one outlier at 1.208 s) (≤ 1.0); guidance outcome p95 0.659 s (≤ 16); 61 s / 15 s deadlines asserted |
| NFR-711 | P | HSTS on API and nginx (tested). **TLS 1.2+ and the HTTP→HTTPS redirect need the deployed Render service.** |
| NFR-712 | V | gitleaks: 0 leaks in all commits and branches, the working tree, and the built browser bundle; CI runs both |
| NFR-713 | V | `test_grounding`, `test_llm_provider` (payload inspected) |
| NFR-714 | V | `test_invalid_inputs`: 30 of 30 rejected server-side |
| NFR-715 | V | Purge tested; 30 days per M3 (the SRS text says 24 h) |
| NFR-716 | **B** | The provider's published terms must be checked by the account owner (`privacy.md` §3). Production ships with AI off (D-19). |
| NFR-717 | V | `test_masking`: 50-case set plus AC-19 and punctuation cases; numbers under 7 digits unchanged |
| NFR-718 | V | `test_security`, including the spoofed X-Forwarded-For regression |
| NFR-721 | **B** | Needs the deployment and a 5-minute monitor over the demo windows |
| NFR-722 | V | `test_resilience`: AC-05/07/12/18/21 functions with no provider |
| NFR-723 | P | Fields tested on AI, storage, database and HTTP failures, with 0 sensitive values. **30-day log retention depends on the Render plan.** |
| NFR-724 | V | `test_resilience`: 20 + 20 forced timeouts, 0 orphan rows |
| NFR-725 | V | `scripts/nfr725_restart_trial.py`: **10 of 10 SIGKILLs mid-confirmation, 0 lost and 0 partial** |
| NFR-731 | V | `test_readability` and `messages.test.js`: every owner message ≤ 30 words, grade ≤ 8, with an action and no codes |
| NFR-732 | V | Edge at 360/768/1280/1920: no overflow, text ≥ 16 px, targets ≥ 44 px, 0 axe WCAG 2.1 AA violations |
| NFR-733 | **B** | Needs 5 first-time participants |
| NFR-734 | V | Displayed advice ≤ 150 words and grade ≤ 8 for any number of items: the items with the highest suggested stock that fit; AI wording over the limits is withheld. Tested in `test_readability`. |
| NFR-751, NFR-752 | **B** | The harness is ready (`benchmarks/`). It needs an authorized key and the benchmark photos; the SRS asks for 30 photos and 20 messages, and no real sales-log photos exist in the repository or workspace. |
| NFR-753 | P | "0 ungrounded displayed" is enforced and tested; "≥ 80% displayable" needs the real provider |
| NFR-761 | P | Core workflow (sign-in, manual entry, capture → review → confirm, history, dashboard, delete-all) plus layout passes in **Chromium 131, Firefox 132, WebKit 18.2** and Android/iPhone emulation (`docs/verification/NFR-761-browser-engines.txt`). Edge (Chromium) on Windows runs the NFR-70x timings. **Branded Chrome/Safari/Firefox desktop releases and physical Android 10+/iOS 16+ devices were not tested.** |

**17 V, 4 P, 5 B.**

## 3. Milestone 3 design conformance

| Area | Status | Notes |
|---|---|---|
| REST contract (4.2) | V | Amounts are JSON numbers (D-07 resolved). D-08 and D-09 are recorded supersets. `delete-all` is additive (D-22). |
| Frontend (4.3) | V | Tailwind with `md` breakpoints (D-17 resolved) |
| Classes and interfaces (5) | V | SQLAlchemy models and Alembic (D-18 resolved); controllers are FastAPI route modules |
| Lifecycle (5.6) | V | 36 pairs tested |
| Sequences SD-01–04 (6) | V | Mocked AI |
| Data design (7) | V | ERD shape since 006; models checked against the migrated schema |
| Deployment and DevOps (8) | P | Production topology checked locally (`prodcheck`). **Not deployed; branch protection unverifiable here.** |
| AI pipeline, privacy, grounding (9) | V\* | |
| Appendix A sources in `docs/architecture/` | V | Recovered from the PDF and render-checked |

## 4. Verification run on this branch

Run on 2026-11-10 on the working tree of `work/srs-completion` (commit recorded in the pull
request), in isolated Compose projects with temporary databases and synthetic data only:

| Check | Result |
|---|---|
| Backend pytest against disposable PostgreSQL 16 | **646 passed, 1 skipped**, 0 failed. The skip is the Dockerfile guard, which runs as a CI step. |
| Migrations | `alembic upgrade head` on an empty database, and again as a no-op; `alembic current` = `006 (head)` |
| Frontend ESLint, Vitest, Vite build | Lint clean; **132 passed** (9 files); build OK |
| Ruff 0.17.4 (`ruff check backend`) | 0 findings |
| HTTP smoke test (PostgreSQL, MinIO, SPA proxy, two backends) | PASS |
| Production images | Backend and frontend images build; the frontend image serves `/capture`, `config.js` and HSTS |
| One-command local stack | `docker compose up -d --build --wait` on empty volumes; extract, analysis and guidance answered over HTTP |
| SQL seed script | Applied twice with `psql`; analysis READY with the documented figures |
| Live Claude check (`scripts/live_ai_check.py`) | **Not run:** no `AI_API_KEY` on this machine |
| Live storage check (`scripts/live_storage_check.py`) | **Not run:** no `S3_*` credentials |
| Render deployment, HTTPS | **Not run:** needs the Render account |

Not re-run on 2026-11-10 (last run 2026-11-09 on `32ffbdd`, before the analysis page was
restored): `prodcheck.py` 13/13, browser timings, cross-engine workflow 5/5, axe and layout 20/20,
NFR-725 restart trial 10/10, gitleaks, Mermaid render 14/14. CI repeats gitleaks and Mermaid.

Evidence files:

| File | Contents |
|---|---|
| `docs/verification/NFR-725-restart-trial.txt` | The 10 forced-restart trials |
| `docs/verification/NFR-70x-browser-timings.txt` | 4G browser timings, 50 trials |
| `docs/verification/NFR-761-browser-engines.txt` | Workflow in 3 engines and 2 device emulations |
