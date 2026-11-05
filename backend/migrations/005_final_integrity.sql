-- NOT VALID preserves legacy values, but PostgreSQL checks every new/updated row.
DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='sales_records'::regclass AND conname='final_sales_limits') THEN
ALTER TABLE sales_records ADD CONSTRAINT final_sales_limits CHECK (
    length(item_name) BETWEEN 1 AND 100 AND quantity <= 99999.99
    AND quantity=round(quantity,2) AND unit_price <= 9999999.99
    AND unit_price=round(unit_price,2)) NOT VALID;
END IF;
END $$;

-- Additive enforcement of final row-count/draft invariants; no data deletion.
ALTER TABLE sales_records ADD CONSTRAINT final_line_number
    CHECK (line_number BETWEEN 0 AND 99) NOT VALID;
ALTER TABLE submissions ADD CONSTRAINT confirmed_has_no_draft
    CHECK (status <> 'CONFIRMED' OR draft_payload IS NULL) NOT VALID;
ALTER TABLE baseline_estimates ADD CONSTRAINT final_snapshot_precision CHECK (
    average_daily_quantity <= 99999999.9999 AND average_daily_quantity=round(average_daily_quantity,4)
    AND (prior_average_daily_quantity IS NULL OR
        (prior_average_daily_quantity>=0 AND prior_average_daily_quantity<=99999999.9999
         AND prior_average_daily_quantity=round(prior_average_daily_quantity,4)))) NOT VALID;

CREATE OR REPLACE FUNCTION enforce_confirmed_records() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; state text; row_count bigint;
BEGIN
    IF TG_TABLE_NAME='submissions' THEN target:=COALESCE(NEW.id,OLD.id);
    ELSE target:=COALESCE(NEW.submission_id,OLD.submission_id); END IF;
    SELECT status INTO state FROM submissions WHERE id=target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT count(*) INTO row_count FROM sales_records WHERE submission_id=target;
    IF (state='CONFIRMED' AND row_count NOT BETWEEN 1 AND 100) OR
       (state<>'CONFIRMED' AND row_count<>0) THEN
        RAISE EXCEPTION 'Confirmed submissions require 1 to 100 rows; other states require none' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;
