CREATE TABLE agent_run_intervention (
  id BINARY(16) PRIMARY KEY,
  run_id BINARY(16) NOT NULL,
  conversation_id BINARY(16) NOT NULL,
  guest_session_id BINARY(16) NOT NULL,
  client_message_id BINARY(16) NOT NULL,
  sequence_number BIGINT NOT NULL,
  type VARCHAR(32) NOT NULL,
  content TEXT NOT NULL,
  status VARCHAR(24) NOT NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  applied_at TIMESTAMP(3) NULL,
  CONSTRAINT fk_agent_intervention_run FOREIGN KEY (run_id) REFERENCES agent_run(id),
  CONSTRAINT fk_agent_intervention_conversation FOREIGN KEY (conversation_id) REFERENCES conversation(id),
  CONSTRAINT fk_agent_intervention_guest FOREIGN KEY (guest_session_id) REFERENCES guest_session(id),
  UNIQUE KEY uq_agent_intervention_client (run_id, client_message_id),
  UNIQUE KEY uq_agent_intervention_sequence (run_id, sequence_number),
  INDEX idx_agent_intervention_run_sequence (run_id, sequence_number)
);
