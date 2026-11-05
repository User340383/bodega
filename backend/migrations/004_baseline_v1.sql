-- Forward-only upgrade. Preserve historical sales and snapshots, including values
-- accepted before the final baseline. New writes obey final limits; legacy rows
-- are not rounded, truncated, deduplicated, or assigned invented analysis dates.
-- Flush deferred integrity events before interleaving backfills and ALTER TABLE.
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE owners ADD COLUMN display_name varchar(100) NOT NULL DEFAULT 'Owner';
ALTER TABLE owners ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE submissions ADD COLUMN input_type varchar NOT NULL DEFAULT 'TEXT'
    CHECK (input_type IN ('IMAGE','TEXT','MANUAL'));
UPDATE submissions SET input_type='IMAGE' WHERE image_key IS NOT NULL;
ALTER TABLE submissions ADD COLUMN masked_text text;
ALTER TABLE submissions ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE submissions ADD COLUMN confirmed_at timestamptz;
UPDATE submissions SET updated_at=created_at,
    confirmed_at=CASE WHEN status='CONFIRMED' THEN created_at END;
ALTER TABLE submissions ADD CONSTRAINT confirmed_timestamp
    CHECK (status <> 'CONFIRMED' OR confirmed_at IS NOT NULL);
CREATE INDEX submissions_stale ON submissions(status, updated_at);
CREATE INDEX submissions_retention ON submissions(created_at);

ALTER TABLE sales_records ADD COLUMN item_key text;
ALTER TABLE baseline_estimates ADD COLUMN item_key text;
ALTER TABLE sales_records DISABLE TRIGGER immutable_sales;
UPDATE sales_records SET item_key=lower(regexp_replace(btrim(item_name), '\s+', ' ', 'g'));
ALTER TABLE sales_records ENABLE TRIGGER immutable_sales;
UPDATE baseline_estimates SET item_key=lower(regexp_replace(btrim(item_name), '\s+', ' ', 'g'));
ALTER TABLE sales_records ALTER COLUMN item_key SET NOT NULL;
ALTER TABLE baseline_estimates ALTER COLUMN item_key SET NOT NULL;
ALTER TABLE baseline_estimates ADD COLUMN as_of_date date;
ALTER TABLE baseline_estimates ADD COLUMN cover_days integer NOT NULL DEFAULT 7 CHECK (cover_days=7);
ALTER TABLE baseline_estimates ADD COLUMN prior_average_daily_quantity numeric;
CREATE UNIQUE INDEX baselines_owner_date_item ON baseline_estimates(owner_id,as_of_date,item_key);
CREATE INDEX sales_item_key ON sales_records(item_key);

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
            (OLD.status IN ('NEEDS_VERIFICATION','REJECTED','AI_UNAVAILABLE') AND NEW.status='CONFIRMED') OR
            (OLD.status IN ('REJECTED','AI_UNAVAILABLE') AND NEW.status='NEEDS_VERIFICATION')) THEN
            RAISE EXCEPTION 'Invalid submission transition' USING ERRCODE='23514';
        END IF;
    END IF;
    NEW.updated_at=now();
    IF NEW.status='CONFIRMED' THEN NEW.confirmed_at=COALESCE(NEW.confirmed_at,now()); END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION populate_item_key() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.item_key=COALESCE(NEW.item_key,lower(regexp_replace(btrim(NEW.item_name),'\s+',' ','g')));
    RETURN NEW;
END $$;
CREATE TRIGGER sales_item_identity BEFORE INSERT ON sales_records FOR EACH ROW EXECUTE FUNCTION populate_item_key();
CREATE TRIGGER baseline_item_identity BEFORE INSERT ON baseline_estimates FOR EACH ROW EXECUTE FUNCTION populate_item_key();
SET CONSTRAINTS ALL DEFERRED;
