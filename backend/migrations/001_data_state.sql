CREATE TABLE owners (
    id uuid PRIMARY KEY
);
CREATE TABLE submissions (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    image_key text,
    sale_date date NOT NULL,
    status text NOT NULL DEFAULT 'UPLOADED' CHECK (status IN
      ('UPLOADED','CLASSIFYING','NEEDS_VERIFICATION','AI_UNAVAILABLE','REJECTED','CONFIRMED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    draft_payload jsonb CHECK (draft_payload IS NULL OR jsonb_typeof(draft_payload) = 'object'),
    UNIQUE(id, sale_date)
);
CREATE INDEX submissions_owner_status_date ON submissions(owner_id, status, sale_date);
CREATE TABLE sales_records (
    id uuid PRIMARY KEY,
    submission_id uuid NOT NULL,
    line_number integer NOT NULL CHECK (line_number >= 0),
    item_name text NOT NULL CHECK (length(btrim(item_name)) BETWEEN 1 AND 200),
    quantity numeric NOT NULL CHECK (quantity > 0 AND quantity < 'Infinity'::numeric),
    unit_price numeric NOT NULL CHECK (unit_price >= 0 AND unit_price < 'Infinity'::numeric),
    sale_date date NOT NULL,
    source text NOT NULL CHECK (source IN ('AI_EXTRACTED','MANUAL')),
    FOREIGN KEY (submission_id, sale_date) REFERENCES submissions(id, sale_date) ON DELETE CASCADE,
    UNIQUE(submission_id, line_number)
);
CREATE INDEX sales_records_item_date ON sales_records(lower(btrim(item_name)), sale_date);
CREATE TABLE baseline_estimates (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    item_name text NOT NULL CHECK (length(btrim(item_name)) BETWEEN 1 AND 200),
    window_days integer NOT NULL CHECK (window_days > 0),
    logged_days integer NOT NULL CHECK (logged_days >= 0),
    average_daily_quantity numeric NOT NULL CHECK (average_daily_quantity >= 0 AND average_daily_quantity < 'Infinity'::numeric),
    trend text NOT NULL CHECK (trend IN ('UP','DOWN','STABLE','INSUFFICIENT_DATA')),
    suggested_restock_qty integer NOT NULL CHECK (suggested_restock_qty >= 0),
    computed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX baselines_owner_item_time ON baseline_estimates(owner_id, lower(btrim(item_name)), computed_at DESC);

CREATE FUNCTION enforce_submission_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'UPLOADED' THEN
            RAISE EXCEPTION 'Submissions must start UPLOADED' USING ERRCODE = '23514';
        END IF;
        IF NEW.sale_date > CURRENT_DATE THEN
            RAISE EXCEPTION 'Sale date cannot be in the future' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.owner_id <> OLD.owner_id OR NEW.sale_date <> OLD.sale_date THEN
        RAISE EXCEPTION 'Submission owner and date are immutable' USING ERRCODE = '23514';
    END IF;
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF (OLD.status = 'UPLOADED' AND NEW.status = 'CLASSIFYING') OR
       (OLD.status = 'CLASSIFYING' AND NEW.status IN ('NEEDS_VERIFICATION','AI_UNAVAILABLE','REJECTED')) OR
       (OLD.status = 'NEEDS_VERIFICATION' AND NEW.status = 'CONFIRMED') OR
       (OLD.status = 'AI_UNAVAILABLE' AND NEW.status IN ('NEEDS_VERIFICATION','CONFIRMED')) OR
       (OLD.status = 'REJECTED' AND NEW.status = 'NEEDS_VERIFICATION') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Invalid submission transition: % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
END $$;
CREATE TRIGGER submission_transition BEFORE INSERT OR UPDATE ON submissions
FOR EACH ROW EXECUTE FUNCTION enforce_submission_transition();

-- Deferred checks allow rows and their parent's final status to change atomically.
CREATE FUNCTION enforce_confirmed_records() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; state text; has_rows boolean;
BEGIN
    IF TG_TABLE_NAME = 'submissions' THEN
        target := COALESCE(NEW.id, OLD.id);
    ELSE
        target := COALESCE(NEW.submission_id, OLD.submission_id);
    END IF;
    SELECT status INTO state FROM submissions WHERE id = target;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT EXISTS(SELECT 1 FROM sales_records WHERE submission_id = target) INTO has_rows;
    IF (state = 'CONFIRMED') <> has_rows THEN
        RAISE EXCEPTION 'Only confirmed submissions must contain sales records' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER confirmed_submission AFTER INSERT OR UPDATE ON submissions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_confirmed_records();
CREATE CONSTRAINT TRIGGER confirmed_sales AFTER INSERT OR UPDATE OR DELETE ON sales_records
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_confirmed_records();
CREATE FUNCTION prevent_sales_edit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Confirmed records cannot be edited' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER immutable_sales BEFORE UPDATE ON sales_records
FOR EACH ROW EXECUTE FUNCTION prevent_sales_edit();
