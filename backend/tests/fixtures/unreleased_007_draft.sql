-- 007: baselined SRS rules that Milestone 3 changed without a recorded approval
-- (docs/INTEGRATION_DECISIONS.md D-26 to D-29). Forward-only and additive.
--
-- 1. Raw-input retention (SRS FR-211, FR-215, NFR-715): the input of a failed extraction is
--    kept for a later retry and deleted no later than 24 hours after the failure, so the time
--    of the failure is recorded. Confirmation and discard remove the input at once (app code).
-- 2. Retry (SRS FR-211): an AI_UNAVAILABLE submission may return to CLASSIFYING, reusing the
--    same row, input and sales date. retry_count records how often that happened.
-- 3. Baseline snapshots (M3 SD-03 saveBaselines) in the SRS 3.1 shape: total confirmed
--    quantity / distinct recorded sales dates over all history. The M3 seven-logged-day columns
--    stay for rows written before this migration and become optional.
SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE submissions ADD COLUMN extraction_failed_at timestamptz;
ALTER TABLE submissions ADD COLUMN retry_count integer NOT NULL DEFAULT 0;
ALTER TABLE submissions ADD CONSTRAINT submissions_retry_count CHECK (retry_count >= 0);
-- Existing failed submissions: count the deadline from their last state change.
UPDATE submissions SET extraction_failed_at = updated_at WHERE status IN ('AI_UNAVAILABLE', 'REJECTED');
CREATE INDEX submissions_extraction_failed ON submissions(extraction_failed_at);

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
            -- SRS FR-211 retry of the retained input (same submission and sales date).
            (OLD.status = 'AI_UNAVAILABLE' AND NEW.status = 'CLASSIFYING') OR
            (OLD.status IN ('NEEDS_VERIFICATION', 'AI_UNAVAILABLE', 'REJECTED') AND NEW.status = 'CONFIRMED')) THEN
            RAISE EXCEPTION 'Invalid submission transition: % -> %', OLD.status, NEW.status
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'CLASSIFYING' AND OLD.status = 'AI_UNAVAILABLE'
           AND NEW.image_key IS NULL AND NEW.masked_text IS NULL THEN
            RAISE EXCEPTION 'Nothing to retry: the input was deleted' USING ERRCODE = '23514';
        END IF;
        NEW.updated_at := now();
    END IF;
    IF NEW.status = 'CONFIRMED' THEN
        NEW.confirmed_at := COALESCE(NEW.confirmed_at, now());
    END IF;
    RETURN NEW;
END $$;

ALTER TABLE baseline_estimates ALTER COLUMN window_days DROP NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN average_daily_quantity DROP NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN trend DROP NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN suggested_restock_qty DROP NOT NULL;
ALTER TABLE baseline_estimates ADD COLUMN method text NOT NULL DEFAULT 'M3_LOGGED_DAYS';
ALTER TABLE baseline_estimates ADD COLUMN total_quantity numeric;
ALTER TABLE baseline_estimates ADD COLUMN recorded_dates integer;
ALTER TABLE baseline_estimates ADD COLUMN baseline_estimate numeric;
ALTER TABLE baseline_estimates ADD COLUMN first_sale_date date;
ALTER TABLE baseline_estimates ADD COLUMN last_sale_date date;
ALTER TABLE baseline_estimates ADD CONSTRAINT baseline_method CHECK (method IN ('M3_LOGGED_DAYS', 'SRS_RECORDED_DATES'));
ALTER TABLE baseline_estimates ADD CONSTRAINT baseline_srs_shape CHECK (method <> 'SRS_RECORDED_DATES' OR (
    total_quantity > 0 AND total_quantity < 'Infinity'::numeric AND recorded_dates >= 2
    AND baseline_estimate > 0 AND baseline_estimate < 'Infinity'::numeric
    AND first_sale_date IS NOT NULL AND last_sale_date >= first_sale_date));
ALTER TABLE baseline_estimates ADD CONSTRAINT baseline_m3_shape CHECK (method <> 'M3_LOGGED_DAYS' OR (
    window_days IS NOT NULL AND average_daily_quantity IS NOT NULL AND trend IS NOT NULL
    AND suggested_restock_qty IS NOT NULL));

SET CONSTRAINTS ALL DEFERRED;
