-- 007: reserved, no schema change.
--
-- An earlier, never-merged draft of this branch had a revision 007 (24-hour input deadline, a
-- retry transition and a second baseline shape; docs/INTEGRATION_DECISIONS.md D-26, D-27, D-29).
-- It was withdrawn, but local databases that ran it are stamped "007". Keeping the revision
-- number, with no content, lets Alembic recognize those databases; 008 then removes whatever
-- the draft created. On every other database this file and 008 change nothing.
SELECT 1;
