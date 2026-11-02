-- Remove implementation-only label caps; preserve the nonblank checks from 002.
-- Existing rows, indexes, foreign keys, and lifecycle rules are unchanged.
ALTER TABLE sales_records DROP CONSTRAINT sales_records_item_name_check;
ALTER TABLE baseline_estimates DROP CONSTRAINT baseline_estimates_item_name_check;
