# José Javier Otazu: AI ingestion, security QA and capture UI

Milestone 4 assignment (`WorkDivision`): **AI Ingestion** (`POST /api/v1/extract`: file validation, Pillow EXIF stripping, PII masking, 30-second AI timeout logic), **Security QA** (automated tests proving EXIF and PII are removed before AI processing) and **Capture UI** (photo upload, loading screen, manual-entry fallback). Built on `main` after Gabriel's data/confirm work (PR #1) and Diushen's analysis/guidance work (PR #3), following Milestone 3 sections 5.4, 6.1, 6.2, 6.4 and 9.

## 1. Pipeline (Milestone 3, Diagram 7)

```text
POST /api/v1/extract (saleDate + exactly one of file | text)
  validate ──fail──> 400 {detail:[{loc,msg,type}]}  (nothing stored, no AI call)
  photo: magic bytes JPEG/PNG, ≤ 10 MiB, ≤ 40 MP ─> sanitize_image ─> put_object (503 on failure)
  text:  1–5,000 chars after trim ─> mask_text
  create_submission (UPLOADED, input_type, masked_text | image_key)
  CLASSIFYING ─> VisionModel (30 s) ─retryable─> wait 1 s ─> attempt 2 (30 s)
     ok   ─> mask contacts in output ─> confidence bands ─> NEEDS_VERIFICATION | REJECTED
     fail ─> AI_UNAVAILABLE (manual entry; never an error page)
  200 ExtractResponse = Gabriel's draft view + inputType, maskedText, imageUrl (300 s signed URL)
```

The response feeds Gabriel's `DraftReview` directly, and confirmation goes through his existing locked, idempotent `POST /api/v1/confirm`. Lines without a `draftLineId` (typed after an AI failure) are stored with source `MANUAL`.

## 2. Files

| File | Purpose |
| --- | --- |
| `backend/app/privacy.py` | Magic-byte type detection, `sanitize_image` (orientation applied, pixels copied into a fresh image, JPEG q85, long edge ≤ 2,048 px, no EXIF/XMP/IPTC/ICC), `mask_text` with the exact NANP-aware phone pattern and email pattern from M3 9.5. |
| `backend/app/vision.py` | `VisionModel` interface; `ProviderTimeoutError` / `ProviderRetryableError` / `ProviderFatalError`; Pydantic `ExtractionResult`; extraction prompt and strict `record_sales` tool schema; `ClaudeVisionModel`; `MockVisionModel`; `UnavailableVisionModel`; `get_vision_model` dependency. |
| `backend/app/ingestion.py` | `InferenceService`: 30 s hard deadline per attempt (daemon thread, so a hung provider cannot hold the request), one retry after 1 s for timeout/429/5xx/network/schema-invalid output, no retry for other errors; `apply_confidence_bands`. |
| `backend/app/storage.py` | `ObjectStorage` (boto3; MinIO locally, R2 in production; creates the local bucket on first use), `InMemoryObjectStorage` for tests, `UnconfiguredObjectStorage` (photos → 503, text still works). |
| `backend/migrations/004_capture_inputs.sql` | Additive, nullable `submissions.input_type` and `submissions.masked_text` from the M3 ERD. |
| `backend/tests/test_privacy.py` | Security QA: EXIF/GPS/device/time removed, orientation, resizing, magic bytes, size and megapixel limits, every phone/email example in M3 9.5, sales lines and ISO dates untouched. |
| `backend/tests/test_ingestion.py` | Band boundaries (0.40, 0.60, 0.85, null), retry/timeout/fatal paths, hung-provider deadline, and API tests proving the model and storage receive only sanitized/masked data, logs contain no input, 503 creates nothing, AI failure → manual confirm. |
| `frontend/src/components/SalesCapture.jsx`, `captureRules.js`, `SalesCapture.css` | Photo/Text tabs, sale-date picker, FR-214 notice (exact text), client-side checks, spinner with “Reading your sales log… this can take up to a minute”, 90 s request timeout, fallback/retake messages, masked-text and stored-photo preview. |
| `frontend/src/components/SalesCapture.test.jsx` | Vitest/Testing Library tests for the capture screen. |

Shared files changed (small, additive): `main.py` (real `/extract`; `GET /submissions/{id}` also returns `inputType`, `maskedText`, `imageUrl`), `repository.py` (`create_submission` accepts `input_type`/`masked_text`; new `get_capture_input`; `get_submission` also returns the draft `message`), `test_database.py` (migration count 3 → 4), `App.jsx` (mounts `SalesCapture`; JSON content type is no longer forced on `FormData` uploads), `requirements.txt` (`pillow`, `boto3`, `anthropic`), `docker-compose.yml` and `.env.example` (`AI_API_KEY`, `AI_EXTRACTION_MODEL`, `S3_PUBLIC_ENDPOINT_URL`).

## 3. Confidence bands (M3 5.4)

- `overallConfidence < 0.40` → `REJECTED`, retake guidance. Lines whose three values are all null are dropped; zero lines → `REJECTED`, “No sales lines found.”
- Per field: `HIGH` c ≥ 0.85; `MEDIUM` 0.60 ≤ c < 0.85; `LOW` c < 0.60 or value missing (value cleared); `UNSCORED` value present, confidence missing.
- Every readable draft is `NEEDS_VERIFICATION`, even when all fields are HIGH.
- Extra safety: values outside the confirm limits (quantity ≤ 0 or > 99,999.99, price < 0 or > 9,999,999.99) are cleared to LOW; values rounded to 2 decimals or names cut to 100 characters are downgraded from HIGH to MEDIUM so the owner checks them.
- Draft `lineId`s are the strings `"1".."n"` (Gabriel's repository requires string IDs).

## 4. Provider notes

- `AI_PROVIDER=mock` (Compose default): free and deterministic. Typed text in “name quantity price” lines is parsed for real; photos return two rows labelled “(mock)” plus a “Demo mode” message, because a mock cannot read images.
- `AI_PROVIDER=claude` + `AI_API_KEY`: `ClaudeVisionModel`, default model `claude-sonnet-5-5` (M3 9.2), `max_retries=0`, `max_tokens` 4096.
- **Deviation from M3 9.2, required by the API:** Claude Sonnet 5.5 returns HTTP 400 for a forced `tool_choice` and for a non-default `temperature`. The adapter therefore uses `tool_choice: auto` with `strict: true` on `record_sales` and the prompt's “Call record_sales once”; a reply without the tool call counts as schema-invalid (retryable). Output is still validated with Pydantic.
- Server-side refusal fallback (`fallbacks: "default"`) is enabled; a final refusal is a non-retryable error → `AI_UNAVAILABLE`.
- No provider configured → `AI_UNAVAILABLE` immediately, so manual entry always works (this is what the isolated test stack exercises).

## 5. How to run

```sh
docker compose up --build -d --wait
```

Open http://localhost:5173, connect with `local-development-code-123`, choose **Text**, paste e.g. `Bread 3 1.50` / `Milk 2`, and press **Read text**. The draft opens in the review table below. To use real Claude extraction: `AI_PROVIDER=claude AI_API_KEY=sk-ant-... docker compose up --build -d --wait`.

Tests (same commands as the rest of the team):

```sh
docker compose -f compose.test.yml build
docker compose -f compose.test.yml run --rm backend-tests
docker compose -f compose.test.yml run --rm --no-deps frontend-checks
```

`test_privacy.py` and the unit half of `test_ingestion.py` need no database; the API half uses the isolated `TEST_DATABASE_URL` schema like the other suites. CI never calls a paid API.

## 6. Verification status

Executed November 6, 2026 (Docker Desktop, `compose.test.yml`):

| Check | Result |
| --- | --- |
| Backend suite (all teams, isolated PostgreSQL) | **286 passed** |
| Frontend ESLint, Vitest (56 tests incl. 10 capture tests), Vite production build | **All passed** |
| HTTP smoke stack through the Vite proxy | **PASS** (extraction 400, AI_UNAVAILABLE fallback, confirmation, idempotency) |
| Full `docker compose` app, photo upload | Stored in MinIO (bucket auto-created), `NEEDS_VERIFICATION`, stored copy has no EXIF; signed preview URL returns HTTP 200 `image/jpeg` from the host browser side |
| Full app, typed text | `Call 809-555-1234` stored and sent as `Call [removed]`; `Bread 3 1.50` all HIGH, `Milk 2` with LOW (blank) unit price |

The first backend run found one failure: Gabriel's migration-upgrade test inserts submissions into a pre-004 schema, and `create_submission` always named the new columns. It now writes `input_type`/`masked_text` only when supplied; the rerun passed. Real Claude extraction was not exercised (no API key in this environment); its behaviour is covered only through the provider-error mapping and the mock.

Business date note, observed while testing: in the evening in Santo Domingo the UTC container date is already "tomorrow", and extraction correctly rejects that date because it uses `APP_TIMEZONE`.

## 7. Open items for the team

- **Standalone manual entry (FR-104, `POST /api/v1/manual-entries`)**: not built. Milestone 3 creates such a submission directly as `CONFIRMED`, but Gabriel's transition trigger only allows inserts in `UPLOADED`. This needs Gabriel's agreement on a trigger change. Until then, manual entry is available after any AI failure (AI_UNAVAILABLE / REJECTED → review manually).
- **Retention** (30-day image/masked-text purge, 120 s stale sweep → `AI_UNAVAILABLE`) is `RetentionService` work, not in this milestone's split.
- **Luis**: CI should run `test_privacy.py` / `test_ingestion.py` with `AI_PROVIDER` unset or `mock`; production needs `AI_PROVIDER=claude`, `AI_API_KEY`, the R2 `S3_*` variables and `S3_PUBLIC_ENDPOINT_URL` (can equal `S3_ENDPOINT_URL` for R2).
- **Diushen**: `tests/mock_vision.py` can now be replaced by `app.vision.MockVisionModel` through `POST /api/v1/extract` if you want the end-to-end test to start at upload.
- Before launch, record Anthropic's data-use and retention terms in `docs/privacy.md` (M3 9.8).

## 8. Integration update (`integration/m3-completion`)

- **Standalone manual entry (FR-104) is done:** the "Enter manually" tab and `POST /api/v1/manual-entries`. The trigger change it needed is in migration 005.
- **Line IDs** are now integers (M3 7.3); older string IDs still match.
- **Late results are guarded.** The final status write only applies from CLASSIFYING, so a late provider result cannot overwrite the 120-second stale sweep.
- **Markup is rejected.** Text containing markup or control characters is a 400 (NFR-714).
- **New test sets:** `test_masking.py` (50-case NFR-717), `test_invalid_inputs.py` (30-case NFR-714) and `test_resilience.py` (20 forced timeouts).
- **RetentionService** is in `app/retention.py` (30-day purge, stale sweep, image outbox).
- **`docs/privacy.md` exists.** The NFR-716 provider terms are still unrecorded and need the account owner.
- **Benchmark harness:** `backend/benchmarks/extraction_benchmark.py` (NFR-751/752). No results yet: it needs 20 real pages and an authorized key.

## 9. Milestone 4 review update (2026-11-10)

Reviewed against `WorkDivision.docx` and the final Milestone 3; the full mapping is in `docs/TRACEABILITY.md`.

- **Your deliverables are complete locally** with the mock model: `/extract`, validation (10 MiB), EXIF stripping, masking, 30 s timeout with one retry, the security tests and the capture UI. Real Claude extraction still needs an authorized `AI_API_KEY`.
- **Changes in your files, for your review:** extraction no longer rounds or clamps numbers (D-31: 1.234 was shown as 1.23). The owner-facing fallback texts are now M3 9.4's (D-33). Masking keeps M3's 10-digit rule and adds `809 555 1234`, `(809) 555 1234` and a leading `1-` (D-28). A separate retry endpoint that had been added was removed again (D-29): M3 has no such transition, and the in-request retry is unchanged.
