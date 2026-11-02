-- btrim removes spaces, but nonblank labels must also reject tabs/newlines.
-- Additive so existing databases that applied 001 receive the same protection.
ALTER TABLE sales_records ADD CONSTRAINT sales_item_nonblank
    CHECK (item_name ~ '[^[:space:]]');
ALTER TABLE baseline_estimates ADD CONSTRAINT baseline_item_nonblank
    CHECK (item_name ~ '[^[:space:]]');
