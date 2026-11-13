# Manual test checklist

Seven end-to-end scenarios that a person can run in a browser to check Bodega's main owner workflows. The automated suites are described in the [README](../README.md#running-the-tests). This checklist covers what a user actually sees.

## Setup

1. Start the app: `docker compose up --build -d --wait` (see [Start locally](../README.md#start-locally)).
2. Open http://localhost:5173. The access code is `local-development-code-123` (the value in `docker-compose.yml`).
3. `AI_PROVIDER` defaults to `mock`, so no API key is needed. The mock reads typed lines such as `Bread 3 1.50` exactly. It cannot read photos: for any photo it returns two labelled sample rows.
4. Use only synthetic data. **MT-07 deletes all of the owner's sales data.**

**Run MT-06 before MT-02 to MT-05.** MT-06's expected figures assume the database holds only the seeded history. Sales captured today add a new logged date and change the averages.

## Summary

| ID | Scenario | Result (both recorded runs) |
| --- | --- | --- |
| [MT-01](#mt-01-sign-in-and-reject-an-invalid-access-code) | Sign in, reject an invalid access code, sign out | Pass |
| [MT-02](#mt-02-capture-sales-from-text) | Capture sales from text | Pass |
| [MT-03](#mt-03-capture-sales-from-a-photo) | Capture sales from a photo, discard a draft | Pass |
| [MT-04](#mt-04-review-correct-and-confirm-a-draft) | Review and correct a draft, confirm it, see it in History | Pass |
| [MT-05](#mt-05-enter-sales-manually-including-during-an-ai-outage) | Enter sales manually, including during an AI outage | Pass |
| [MT-06](#mt-06-results-dashboard-and-restocking-advice) | Results dashboard and restocking advice | Pass |
| [MT-07](#mt-07-delete-all-my-sales-data) | Delete all my sales data | Pass |

**About the recorded runs.** Both used branch `chore/m4-submission-requirements`, based on `main` at `749fc71`, in Chrome at 1280×900. Each step was driven with Playwright, and each expected result was asserted, not just observed.
- **2026-11-13, Docker Compose stack:** the images and settings from `docker-compose.yml`, with real PostgreSQL and MinIO, `AI_PROVIDER=mock` and access code `local-development-code-123`. MT-05 Part B restarted the backend with `AI_PROVIDER=none`, as described there. One difference: Docker Desktop on the test PC could not read the `D:` drive the repository is on, so the source bind mounts were removed and the code baked into the images ran instead.
- **2026-11-12, native stack:** Docker Desktop did not start that day. The app ran natively (uvicorn, the Vite dev server, PostgreSQL 16) with the `moto` S3 emulator in place of MinIO, using the same access code, `AI_PROVIDER=mock` and `APP_TIMEZONE=America/Santo_Domingo`. MT-05 Part B used a second backend with no AI provider.
- **Not covered:** real Claude extraction and wording (`AI_PROVIDER=claude` with a key). Mock output shows that the workflow works, not how well the AI reads.

The "Actual result" sections quote the 2026-11-12 run. The 2026-11-13 Docker run showed the same results, with that day's dates.

To re-run, add a row to the run log at the end of this file.

---

## MT-01: Sign in and reject an invalid access code

**Purpose:** Only the owner's access code opens the app. A wrong code is refused without revealing anything.

**Preconditions:** The app is running and you are signed out.

**Steps**
1. Go to http://localhost:5173/history.
2. Enter `wrong-code` as the **Access code** and select **Sign in**.
3. Enter `local-development-code-123` and select **Sign in**.
4. Select **Sign out** in the header.

**Expected result**
- Step 1 redirects to `/login`.
- Step 2 shows "That access code is not correct." and stays on `/login`.
- Step 3 opens `/history`, the page you first asked for. The header shows **Capture**, **Pending drafts**, **History**, **Analysis** and **Sign out**.
- Step 4 returns to `/login`.

**Actual result:** As expected. `/history` redirected to `/login`; the wrong code showed "That access code is not correct."; the correct code returned to `/history` with all four navigation links; **Sign out** returned to `/login`.

**Pass/Fail:** Pass

---

## MT-02: Capture sales from text

**Purpose:** Typed or pasted sales become a reviewable draft, and contact details are hidden before the text is sent to the AI.

**Preconditions:** Signed in.

**Steps**
1. On **Capture**, choose the **Text** tab and keep today's **Sale date**.
2. In **Sales text**, enter these three lines:
   ```text
   Bread 3 1.50
   Milk 2 2.25
   Call 809-555-0142 for orders
   ```
3. Select **Read text**.

**Expected result**
- The app opens **Review draft** (`/review/<submission id>`) with rows for Bread and Milk.
- The **Your text** panel says "Phone numbers and emails were removed." and shows the phone number as `[removed]`.
- Nothing is in History yet.

**Actual result:** As expected. The draft had rows `Bread` and `Milk`. The text panel showed `Call [removed] for orders`, and the phone number did not appear anywhere on the page.

**Pass/Fail:** Pass

---

## MT-03: Capture sales from a photo

**Purpose:** An uploaded JPEG or PNG is stored and turned into a draft that must be reviewed before it can be confirmed. A draft can be discarded.

**Preconditions:** Signed in. Have any JPEG or PNG under 10 MiB.

**Steps**
1. On **Capture**, choose the **Photo** tab, keep today's date and choose the image in **Photo of your sales page**.
2. Select **Read photo**.
3. Look at the review page without changing anything.
4. Open **Pending drafts** and select **Discard** on the photo draft.

**Expected result**
- A **Review draft** page opens. With the mock provider it shows rows "Sample bread (mock)" and "Sample milk (mock)".
- **Your photo** shows the stored image.
- An uncertain value has a **Please verify** checkbox, and an unreadable one says "Could not read — enter value". **Confirm sales** stays disabled until they are handled.
- **Pending drafts** lists the draft. **Discard** removes it and reports "Discarded the draft from <date>."

**Actual result:** As expected. Both sample rows appeared; there was one **Please verify** checkbox and one "Could not read — enter value" field. The stored photo loaded, and **Confirm sales** was disabled. **Pending drafts** listed two drafts (this one and MT-02's); **Discard** left one and showed "Discarded the draft from 2026-11-12."

**Pass/Fail:** Pass

---

## MT-04: Review, correct and confirm a draft

**Purpose:** The owner corrects a draft, confirms it, and the confirmed sales appear in History with their source.

**Preconditions:** The MT-02 text draft is still pending.

**Steps**
1. Open the MT-02 draft from **Pending drafts** (**Open**).
2. Change **Row 1 Quantity** (Bread) from 3 to `4`. Tick any **Please verify** boxes that are shown.
3. Select **Confirm sales**.
4. Select **View history**.

**Expected result**
- The page reports "Sales saved. 2 records created."
- **Confirmed sales history** shows Bread 4 × 1.50 = 6.00 and Milk 2 × 2.25 = 4.50 for today, each with the source "Read by AI, checked by you".
- The draft no longer appears under **Pending drafts**.

**Actual result:** As expected. Confirmation reported "Sales saved. 2 records created." History showed `Bread 4 1.50 6.00 Read by AI, checked by you` and `Milk 2 2.25 4.50 Read by AI, checked by you`. MT-07 later found no pending drafts left.

**Pass/Fail:** Pass

---

## MT-05: Enter sales manually, including during an AI outage

**Purpose:** Sales can always be recorded without the AI: by choice, and when the AI cannot read a capture.

**Preconditions:** Signed in.
- **Part B needs a backend with no AI provider.** Any `AI_PROVIDER` value other than `mock` (or `claude` with a key) gives the no-AI path. Restart the backend with one, then switch back afterwards:
  - PowerShell: `$env:AI_PROVIDER='none'; docker compose up -d backend`
  - bash: `AI_PROVIDER=none docker compose up -d backend`
  - To switch back: `Remove-Item Env:AI_PROVIDER` (or `unset AI_PROVIDER`), then `docker compose up -d backend`.

**Steps**
- **Part A (manual entry):**
  1. On **Capture**, choose **Enter manually**.
  2. Enter Item `Coffee`, Quantity `1.5`, Unit price `4.00`.
  3. Select **Save sales**, then open **History**.
- **Part B (AI unavailable):**
  1. With no AI provider, use the **Text** tab with `Tea 2 1.00` and select **Read text**.
  2. On the review page, select **Add row**, enter Tea, 2, 1.00 and select **Confirm sales**.

**Expected result**
- **Part A:** "Sales saved. 1 record created." History shows `Coffee 1.5 4.00 6.00` with the source "Typed by you". Nothing is sent to the AI service, as the form states.
- **Part B:** the review page says "We could not read this right now. You can type the sales below." The typed row is saved and appears in History as "Typed by you".

**Actual result:**
- **Part A:** as expected. "Sales saved. 1 record created."; History showed `Coffee 1.5 4.00 6.00 Typed by you`.
- **Part B:** as expected. The outage message appeared, and the typed Tea row was saved and listed in History as "Typed by you".
- **Wording note:** Part B's confirmation read "Sales saved. 1 records created." The plural is wrong for one record; manual entry words it correctly. This is cosmetic and is noted for the draft review component's owner.

**Pass/Fail:** Pass

---

## MT-06: Results dashboard and restocking advice

**Purpose:** The dashboard shows per-item averages, trends and suggested stock from confirmed sales, and restocking advice that matches those figures.

**Preconditions:** Signed in, with only the synthetic history loaded:

```sh
docker compose exec backend python -m app.seed_analysis
```

This creates 14 logged days ending yesterday: Bread rises from 2 to 4 a day, Milk stays at 2.5 and Eggs fall from 3 to 1.

**Steps**
1. Open **Analysis**.
2. Read the Bread, Milk and Eggs cards.
3. Select **Get restocking advice**.

**Expected result**
- The page says "As of <today>" with a 28-day history range and 14 logged dates.
- **Bread:** ▲ UP, suggested stock 28, average 4.0, previous average 2.
- **Milk:** ▬ STABLE, 18, average 2.5.
- **Eggs:** ▼ DOWN, 7, average 1.0, previous average 3.
- Trends are shown as text with a symbol, not by color alone.
- The advice lists each item with the same quantities. It shows its source ("AI-worded by the mock provider (demo text, not a real AI model)", or **Standard advice** if the wording is rejected) and a **Check your stock** notice.

**Actual result:** As expected. "As of 2026-11-12. Sales history from 2026-10-16 to 2026-11-12: 14 logged dates."
- **Bread:** ▲ UP, 28, 4.0, previous 2, +100%.
- **Milk:** ▬ STABLE, 18, 2.5, 0%.
- **Eggs:** ▼ DOWN, 7, 1.0, previous 3, −66.67%.
- **Advice:** "Bread: plan 28 units…", "Milk: plan 18 units…", "Eggs: plan 7 units…", labelled as the mock provider, with the stock notice "These numbers come only from the sales you logged…".

**Pass/Fail:** Pass

---

## MT-07: Delete all my sales data

**Purpose:** The owner can delete all of their data, and only after explicitly typing DELETE.

**Preconditions:** Signed in, with confirmed sales and a pending draft present (for example after MT-02 to MT-06). **Local synthetic data only.**

**Steps**
1. Open **History** and select **Delete all data**.
2. Type `delete` (lower case) in **Type DELETE to confirm**, and look at the submit button.
3. Select **Cancel**.
4. Select **Delete all data** again, type `DELETE` and select **Delete all data**.
5. Check **History** (**Refresh history**), **Pending drafts** and **Analysis**.

**Expected result**
- With `delete` the submit button stays disabled.
- **Cancel** shows "Nothing was deleted."
- `DELETE` deletes everything and reports "<N> records deleted".
- **History** shows "No confirmed sales in these dates."
- **Pending drafts** shows "No pending drafts."
- **Analysis** shows "No confirmed sales exist in this 28-day period."

**Actual result:** As expected. The button was disabled for `delete`, and **Cancel** showed "Nothing was deleted." `DELETE` reported "45 records deleted": the 42 seeded records, MT-04's 2 and MT-05's 1. History, Pending drafts and Analysis were all empty afterwards.

**Pass/Fail:** Pass

---

## Run log

| Date | Commit | Environment | Tester | MT-01 | MT-02 | MT-03 | MT-04 | MT-05 | MT-06 | MT-07 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-12 | `749fc71` + this branch | Native stack, `moto` for S3, mock AI, Chrome (scripted) | Claude Code for Diushen Cabrera | Pass | Pass | Pass | Pass | Pass | Pass | Pass |
| 2026-11-13 | `f0f22c2` | Docker Compose stack: PostgreSQL, MinIO, mock AI (bind mounts removed), Chrome (scripted) | Claude Code for Diushen Cabrera | Pass | Pass | Pass | Pass | Pass | Pass | Pass |
