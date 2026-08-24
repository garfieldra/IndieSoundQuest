CREATE TABLE conversation (
  id BINARY(16) PRIMARY KEY, guest_session_id BINARY(16) NOT NULL, title VARCHAR(120) NOT NULL, summary TEXT NULL,
  status VARCHAR(20) NOT NULL, last_message_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_conversation_guest FOREIGN KEY (guest_session_id) REFERENCES guest_session(id), INDEX idx_conversation_guest_last (guest_session_id, status, last_message_at)
);
CREATE TABLE conversation_message (
  id BINARY(16) PRIMARY KEY, conversation_id BINARY(16) NOT NULL, client_message_id BINARY(16) NULL, agent_run_id BINARY(16) NULL,
  role VARCHAR(20) NOT NULL, type VARCHAR(40) NOT NULL, text_content TEXT NULL, card_type VARCHAR(40) NULL, card_payload_json JSON NULL, status VARCHAR(20) NOT NULL, sequence_number BIGINT NOT NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), completed_at TIMESTAMP(3) NULL,
  CONSTRAINT fk_message_conversation FOREIGN KEY (conversation_id) REFERENCES conversation(id), UNIQUE KEY uq_message_client (conversation_id, client_message_id), UNIQUE KEY uq_message_sequence (conversation_id, sequence_number), INDEX idx_message_conversation_sequence (conversation_id, sequence_number)
);
CREATE TABLE conversation_tournament_link (
  conversation_id BINARY(16) NOT NULL, tournament_id BINARY(16) NOT NULL, relation_type VARCHAR(30) NOT NULL, created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (conversation_id, tournament_id), CONSTRAINT fk_conversation_link_conversation FOREIGN KEY (conversation_id) REFERENCES conversation(id), CONSTRAINT fk_conversation_link_tournament FOREIGN KEY (tournament_id) REFERENCES tournament(id)
);
CREATE TABLE music_preference_memory (
  id BINARY(16) PRIMARY KEY, guest_session_id BINARY(16) NOT NULL, content VARCHAR(800) NOT NULL, source_conversation_id BINARY(16) NOT NULL, confirmation_status VARCHAR(20) NOT NULL,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), revoked_at TIMESTAMP(3) NULL,
  CONSTRAINT fk_memory_guest FOREIGN KEY (guest_session_id) REFERENCES guest_session(id), CONSTRAINT fk_memory_conversation FOREIGN KEY (source_conversation_id) REFERENCES conversation(id), INDEX idx_memory_guest_status (guest_session_id, confirmation_status)
);
