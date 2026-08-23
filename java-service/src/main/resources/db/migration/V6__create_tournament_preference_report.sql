CREATE TABLE tournament_preference_report (
  id BINARY(16) PRIMARY KEY,
  tournament_id BINARY(16) NOT NULL,
  status VARCHAR(32) NOT NULL,
  report_json JSON NULL,
  failure_message VARCHAR(512) NULL,
  version_number INT NOT NULL DEFAULT 1,
  created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  generated_at TIMESTAMP(3) NULL,
  updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_report_tournament FOREIGN KEY (tournament_id) REFERENCES tournament(id),
  UNIQUE KEY uk_report_tournament_version (tournament_id, version_number)
);
