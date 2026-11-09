# Requirements traceability and verification status

**Sources**, all read in full:

| Milestone | Document |
|---|---|
| M1 | `Milestone_1_Project_Proposal_and_Opportunity_Study.docx` |
| M2 | `Milestone_2_final.docx`: SRS v2.0, baselined; 99.7% identical to the earlier SRS text. Its only requirement change: FR-211 drops the "retained input deleted within 24 hours" clause. |
| M3 | `Milestone_3_Final.pdf` (October 23, 2026). Its section 3 changes and adds requirements; those win over M2. |

The original documents were read, never modified.

**Status**

| Status | Meaning |
|---|---|
| **V** | Implemented, and verified by a test or measurement run on this branch. |
| **V\*** | Implemented and verified only against a stand-in for an external service, as the SRS allows for acceptance tests. The real service was not exercised. |
| **P** | Implemented in part, or verified in part. The gap is named. |
| **B** | Blocked by something this environment cannot provide (credentials, deployment, people, real data). |

## 0. Milestone 1 — proposal commitments

| M1 commitment | Where it went | Status |
|---|---|---|
| Owner photographs a handwritten log, or pastes WhatsApp-style text | SRS IN-01, FR-101/102; capture Photo and Text tabs (pasted text only: SRS OUT-02 excludes a WhatsApp connection) | V |
| AI #1 extracts JSON (item, quantity, price) from images directly, with no separate OCR pipeline | `ClaudeVisionModel` with a strict tool schema | V\* (real Claude not run: no key) |
| Pandas baseline trends | `BaselineAnalyzer` (pandas with exact fractions) | V |
| AI #2 turns trends into plain-language restocking recommendations | `GuidanceEngine` and `ClaudeLLMService`, with grounding; Standard advice fallback | V\* |
| Stack: React, FastAPI, PostgreSQL, Docker, GitHub Actions | All present, with CI | V |
| React + **Tailwind CSS** | Plain mobile-first CSS meeting NFR-731–734 | P: deviation D-17, needs team sign-off |
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
| Styling | Tailwind | (problem space only) | Tailwind | Plain CSS (D-17, needs sign-off) |
| NFR-734 vs the M3 template | — | ≤ 150 words in total | Fixed ~28-word template per item | Conflict for 6+ items (needs a team decision) |
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
| FR-210 | V | → AC-16 (0.35 rejected, 0.40 accepted, "Image could not be read" + retake + manual) |
| FR-211 | V | → AC-17 (two timeouts → "AI reading is temporarily unavailable", text and date kept, manual entry), `test_resilience` |
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
| NFR-701 | V | `prodcheck.py`: 50 invalid submissions over 10 concurrent sessions, production image: **p95 0.025 s** (≤ 2.0) |
| NFR-702 | V | `browser_nfr.mjs`: 50 trials of 50-row drafts in Edge on the 4G profile: **p95 0.074 s** after the AI response (≤ 8.0). The AI was the mock, which the SRS allows ("stubbed AI response"). |
| NFR-703 | V | `prodcheck.py`: 50 refreshes at 5,000 records over 10 concurrent sessions: **p95 1.957 s** (≤ 3.0); single-session p95 0.159 s. Risk: little headroom on a small Render CPU. |
| NFR-704 | V | 50 dashboard opens, 5,000 records / 100 items; 10/5 Mbps, 100 ms, CPU 4×, cache off: **p95 1.269 s** (≤ 3.0; one outlier trial reached 3.027 s). Emulated network and phone, not a physical phone. |
| NFR-705 | V | Indicators p95 0.096 s (extraction) and 0.442 s (guidance) (≤ 1.0); guidance outcome p95 0.505 s (≤ 16); 61 s / 15 s deadlines asserted |
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
| NFR-734 | P | AI wording over 150 words or above grade 8 is withheld (tested). **The fixed M3 template exceeds 150 words in total for 6 or more items** (team decision). |
| NFR-751, NFR-752 | **B** | The harness is ready (`benchmarks/`). It needs an authorized key and the benchmark photos; the SRS asks for 30 photos and 20 messages, and no real sales-log photos exist in the repository or workspace. |
| NFR-753 | P | "0 ungrounded displayed" is enforced and tested; "≥ 80% displayable" needs the real provider |
| NFR-761 | P | Core workflow (sign-in, manual entry, capture → review → confirm, history, dashboard, delete-all) plus layout passes in **Chromium 131, Firefox 132, WebKit 18.2** and Android/iPhone emulation (`docs/verification/NFR-761-browser-engines.txt`). Edge (Chromium) on Windows runs the NFR-70x timings. **Branded Chrome/Safari/Firefox desktop releases and physical Android 10+/iOS 16+ devices were not tested.** |

**16 V, 5 P, 5 B.**

## 3. Milestone 3 design conformance

| Area | Status | Notes |
|---|---|---|
| REST contract (4.2) | V | Deviations D-07, D-08 and D-09 are recorded. `delete-all` is additive (D-22). |
| Frontend (4.3) | V | Tailwind not used (D-17) |
| Classes and interfaces (5) | V | Controllers are route modules (D-18) |
| Lifecycle (5.6) | V | 36 pairs tested |
| Sequences SD-01–04 (6) | V | Mocked AI |
| Data design (7) | V | ERD shape since 006; only SQLAlchemy/Alembic differ (D-18) |
| Deployment and DevOps (8) | P | Production topology checked locally (`prodcheck`). **Not deployed; branch protection unverifiable here.** |
| AI pipeline, privacy, grounding (9) | V\* | |
| Appendix A sources in `docs/architecture/` | V | Recovered from the PDF and render-checked |

## 4. Verification run on this branch

Final run on HEAD `77889fd`, 2026-11-09, in isolated Compose projects with temporary in-memory databases:

| Check | Result |
|---|---|
| Backend pytest | **599 passed, 1 skipped**. The skip is the Dockerfile guard: it passed natively and runs as a CI step. |
| Frontend ESLint, Vitest, Vite build | Lint clean; **126 passed**; build OK |
| HTTP smoke test | PASS |
| `prodcheck.py` | 13/13 checks pass |
| Browser timings | All 5 targets pass at p95 (above) |
| Cross-engine workflow | 5/5 configurations pass |
| axe and layout check on the production bundle | 20/20 page × width combinations pass |
| NFR-725 restart trial | 10/10 pass |
| ruff | 0 findings |
| gitleaks | 0 leaks (history, branches, working tree, browser bundle) |
| Mermaid | 14/14 diagrams render |

Evidence files:

| File | Contents |
|---|---|
| `docs/verification/NFR-725-restart-trial.txt` | The 10 forced-restart trials |
| `docs/verification/NFR-70x-browser-timings.txt` | 4G browser timings, 50 trials |
| `docs/verification/NFR-761-browser-engines.txt` | Workflow in 3 engines and 2 device emulations |
