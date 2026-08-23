CREATE TABLE music_preference_profile (
  id BINARY(16) PRIMARY KEY,
  guest_session_id BINARY(16) NOT NULL,
  profile_json JSON NOT NULL,
  use_for_candidate_generation BOOLEAN NOT NULL DEFAULT TRUE,
  tournament_count INT NOT NULL DEFAULT 0,
  summary_status VARCHAR(32) NOT NULL DEFAULT 'STRUCTURED_ONLY',
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_profile_guest FOREIGN KEY (guest_session_id) REFERENCES guest_session(id),
  UNIQUE KEY uk_profile_guest (guest_session_id)
);
