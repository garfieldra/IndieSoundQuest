CREATE TABLE async_outbox_event (
  id BINARY(16) PRIMARY KEY,
  aggregate_type VARCHAR(64) NOT NULL,
  aggregate_id BINARY(16) NOT NULL,
  event_type VARCHAR(96) NOT NULL,
  payload_json JSON NOT NULL,
  trace_id VARCHAR(64) NULL,
  status VARCHAR(24) NOT NULL,
  publish_attempts INT NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  published_at TIMESTAMP(3) NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  INDEX ix_outbox_pending (status, next_attempt_at),
  UNIQUE KEY uk_outbox_event (aggregate_type, aggregate_id, event_type)
);
