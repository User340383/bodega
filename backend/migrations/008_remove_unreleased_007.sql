-- 008: bring every database to the Milestone 3 schema of 006, whether or not it ran the
-- withdrawn draft of 007 (see 007_reserved.sql). Forward-only and idempotent: every statement is
-- a no-op on a database that never had the draft.
--
-- Nothing an owner entered is changed or lost: submissions, drafts, sales records and the
-- Milestone 3 baseline snapshots keep every value and ID. What goes:
--   * submissions.extraction_failed_at and submissions.retry_count (bookkeeping for the
--     withdrawn 24-hour deadline and retry);
--   * the AI_UNAVAILABLE -> CLASSIFYING transition (M3 Diagram 4 has none);
--   * baseline snapshots written in the withdrawn "SRS_RECORDED_DATES" shape. Snapshots are an
--     audit copy that is never read back and is rewritten by the next analysis (M3 7.1, D-10),
--     and those rows have none of the columns M3 7.1 requires.
SET CONSTRAINTS ALL IMMEDIATE;

-- SUBMISSIONS
ALTER TABLE submissions DROP CONSTRAINT IF EXISTS submissions_retry_count;
DROP INDEX IF EXISTS submissions_extraction_failed;
ALTER TABLE submissions DROP COLUMN IF EXISTS extraction_failed_at;
ALTER TABLE submissions DROP COLUMN IF EXISTS retry_count;

-- Lifecycle exactly as defined in 005 (M3 Diagram 4).
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

-- BASELINE_ESTIMATES
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()
               AND table_name = 'baseline_estimates' AND column_name = 'method') THEN
        EXECUTE 'DELETE FROM baseline_estimates WHERE method = ''SRS_RECORDED_DATES''';
    END IF;
END $$;
ALTER TABLE baseline_estimates DROP CONSTRAINT IF EXISTS baseline_m3_shape;
ALTER TABLE baseline_estimates DROP CONSTRAINT IF EXISTS baseline_srs_shape;
ALTER TABLE baseline_estimates DROP CONSTRAINT IF EXISTS baseline_method;
ALTER TABLE baseline_estimates DROP COLUMN IF EXISTS method;
ALTER TABLE baseline_estimates DROP COLUMN IF EXISTS total_quantity;
ALTER TABLE baseline_estimates DROP COLUMN IF EXISTS recorded_dates;
ALTER TABLE baseline_estimates DROP COLUMN IF EXISTS baseline_estimate;
ALTER TABLE baseline_estimates DROP COLUMN IF EXISTS first_sale_date;
ALTER TABLE baseline_estimates DROP COLUMN IF EXISTS last_sale_date;
-- A snapshot without the M3 7.1 figures cannot exist once the draft shape is gone.
DELETE FROM baseline_estimates WHERE window_days IS NULL OR average_daily_quantity IS NULL
    OR trend IS NULL OR suggested_restock_qty IS NULL;
ALTER TABLE baseline_estimates ALTER COLUMN window_days SET NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN average_daily_quantity SET NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN trend SET NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN suggested_restock_qty SET NOT NULL;

SET CONSTRAINTS ALL DEFERRED;
