-- Final Milestone 3 data design (sections 5.6, 7.1-7.4). Forward-only and additive on top of
-- 001-004; 004_capture_inputs.sql (José) already added submissions.input_type/masked_text, so this
-- migration only tightens them. It replaces Gabriel's unmerged 004_baseline_v1..007 branch
-- migrations (see docs/INTEGRATION_DECISIONS.md); no applied migration is edited or renamed.
--
-- Limits that could reject rows written before the final baseline are added NOT VALID:
-- PostgreSQL enforces them on every new or updated row without blocking an upgrade of a
-- database that already holds older values. Nothing is rounded, truncated or deleted.
SET CONSTRAINTS ALL IMMEDIATE;

-- OWNERS: seeded identity (section 7.1).
ALTER TABLE owners ADD COLUMN IF NOT EXISTS display_name varchar(100) NOT NULL DEFAULT 'Owner';
ALTER TABLE owners ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

-- SUBMISSIONS: inputType is required; lifecycle timestamps for the stale sweep and purge.
UPDATE submissions SET input_type = CASE WHEN image_key IS NOT NULL THEN 'IMAGE' ELSE 'TEXT' END
    WHERE input_type IS NULL;
ALTER TABLE submissions ALTER COLUMN input_type SET DEFAULT 'TEXT';
ALTER TABLE submissions ALTER COLUMN input_type SET NOT NULL;
ALTER TABLE submissions ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE submissions ADD COLUMN confirmed_at timestamptz;
UPDATE submissions SET updated_at = created_at,
    confirmed_at = CASE WHEN status = 'CONFIRMED' THEN created_at END;
ALTER TABLE submissions ADD CONSTRAINT submissions_confirmed_at
    CHECK (status <> 'CONFIRMED' OR confirmed_at IS NOT NULL);
ALTER TABLE submissions ADD CONSTRAINT submissions_confirmed_has_no_draft
    CHECK (status <> 'CONFIRMED' OR draft_payload IS NULL);
CREATE INDEX submissions_stale ON submissions(status, updated_at);
CREATE INDEX submissions_retention ON submissions(created_at);

-- SALES_RECORDS: FR-202 as clarified in section 3 (quantity > 0, at most 2 decimals and
-- 99,999.99), item names of 1-100 characters (7.2), 1-100 rows per submission.
ALTER TABLE sales_records ADD CONSTRAINT sales_final_limits CHECK (
    char_length(btrim(item_name)) BETWEEN 1 AND 100
    AND quantity <= 99999.99 AND quantity = round(quantity, 2)
    AND unit_price <= 9999999.99 AND unit_price = round(unit_price, 2)) NOT VALID;
ALTER TABLE sales_records ADD CONSTRAINT sales_line_number_range
    CHECK (line_number BETWEEN 0 AND 99) NOT VALID;

-- BASELINE_ESTIMATES: one snapshot per owner, asOfDate and item (7.1, 7.2).
ALTER TABLE baseline_estimates ADD COLUMN as_of_date date;
ALTER TABLE baseline_estimates ADD COLUMN item_key text;
ALTER TABLE baseline_estimates ADD COLUMN prior_average_daily_quantity numeric
    CHECK (prior_average_daily_quantity IS NULL OR
           (prior_average_daily_quantity >= 0 AND prior_average_daily_quantity < 'Infinity'::numeric));
ALTER TABLE baseline_estimates ADD COLUMN cover_days integer NOT NULL DEFAULT 7 CHECK (cover_days = 7);
UPDATE baseline_estimates SET item_key = lower(btrim(item_name)) WHERE item_key IS NULL;
-- Legacy snapshots have no analysis date; new writes must supply both keys.
ALTER TABLE baseline_estimates ADD CONSTRAINT baseline_snapshot_keys
    CHECK (as_of_date IS NOT NULL AND item_key IS NOT NULL AND char_length(btrim(item_name)) <= 100) NOT VALID;
CREATE UNIQUE INDEX baselines_owner_date_item ON baseline_estimates(owner_id, as_of_date, item_key);
CREATE INDEX baselines_as_of ON baseline_estimates(as_of_date);

-- Lifecycle (Diagram 4). Open states (NEEDS_VERIFICATION, AI_UNAVAILABLE, REJECTED) all accept
-- the same confirm; UPLOADED/CLASSIFYING go stale to AI_UNAVAILABLE; standalone manual entry is
-- inserted directly as CONFIRMED. The REJECTED/AI_UNAVAILABLE -> NEEDS_VERIFICATION
-- "manual review" transition from 001 is not part of the final diagram and is removed.
-- The database permits one day of clock skew: the session runs in UTC while the business date
-- uses APP_TIMEZONE, which the API enforces exactly (app/clock.py).
CREATE OR REPLACE FUNCTION enforce_submission_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT (NEW.status = 'UPLOADED' OR (NEW.status = 'CONFIRMED' AND NEW.input_type = 'MANUAL')) THEN
            RAISE EXCEPTION 'Submissions start UPLOADED, or CONFIRMED for standalone manual entry'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.sale_date > CURRENT_DATE + 1 THEN
            RAISE EXCEPTION 'Sale date cannot be in the future' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.owner_id <> OLD.owner_id OR NEW.sale_date <> OLD.sale_date
           OR NEW.input_type IS DISTINCT FROM OLD.input_type THEN
            RAISE EXCEPTION 'Submission owner, date and input type are immutable' USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
            (OLD.status = 'UPLOADED' AND NEW.status IN ('CLASSIFYING', 'AI_UNAVAILABLE')) OR
            (OLD.status = 'CLASSIFYING' AND NEW.status IN ('NEEDS_VERIFICATION', 'REJECTED', 'AI_UNAVAILABLE')) OR
            (OLD.status IN ('NEEDS_VERIFICATION', 'AI_UNAVAILABLE', 'REJECTED') AND NEW.status = 'CONFIRMED')) THEN
            RAISE EXCEPTION 'Invalid submission transition: % -> %', OLD.status, NEW.status
                USING ERRCODE = '23514';
        END IF;
        NEW.updated_at := now();
    END IF;
    IF NEW.status = 'CONFIRMED' THEN
        NEW.confirmed_at := COALESCE(NEW.confirmed_at, now());
    END IF;
    RETURN NEW;
END $$;

-- A CONFIRMED submission has 1-100 rows; every other status has none (7.1).
CREATE OR REPLACE FUNCTION enforce_confirmed_records() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; state text; row_count bigint;
BEGIN
    IF TG_TABLE_NAME = 'submissions' THEN
        target := COALESCE(NEW.id, OLD.id);
    ELSE
        target := COALESCE(NEW.submission_id, OLD.submission_id);
    END IF;
    SELECT status INTO state FROM submissions WHERE id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO row_count FROM sales_records WHERE submission_id = target;
    IF (state = 'CONFIRMED' AND row_count NOT BETWEEN 1 AND 100) OR (state <> 'CONFIRMED' AND row_count <> 0) THEN
        RAISE EXCEPTION 'Confirmed submissions require 1 to 100 rows; other states require none'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;

-- Outbox for sanitized-image deletion (7.4). Rows are written in the same transaction that
-- removes the image reference; the object is deleted only after commit, and a failed delete is
-- retried by the retention sweep. No foreign key: the job must outlive its submission.
CREATE TABLE image_deletion_jobs (
    image_key text PRIMARY KEY CHECK (char_length(btrim(image_key)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_attempt_at timestamptz
);

SET CONSTRAINTS ALL DEFERRED;
