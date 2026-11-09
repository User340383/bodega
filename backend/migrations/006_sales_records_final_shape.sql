-- SALES_RECORDS in the final Milestone 3 shape (sections 7.1, 7.2):
--   * saleDate is not repeated on SALES_RECORDS; it is read from the parent submission.
--   * UNIQUE (submissionId, lineNo) with lineNo 1-100.
--   * an index on itemKey.
-- Forward-only. No sales value, record ID or submission is changed or lost:
--   * the dropped date is recoverable, because the composite FK from 001 guaranteed it always
--     equalled the parent's sale_date;
--   * line numbers shift from 0-99 to 1-100 in the same order, through negative values so the
--     unique key never collides;
--   * item_key is a generated column, so no immutable row is updated to fill it.
SET CONSTRAINTS ALL IMMEDIATE;

-- Replace the composite (submission_id, sale_date) FK with a plain FK on submission_id.
DO $$ DECLARE fk text; BEGIN
    FOR fk IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'sales_records'::regclass AND contype = 'f' LOOP
        EXECUTE format('ALTER TABLE sales_records DROP CONSTRAINT %I', fk);
    END LOOP;
END $$;
ALTER TABLE sales_records ADD CONSTRAINT sales_records_submission_fk
    FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE;
ALTER TABLE sales_records DROP COLUMN sale_date;  -- Also drops the old (item, sale_date) index.

-- lineNo 1-100. Drop both old range checks, renumber, then add the final one.
DO $$ DECLARE ck text; BEGIN
    FOR ck IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'sales_records'::regclass AND contype = 'c'
                AND pg_get_constraintdef(oid) LIKE '%line_number%' LOOP
        EXECUTE format('ALTER TABLE sales_records DROP CONSTRAINT %I', ck);
    END LOOP;
END $$;
ALTER TABLE sales_records DISABLE TRIGGER immutable_sales;  -- Renumbering only; values unchanged.
UPDATE sales_records SET line_number = -(line_number + 1);
UPDATE sales_records SET line_number = -line_number;
ALTER TABLE sales_records ENABLE TRIGGER immutable_sales;
ALTER TABLE sales_records RENAME COLUMN line_number TO line_no;
ALTER TABLE sales_records ADD CONSTRAINT sales_line_no_range CHECK (line_no BETWEEN 1 AND 100);

-- itemKey: trimmed, lower-cased label used for item matching lookups (M3 7.2 index (itemKey)).
ALTER TABLE sales_records ADD COLUMN item_key text GENERATED ALWAYS AS (lower(btrim(item_name))) STORED;
CREATE INDEX sales_records_item_key ON sales_records(item_key);
-- History and analysis read by the parent's owner/status/date; this serves the join.
CREATE INDEX sales_records_submission ON sales_records(submission_id);

SET CONSTRAINTS ALL DEFERRED;
