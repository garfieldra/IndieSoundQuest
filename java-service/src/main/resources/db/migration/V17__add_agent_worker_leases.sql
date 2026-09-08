ALTER TABLE agent_run
  ADD COLUMN next_event_sequence BIGINT NOT NULL DEFAULT 1;

CREATE TABLE agent_run_attempt (
  id BINARY(16) PRIMARY KEY,
  run_id BINARY(16) NOT NULL,
  attempt_no INT NOT NULL,
  worker_id VARCHAR(120) NOT NULL,
  lease_token VARCHAR(80) NOT NULL,
  status VARCHAR(24) NOT NULL,
  started_at TIMESTAMP(3) NOT NULL,
  heartbeat_at TIMESTAMP(3) NOT NULL,
  lease_expires_at TIMESTAMP(3) NOT NULL,
  finished_at TIMESTAMP(3) NULL,
  failure_code VARCHAR(80) NULL,
  CONSTRAINT fk_agent_attempt_run FOREIGN KEY (run_id) REFERENCES agent_run(id),
  UNIQUE KEY uq_agent_attempt_number (run_id, attempt_no),
  UNIQUE KEY uq_agent_attempt_lease (lease_token),
  INDEX idx_agent_attempt_lease (run_id, status, lease_expires_at)
);
