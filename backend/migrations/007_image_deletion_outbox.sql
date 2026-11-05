-- Operational outbox for final Milestone 3 sections 5.4 and 7.4.
-- No FK: deletion of a submission/owner must not delete its pending cleanup job.
-- No domain records or previous migrations are changed.
CREATE TABLE image_deletion_jobs (
    bucket_name text NOT NULL CHECK (length(btrim(bucket_name)) > 0),
    image_key text NOT NULL CHECK (length(btrim(image_key)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_attempt_at timestamptz,
    PRIMARY KEY (bucket_name, image_key)
);
