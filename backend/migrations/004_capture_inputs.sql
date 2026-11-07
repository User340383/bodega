-- Ingestion inputs (Milestone 3 ERD: SUBMISSIONS.inputType and maskedText).
-- Additive and nullable, so submissions created before this migration stay valid.
-- masked_text holds typed text only AFTER phone/email masking; raw text is never stored.
ALTER TABLE submissions ADD COLUMN input_type text
    CHECK (input_type IS NULL OR input_type IN ('IMAGE','TEXT','MANUAL'));
ALTER TABLE submissions ADD COLUMN masked_text text
    CHECK (masked_text IS NULL OR char_length(masked_text) <= 5000);
