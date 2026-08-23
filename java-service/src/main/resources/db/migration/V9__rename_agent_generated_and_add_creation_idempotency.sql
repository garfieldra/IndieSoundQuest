UPDATE tournament
SET candidate_source = 'AGENT_GENERATED'
WHERE candidate_source = 'AGENT_CURATED';

ALTER TABLE tournament
    ADD COLUMN creation_idempotency_key CHAR(36) NULL,
    ADD COLUMN creation_request_hash CHAR(64) NULL,
    ADD CONSTRAINT uk_tournament_guest_creation_key
        UNIQUE (guest_session_id, creation_idempotency_key);
