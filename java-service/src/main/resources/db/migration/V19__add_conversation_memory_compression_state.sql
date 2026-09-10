ALTER TABLE conversation
  ADD COLUMN summary_version INT NOT NULL DEFAULT 0 AFTER summary,
  ADD COLUMN summary_through_sequence BIGINT NOT NULL DEFAULT 0 AFTER summary_version,
  ADD COLUMN summary_updated_at TIMESTAMP(3) NULL AFTER summary_through_sequence;
