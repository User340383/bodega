-- Approved policy: incompatible historical values stop the transaction unchanged.
-- Never round old sales or invent baseline analysis dates to make an upgrade pass.
SET CONSTRAINTS ALL IMMEDIATE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM sales_records WHERE
        quantity>99999.99 OR quantity<>round(quantity,2) OR
        unit_price>9999999.99 OR unit_price<>round(unit_price,2) OR
        length(item_name)>100 OR item_name<>regexp_replace(btrim(item_name),'\s+',' ','g') OR
        line_number NOT BETWEEN 0 AND 99) THEN
        RAISE EXCEPTION 'Upgrade blocked: historical sales require approved remediation; no records were changed';
    END IF;
    IF EXISTS (SELECT 1 FROM baseline_estimates WHERE as_of_date IS NULL OR
        average_daily_quantity>99999999.9999 OR average_daily_quantity<>round(average_daily_quantity,4) OR
        window_days<>7 OR cover_days<>7 OR length(item_name)>100 OR
        (prior_average_daily_quantity IS NOT NULL AND
            (prior_average_daily_quantity<>round(prior_average_daily_quantity,4) OR prior_average_daily_quantity>99999999.9999))) THEN
        RAISE EXCEPTION 'Upgrade blocked: historical baseline dates or values require approved remediation; no snapshots were changed';
    END IF;
END $$;

ALTER TABLE sales_records DISABLE TRIGGER immutable_sales;
ALTER TABLE sales_records DROP CONSTRAINT final_line_number;
-- Move higher line numbers first to preserve the unique key throughout the shift.
DO $$ DECLARE rid uuid; BEGIN
    FOR rid IN SELECT id FROM sales_records ORDER BY line_number DESC LOOP
        UPDATE sales_records SET line_number=line_number+1 WHERE id=rid;
    END LOOP;
END $$;
ALTER TABLE sales_records RENAME COLUMN line_number TO line_no;
ALTER TABLE sales_records ADD CONSTRAINT final_line_no CHECK (line_no BETWEEN 1 AND 100);
ALTER TABLE sales_records ADD CONSTRAINT sales_submission_fk
    FOREIGN KEY(submission_id) REFERENCES submissions(id) ON DELETE CASCADE;
-- The old composite FK proves this duplicate date equals the parent's date.
-- Removing the duplicate loses no information; all reads now join the parent.
ALTER TABLE sales_records DROP COLUMN sale_date;
ALTER TABLE sales_records ALTER COLUMN item_name TYPE varchar(100);
ALTER TABLE sales_records ALTER COLUMN quantity TYPE numeric(10,2);
ALTER TABLE sales_records ALTER COLUMN unit_price TYPE numeric(12,2);
ALTER TABLE sales_records VALIDATE CONSTRAINT final_sales_limits;
ALTER TABLE sales_records ENABLE TRIGGER immutable_sales;
ALTER TABLE submissions VALIDATE CONSTRAINT confirmed_has_no_draft;

ALTER TABLE baseline_estimates ALTER COLUMN as_of_date SET NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN item_name TYPE varchar(100);
ALTER TABLE baseline_estimates ALTER COLUMN average_daily_quantity TYPE numeric(12,4);
ALTER TABLE baseline_estimates ALTER COLUMN prior_average_daily_quantity TYPE numeric(12,4);
ALTER TABLE baseline_estimates ADD CONSTRAINT baseline_window CHECK (window_days=7);
ALTER TABLE baseline_estimates VALIDATE CONSTRAINT final_snapshot_precision;

-- The final diagram permits direct confirmation from open states, not an extra
-- rejected/unavailable -> review lifecycle transition.
CREATE OR REPLACE FUNCTION enforce_submission_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status <> 'UPLOADED' AND NOT (NEW.input_type='MANUAL' AND NEW.status='CONFIRMED') THEN
            RAISE EXCEPTION 'Invalid initial submission state' USING ERRCODE='23514';
        END IF;
        IF NEW.sale_date > CURRENT_DATE THEN
            RAISE EXCEPTION 'Sale date cannot be in the future' USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.owner_id<>OLD.owner_id OR NEW.sale_date<>OLD.sale_date OR NEW.input_type<>OLD.input_type THEN
            RAISE EXCEPTION 'Submission identity is immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.status<>OLD.status AND NOT (
            (OLD.status='UPLOADED' AND NEW.status IN ('CLASSIFYING','AI_UNAVAILABLE')) OR
            (OLD.status='CLASSIFYING' AND NEW.status IN ('NEEDS_VERIFICATION','REJECTED','AI_UNAVAILABLE')) OR
            (OLD.status IN ('NEEDS_VERIFICATION','REJECTED','AI_UNAVAILABLE') AND NEW.status='CONFIRMED')) THEN
            RAISE EXCEPTION 'Invalid submission transition' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.updated_at=now();
    IF NEW.status='CONFIRMED' THEN NEW.confirmed_at=COALESCE(NEW.confirmed_at,now()); END IF;
    RETURN NEW;
END $$;
SET CONSTRAINTS ALL DEFERRED;
