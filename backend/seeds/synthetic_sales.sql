-- Synthetic sales history for building and testing analysis and guidance from the command line
-- (WorkDivision "Immediate Execution Steps", step 2). No real data: two invented items, Bread and
-- Milk, on the 14 days before today, entered as standalone manual entries (CONFIRMED, source MANUAL).
--
--   Bread: 2 per day on the older 7 days, 4 per day on the latest 7  -> average 4.0, trend UP,
--          suggested stock ceil(4.0 x 7) = 28 (M3 5.5).
--   Milk:  2.5 per day on all 14 days                                -> average 2.5, trend STABLE,
--          suggested stock ceil(2.5 x 7) = 18.
--
-- Run it after the migrations (the backend applies them at start), then call GET /api/v1/analysis:
--   docker compose exec -T postgres psql -U bodega -d bodega -v ON_ERROR_STOP=1 < backend/seeds/synthetic_sales.sql
--
-- Safe to repeat: IDs are derived from the owner and the sale date, and existing rows are left
-- alone. It never runs at application startup and must not be used on a production database.
DO $$
DECLARE
    seed_owner uuid := (SELECT id FROM owners ORDER BY created_at, id LIMIT 1);
    back integer;
    sale_day date;
    hash text;
    submission uuid;
    added integer := 0;
BEGIN
    IF seed_owner IS NULL THEN
        RAISE EXCEPTION 'No owner row yet: start the backend once (it runs the migrations), then run this script';
    END IF;
    FOR back IN 1..14 LOOP
        sale_day := CURRENT_DATE - back;
        -- A stable version-4-shaped UUID per owner and date, so a repeat run adds nothing twice.
        hash := md5('bodega-synthetic-sales:' || seed_owner || ':' || sale_day);
        submission := (substr(hash, 1, 12) || '4' || substr(hash, 14, 3) || '8' || substr(hash, 18))::uuid;
        INSERT INTO submissions (id, owner_id, sale_date, status, input_type, confirmed_at)
            VALUES (submission, seed_owner, sale_day, 'CONFIRMED', 'MANUAL', now())
            ON CONFLICT (id) DO NOTHING;
        CONTINUE WHEN NOT FOUND;
        INSERT INTO sales_records (id, submission_id, line_no, item_name, quantity, unit_price, source) VALUES
            (gen_random_uuid(), submission, 1, 'Bread', CASE WHEN back <= 7 THEN 4 ELSE 2 END, 1.50, 'MANUAL'),
            (gen_random_uuid(), submission, 2, 'Milk', 2.5, 1.20, 'MANUAL');
        added := added + 1;
    END LOOP;
    RAISE NOTICE 'Synthetic sales: % sales dates added, % already present', added, 14 - added;
END $$;
