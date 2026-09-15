CREATE TABLE agent_run_follow_up (
  id BINARY(16) PRIMARY KEY,
  run_id BINARY(16) NOT NULL,
  conversation_id BINARY(16) NOT NULL,
  guest_session_id BINARY(16) NOT NULL,
  client_message_id BINARY(16) NOT NULL,
  content TEXT NOT NULL,
  status VARCHAR(24) NOT NULL,
  next_run_id BINARY(16) NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  resolved_at TIMESTAMP(3) NULL,
  CONSTRAINT fk_agent_follow_up_run FOREIGN KEY (run_id) REFERENCES agent_run(id),
  CONSTRAINT fk_agent_follow_up_conversation FOREIGN KEY (conversation_id) REFERENCES conversation(id),
  CONSTRAINT fk_agent_follow_up_guest FOREIGN KEY (guest_session_id) REFERENCES guest_session(id),
  CONSTRAINT fk_agent_follow_up_next_run FOREIGN KEY (next_run_id) REFERENCES agent_run(id),
  UNIQUE KEY uq_agent_follow_up_run (run_id),
  UNIQUE KEY uq_agent_follow_up_client (conversation_id, client_message_id),
  INDEX idx_agent_follow_up_status (status, created_at)
);
