# Diushen Cabrera: analysis, guidance and results dashboard

This report covers `GET /api/v1/analysis`, `POST /api/v1/guidance`, their automated tests and the React Results Dashboard. It records the assumptions the team needs to reconcile, the contracts other work depends on, and what was and was not verified.

Branch: `feature/diushen-analysis-guidance-dashboard`, based on `main` at `94d304f`. PR #2 was open and unmerged when this was written. Its changes are not included, but this work was tested on top of it (section 10).

## 1. Analysis policy and its difference from Milestone 2

This work implements the **seven-logged-date moving-average design from Milestone 3**, as the assignment requires. Milestone 2's earlier baseline used all history and a two-date comparison. That older formula is not implemented, so **the team should reconcile this in the requirements documents**. The milestone DOCX files are not in the repository. The policy below comes from the assignment brief, not from re-reading those files.

| Rule | Decision |
| --- | --- |
| Logged business date | A date with at least one confirmed sales record for the configured owner. |
| Seven-day window | Seven logged dates, not necessarily consecutive calendar days. |
| History period | Inclusive 28 calendar days: `asOf - 27 days` through `asOf`. Older sales are ignored even if that leaves too few dates. |
| Item matching | Trim surrounding whitespace and ignore case (`str.strip().casefold()`). Synonyms are not merged. The label shown is the most recent spelling. |
| Same-date rows | Multiple rows and submissions on a date are summed first. |
| Item missing on a logged date | Counts as zero, and is marked `observed: false`. |
| Date nobody logged | Unknown. It is never inserted as a zero-sales date. |
| Current estimate | Requires 7 logged dates: the mean over the latest 7. |
| Trend | Requires 14 logged dates: latest 7 against the preceding 7. |
| 7 to 13 logged dates | Estimate shown, trend `INSUFFICIENT_DATA`. `STABLE` is never fabricated. |
| Trend thresholds | `UP` above +10%, `DOWN` below -10%, otherwise `STABLE`. Exactly +10% and -10% are `STABLE`. |
| Prior average of zero | Current above zero is `UP` with `changePercent: null`. Both zero is `STABLE`. |
| Planning quantity | `ceil(current average × 7)`. Sales-based planning guidance: it does not establish actual stock or forecast a calendar week. |

Formulas, with `D` the sorted logged dates and `q(i, d)` the item's summed quantity on date `d` (zero if absent):

```text
current average  = sum(q(i, d) for the last 7 dates in D) / 7
previous average = sum(q(i, d) for the 7 dates before those) / 7
suggestedQty     = ceil(current average × 7)
UP      when current × 10 > previous × 11
DOWN    when current × 10 < previous × 9
```

**Worked example.** 14 consecutive logged dates; bread sells 2 on each of the first seven and 4 on each of the latest seven. Prior average 2, current average 4, trend `UP`, suggested quantity 28.

**Default date.** Without `asOf`, the analysis uses "today" from an injectable clock (`app/clock.py`) in `APP_TIMEZONE`, defaulting to UTC when unset. A malformed date or a date in the future returns 400 from both endpoints (M3 4.2). An invalid timezone setting returns 503.

## 2. How exactness is preserved

Gabriel's API accepts values such as `123456789012345678901.1234567` and `1e-400`. Three common approaches would corrupt those silently:

- `float` loses digits and turns `10/7*7` into `10.000000000000002`, which would wrongly round up to 11.
- `Decimal` rounds to 28 significant digits by default, and `10/7` has no exact decimal form anyway.
- `DataFrame.rolling()` converts to float.

So `BaselineAnalyzer` converts each `Decimal` to `fractions.Fraction` and holds them in pandas object-dtype columns. There, `groupby().sum()`, `unstack()`, `cumsum()` and `shift()` call Python's exact operators. The moving sum is `running - running.shift(7)`.

Values leave the backend as JSON strings. A repeating average such as 10/7 is rounded for display only and flagged (`currentAverage: "1.428571"`, `currentAverageRounded: true`). `currentTotal` and `suggestedQty` are always exact.

## 3. API contract

Both routes keep their paths, `require_owner_access` and the configured `OWNER_ID`. Authentication is checked before input validation. Database errors use the existing safe 500 handler.

### GET /api/v1/analysis?asOf=YYYY-MM-DD

`status` is one of `NO_DATA` (0 logged dates), `INSUFFICIENT_HISTORY` (1-6), `ESTIMATE_ONLY` (7-13) or `READY` (14+). This adds fields to the earlier placeholder and keeps its names. One change: zero logged dates now reports `NO_DATA`, not `INSUFFICIENT_HISTORY`.

```json
{
  "asOf": "2026-04-30",
  "historyStart": "2026-04-03",
  "historyEnd": "2026-04-30",
  "loggedDays": 14,
  "requiredDays": 7,
  "trendRequiredDays": 14,
  "status": "READY",
  "statusMessage": "Estimates and trends use the latest 14 of 14 logged sales dates.",
  "quantityBasis": "Suggested quantities are sales-based planning guidance: ...",
  "loggedDates": ["2026-04-17", "..."],
  "currentWindow": { "start": "2026-04-24", "end": "2026-04-30", "loggedDays": 7 },
  "previousWindow": { "start": "2026-04-17", "end": "2026-04-23", "loggedDays": 7 },
  "estimates": [{
    "itemKey": "bread",
    "itemName": "Bread",
    "windowLoggedDays": 7,
    "currentAverage": "4",
    "currentAverageRounded": false,
    "currentTotal": "28",
    "currentDaysWithSales": 7,
    "previousAverage": "2",
    "previousAverageRounded": false,
    "previousTotal": "14",
    "previousDaysWithSales": 7,
    "trend": "UP",
    "changePercent": "100",
    "suggestedQty": "28",
    "series": [{
      "date": "2026-04-30",
      "quantity": "4",
      "observed": true,
      "movingAverage": "4",
      "movingAverageRounded": false,
      "calendarDaysSkipped": 0
    }]
  }]
}
```

Below seven logged dates, each item keeps its confirmed `series`, while its averages and `suggestedQty` are `null`. Real sales are shown; nothing is estimated.

Error shape, matching the one PR #2 uses:

```json
{ "detail": [{ "loc": ["query", "asOf"], "msg": "asOf cannot be after the current business date (2026-04-30)", "type": "value_error" }] }
```

### POST /api/v1/guidance

Request: `{ "asOf": "2026-04-30" }`, or `{}` for today. Any other client field is ignored, so a client cannot supply quantities or select a mock scenario.

```json
{
  "asOf": "2026-04-30",
  "status": "READY",
  "items": [{
    "itemName": "Bread",
    "suggestedQty": "28",
    "currentAverage": "4",
    "previousAverage": "2",
    "trend": "UP",
    "message": "Bread: plan 28 units to cover 7 logged sales dates. Average is 4 per logged date. Sales trend is up."
  }],
  "source": "AI_WORDED",
  "groundingPassed": true,
  "failedRule": null,
  "failureReason": null,
  "providerCalled": true,
  "provider": "mock",
  "stockNotice": "These are sales-based planning suggestions, not low-stock alerts. Bodega does not record current inventory, so check your actual stock before ordering."
}
```

Fallback after rejected wording: the same shape with `"source": "TEMPLATE"`, `"groundingPassed": false`, `"failedRule": "UNSUPPORTED_NUMBER"`, a fixed `failureReason`, and messages like `Bread: plan for 28 units to cover 7 logged sales dates. Average is 4 per logged date. Sales are trending up compared with the previous 7 logged dates.`

`groundingPassed` always describes the provider's own output. A good fallback never turns it true.

With no eligible estimate: `status: "INSUFFICIENT_HISTORY"`, `items: []`, `providerCalled: false`, `failedRule: "INSUFFICIENT_HISTORY"`. `source` stays `TEMPLATE` because the field has only two values; no wording was generated.

The exact Milestone 3 template wording was not available in the repository. The team should compare the text in `template_message` with the document.

## 4. Guidance and grounding

`GuidanceEngine` obtains analysis on the server, finishes the database work, then makes **one provider attempt with a 15-second limit and no retry**. The call runs on a daemon thread, so the limit holds even if provider code ignores cancellation. A late result is discarded.

The provider receives calculated evidence only: item name, suggested quantity, averages, trend, percentage and window size. It gets no prices, dates or raw rows. Logs contain only the rule code and provider name.

`check_grounding` rejects the whole response if any entry fails:

| Rule | Rejects |
| --- | --- |
| `EMPTY_RESPONSE`, `MALFORMED_JSON`, `INVALID_SCHEMA` | Unusable output, code fences, extra fields, wrong types. |
| `UNKNOWN_ITEM`, `DUPLICATE_ITEM`, `MISSING_ITEM`, `ITEM_NOT_NAMED` | Fabricated, repeated or omitted products, or a message that does not name its product. |
| `QUANTITY_MISMATCH` | A changed `suggestedQty`, or a message that does not state it. |
| `UNSUPPORTED_NUMBER` | Any number outside the item's evidence, or an allowed number in the wrong role (for example the average followed by "units"). |
| `TREND_MISMATCH` | Direction words that contradict the trend, or any direction word when there is no trend. |
| `MESSAGE_TOO_LONG` | More than 240 characters, excluding the unrestricted item name. |
| `UNSAFE_MARKUP` | HTML or Markdown characters. |
| `UNSUPPORTED_CLAIM` | Any word outside a closed vocabulary, or "stock" used other than as advice to check it. |

**The closed vocabulary is the main defence.** Apart from the item's own name, a message may use only 103 planning and trend words. Prices, profit, weather, competitors and guarantees fail because those words are unavailable, not because a blocklist anticipated them. The list also leaves out negation ("not"), certainty ("will") and stock states ("low", "out"). This trades flexibility for safety: unusual but truthful wording falls back to the template, which is the safe direction.

**Residual limitations.** This reduces risk but does not prove that natural language is true:

- Allowed words can still be arranged misleadingly, and an allowed number can appear in a role the context rules do not catch.
- Removing the item name before checking means a label containing a trend word (an item called "Up") could hide that word.
- A real provider's output quality, refusal rate and prompt-injection behaviour are all untested. Item labels are owner-entered and passed as data, but no prompt defence is proven.

**Mock limitation.** `AI_PROVIDER=mock` runs with no paid key, and `MockLLMService` returns fixed sentences. This demonstrates the integration and nothing about real-provider quality. The dashboard labels it as the mock. No production provider has been chosen. Any other `AI_PROVIDER` value gives `PROVIDER_NOT_CONFIGURED` and the template. A real adapter implements `generate(WordingRequest) -> str` and must set its own client timeouts, because a timed-out thread can only be abandoned, not killed.

## 5. Results Dashboard

`ResultsDashboard.jsx` is mounted between DraftReview and SalesHistory, and uses App's existing `request` function and `revision` counter.

- **Exact values:** displayed numbers are the API's strings and are never passed through `Number`. `chartValues.js` converts only for chart positions. A value that overflows or underflows replaces the chart with a notice, and the exact table remains.
- **Chart:** a small hand-written SVG, so no dependency was added. Bars show confirmed sales and the line shows the seven-date average on a single axis. Logged dates are evenly spaced, tick marks show skipped calendar dates, and hollow circles show an item absent on a logged date. Each chart has an exact-values table.
- **Trends:** text and a glyph (▲ ▼ ▬ ?), never color alone.
- **Stale responses:** each load has a `selection` key made from revision, date and refresh count. Older analysis responses are ignored. Guidance belongs to the selection that requested it, so it disappears when the analysis changes, and a late response cannot display.
- **Stock wording:** guidance is presented as planning suggestions with a stock-check notice, and is explicitly not a low-stock alert.

The app has no dark mode, so the chart does not add one. Bar and line colors pass a contrast and color-blindness validator (`#3a9a78`, `#bb6c1d`).

## 6. Data access and snapshots

`repository.get_confirmed_sales_between` is a new, unpaginated, owner-scoped, confirmed-only, parameterized read. `get_confirmed_records` and its 100-row paging are unchanged.

**`baseline_estimates` is neither read nor written.** Results come fresh from confirmed records, so a snapshot cannot override them, and a confirmation shows up on the next request. This also avoids snapshot duplication, refresh and `asOf` questions, and the column's PostgreSQL `integer` limit on `suggested_restock_qty`, which an exact quantity can exceed. If the team later wants snapshots, that limit needs a decision first.

## 7. Files

| Area | Files |
| --- | --- |
| New backend | `app/analysis.py`, `app/clock.py`, `app/grounding.py`, `app/guidance.py`, `app/llm.py`, `app/seed_analysis.py` |
| Shared backend (additive) | `app/main.py` (imports, `GuidanceRequest.asOf` type, the two placeholder routes), `app/repository.py` (one new function at the end), `requirements.txt` (`pandas`, `tzdata`) |
| New frontend | `components/ResultsDashboard.jsx`, `ResultsDashboard.css`, `SalesTrendChart.jsx`, `chartValues.js`, `ResultsDashboard.test.jsx` |
| Shared frontend (additive) | `App.jsx` (import and one mount line) |
| New tests | `tests/conftest.py`, `test_analysis_math.py`, `test_grounding.py`, `test_insights_api.py`, `test_vision_output_to_guidance.py`, `mock_vision.py` |
| Documentation | this report, `README.md` |

`tests/conftest.py` deliberately duplicates the `db` and `client` fixtures. `test_database.py` keeps its own, which take precedence there, so Gabriel's file and PR #2 are untouched. Gabriel's copies can be removed once PR #2 lands. No migration, Compose file, Gabriel component or Gabriel test was changed.

## 8. Mocks and the vision boundary

No extraction adapter exists on `main` or in PR #2. `tests/mock_vision.py` therefore holds a **test-only** `MockVisionModel`, and `test_vision_output_to_guidance.py` starts at the **vision-output persistence boundary**, not at image upload:

```text
MockVisionModel draft -> repository.create_submission / update_status (valid lifecycle)
  -> GET submission -> POST /confirm with draftLineId and verifiedFields -> /analysis -> /guidance
```

It checks stable line IDs, that unverified uncertain fields are refused, `AI_EXTRACTED` provenance, idempotent re-confirmation and the final worked-example figures. It does not touch José's production route. Once his adapter exists, this test should inject the mock through the real interface.

`MockLLMService` scenarios (`valid`, `unknown-item`, `changed-quantity`, `unsupported-number`, `wrong-trend`, `malformed`, `empty`, `failure`, `timeout`) are constructor arguments used by test injection. No API parameter or environment variable can select them.

## 9. Setup

```sh
docker compose up --build -d --wait
# Optional: synthetic history (Bread UP, Milk STABLE, Eggs DOWN). Never runs automatically.
docker compose exec backend python -m app.seed_analysis
```

Open http://localhost:5173, connect with `local-development-code-123`, and use the Results dashboard section. `pandas` requires Python 3.11+, which the Python 3.12 Dockerfile satisfies. Tests run with the commands in the README. `test_analysis_math.py` and `test_grounding.py` need no database.

## 10. Verification

These commands ran on Windows on 2026-11-04 with Python 3.12, pandas 3.1.1, Node 24 and PostgreSQL 16.2. **Docker was not available on that machine**, so PostgreSQL ran natively from the `pgserver` package in a temporary directory.

| Check | Result |
| --- | --- |
| `python -m pytest -q` with `TEST_DATABASE_URL` | **209 passed**: 58 Gabriel regressions and 151 new (24 math, 68 grounding, 57 API, 2 vision boundary). 1 existing Starlette deprecation warning. |
| Same suite without a database, math and grounding files only | 92 passed |
| Baseline before any change | 58 backend and 20 frontend passed |
| `npm test` | **46 passed**: 20 existing and 26 new |
| `npm run lint`, `npm run build` | Passed |
| `tests/smoke_http.py` through a Vite proxy to uvicorn | Passed |
| Changes applied over PR #2 at `36bbbe3` (temporary worktree) | Applied without conflicts. 211 backend and 47 frontend passed. |
| Live browser run in Chrome, desktop and phone width | 0 console errors, 0 horizontal overflow. Confirming a sale updated the dashboard from 28 to 35 and cleared old guidance. A future date showed its error. |
| Test schema cleanup | 0 left behind |

**Not run:** `docker compose` commands, including `compose.test.yml`, the Linux image build and the `requirements.txt` install inside the image. Please run them on a Docker machine before merging. A real LLM provider was not exercised. The 15-second limit was tested with a shortened timeout and an assertion on the default value, not by waiting 15 seconds.

## 11. Remaining team work

- **Team:** reconcile section 1 against Milestones 2 and 3, compare the template wording with Milestone 3, and agree the response shapes in section 3.
- **Gabriel:** PR #2 is compatible as tested. `POST /confirm` and `POST /extract` still use `date.today()` and the database trigger uses `CURRENT_DATE`, while analysis uses `APP_TIMEZONE`. In a UTC container, a sale confirmed during the local evening can carry a date that analysis treats as tomorrow, so it stays out of the default view until local midnight. A shared business-date policy would close this.
- **José:** implement the extraction adapter and replace the test-only `MockVisionModel` wiring. Reuse `WordingProvider` if a shared provider abstraction appears.
- **Luis:** when the shared shell or client arrives, keep passing `request` and `revision` to the dashboard. Choose the production provider and configuration with the team. Add `compose.test.yml` coverage for the new checks if wanted; it needs no new service.
- **Not implemented:** real AI provider, snapshot persistence, and any stock or inventory tracking.

## 12. Integration update (`integration/m3-completion`)

- **M3 9.6/9.7 wording.** The exact template and trend phrases, the "Standard advice" label (`sourceLabel`) and the exact stock-check notice. The dashboard uses the M3 labels and shows the average to one decimal (`currentAverageDisplay`); exact values remain.
- **Grounding** also applies the M3 rules: 1-decimal average, 40 words, the listed trend words. A test proves the template passes the check by construction.
- **`ClaudeLLMService`** is implemented: strict tool, `tool_choice` auto, no retries, 15 s timeout. It is tested only with a fake client; real wording is unverified.
- **Snapshots.** `/analysis` and `/guidance` save SD-03 snapshots and never read them back (D-10).
- **Insufficient history** in guidance is now 409 INSUFFICIENT_HISTORY (M3 4.2).
- **Exactness tests** use the final limits (99,999.99); arbitrary-precision math stays covered in `test_analysis_math.py`.
- **NFR-703 measured:** p95 0.159 s at 5,000 records (`test_performance.py`).

## 13. Milestone 4 review update (2026-11-10)

Reviewed against `WorkDivision.docx` and the final Milestone 3; the full mapping is in `docs/TRACEABILITY.md`.

- **Your analysis, guidance and dashboard are unchanged from this report.** For one day the working tree used the Milestone 2 all-history formula without trends (D-26); that was withdrawn because WorkDivision assigns 7-day moving averages, trend classification and trend directions on the dashboard. The files are identical to the committed versions except for the points below.
- **Changes in your files, for your review:** an invalid `asOf` answers 400 directly (M3 4.2). `ClaudeLLMService` requires exactly one schema-valid `write_guidance` call, uses `max_tokens` 1024 and a 15-second per-call timeout (D-35). New regression tests in `tests/test_integration_regressions.py` pin the 7-logged-day average, the trend, the suggested stock, the snapshot shape and the 409.
- **Known limit:** a suggested stock above 2,147,483,647 cannot be saved in the snapshot column; the confirmed-row limits make that unreachable.
- Real Claude wording still needs an authorized `AI_API_KEY`.
