# Bodega privacy and data handling

Milestone 3, section 9.8 asks the team to record what Bodega sends to its AI provider and how
long it keeps data. This page describes what the code does today. Each claim names the code and
the automated test that enforces it.

## 1. What leaves Bodega

| Call | Data sent | Never sent | Enforced by |
|---|---|---|---|
| Extraction, photo | Sanitized JPEG (re-encoded from pixels, no EXIF/XMP/IPTC/ICC, long edge ≤ 2,048 px) and fixed instructions | Original upload, GPS, device, capture time, file name, owner identity, past sales | `app/privacy.py` `sanitize_image`; `tests/test_privacy.py`, `tests/test_ingestion.py` |
| Extraction, text | Text after phone numbers and emails are replaced with `[removed]` (M3 9.5 pattern) | The unmasked text | `app/privacy.py` `mask_text`; `tests/test_masking.py` (50-case NFR-717 set) |
| Guidance wording | Item names and calculated figures only: suggested quantity, averages, trend, change %, window size | Prices, dates, raw rows, images, text, owner identity | `app/llm.py` `ItemEvidence`; `tests/test_grounding.py`, `tests/test_llm_provider.py` (NFR-713) |

Names written inside a photo are visible to the provider, because a photo can only be masked
after the model has read it. The capture screen says so (FR-214, exact M3 9.7 text). Contact
details that the model transcribes are masked before they are stored or shown.

All provider traffic is HTTPS from the backend. `AI_API_KEY` and the storage secrets are backend
environment variables only; they are never sent to the browser and never committed. CI runs a
gitleaks scan of the full history (NFR-712).

## 2. Retention in Bodega (FR-215, NFR-715, M3 7.4)

| Data | Kept until | Mechanism |
|---|---|---|
| Draft (`draft_payload`, masked values and confidence bands) | Confirmation | Set to null in the confirming transaction |
| Sanitized image and masked text | 30 days after upload | `RetentionService` (`app/retention.py`): references cleared, image deleted from the bucket after commit, retried while storage is down |
| Open submissions (NEEDS_VERIFICATION, AI_UNAVAILABLE, REJECTED) | 30 days, or when the owner discards them | Row and image deleted |
| Confirmed sales | Until the owner deletes the submission | `DELETE /api/v1/submissions/{id}`, typed-DELETE confirmation in History |
| Baseline snapshots | 30 days after their analysis date, or any deletion | Deleted by the sweep or with the submission |
| Application logs | Render plan log retention, at most 30 days | JSON lines on stdout. Allow-listed fields only: no inputs, prompts, model output or access code (NFR-723) |

The sweep runs at startup and every 6 hours. Tests: `tests/test_final_contract.py` (purge,
storage outage, stale sweep, deletion).

**Difference from the SRS:** FR-215 and NFR-715 say 24 hours after confirmation or discard.
Milestone 3 section 3 clarified this to 30 days after upload. The code follows Milestone 3, and
the SRS text should be updated to match.

## 3. Provider terms (NFR-716) — NOT YET RECORDED

NFR-716 allows only a provider whose published terms say that submitted data is not used for
training and is kept no more than 30 days. Those terms depend on the agreement on the account
that issues `AI_API_KEY`. Nobody has checked that account yet, and this repository does not state
the terms from memory.

Before launch, the account owner must fill in this section:

- [ ] Provider account and agreement in force (for example the Anthropic Commercial Terms):
- [ ] Training on API inputs/outputs (quote and link):
- [ ] Retention period for API inputs/outputs (quote and link):
- [ ] Zero-data-retention or other addendum, if any:
- [ ] Date checked, and who checked it:

Until this is done, NFR-716 is **unverified**, and production should keep `AI_PROVIDER` unset or
on `mock`. In that case extraction falls back to manual entry and guidance uses Standard advice.

## 4. Known limits

- Removing metadata does not remove personal information visible in the photo itself.
- The phone pattern is the precise M3 9.5 one, by team decision (M3 section 10). A number written
  only with spaces and no country code (`809 555 1234`) is not masked. The generic SRS "7+ digits"
  rule was rejected because it erased ordinary sales lines.
- Confidence scores are the model's own estimates. They decide what the owner must check; they
  never let a value skip confirmation.
