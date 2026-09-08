CREATE TABLE agent_run (
  id BINARY(16) PRIMARY KEY,
  guest_session_id BINARY(16) NOT NULL,
  conversation_id BINARY(16) NULL,
  tournament_id BINARY(16) NULL,
  run_type VARCHAR(40) NOT NULL,
  status VARCHAR(30) NOT NULL,
  model_provider VARCHAR(40) NULL,
  model_name VARCHAR(80) NULL,
  budget_json JSON NULL,
  input_version VARCHAR(20) NULL,
  artifact_ref_json JSON NULL,
  context_snapshot_json JSON NULL,
  started_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  completed_at TIMESTAMP(3) NULL,
  expires_at TIMESTAMP(3) NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_agent_run_guest FOREIGN KEY (guest_session_id) REFERENCES guest_session(id),
  CONSTRAINT fk_agent_run_conversation FOREIGN KEY (conversation_id) REFERENCES conversation(id),
  CONSTRAINT fk_agent_run_tournament FOREIGN KEY (tournament_id) REFERENCES tournament(id),
  INDEX idx_agent_run_guest_status (guest_session_id, status),
  INDEX idx_agent_run_conversation (conversation_id, status)
);

CREATE TABLE agent_run_event (
  id BINARY(16) PRIMARY KEY,
  run_id BINARY(16) NOT NULL,
  sequence_number BIGINT NOT NULL,
  type VARCHAR(40) NOT NULL,
  payload_json JSON NOT NULL,
  trace_id VARCHAR(64) NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_agent_run_event_run FOREIGN KEY (run_id) REFERENCES agent_run(id),
  UNIQUE KEY uq_agent_run_event_sequence (run_id, sequence_number),
  INDEX idx_agent_run_event_run_sequence (run_id, sequence_number)
);

CREATE TABLE preference_event (
  id BINARY(16) PRIMARY KEY,
  guest_session_id BINARY(16) NOT NULL,
  conversation_id BINARY(16) NULL,
  agent_run_id BINARY(16) NULL,
  target_type VARCHAR(20) NOT NULL,
  target_ref_json JSON NOT NULL,
  feedback VARCHAR(30) NOT NULL,
  idempotency_key VARCHAR(80) NOT NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_preference_event_guest FOREIGN KEY (guest_session_id) REFERENCES guest_session(id),
  UNIQUE KEY uq_preference_event_idempotency (guest_session_id, idempotency_key),
  INDEX idx_preference_event_guest_created (guest_session_id, created_at DESC)
);
