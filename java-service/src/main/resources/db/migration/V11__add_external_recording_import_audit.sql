ALTER TABLE recording
    ADD COLUMN external_source_url VARCHAR(1000) NULL,
    ADD COLUMN external_imported_at TIMESTAMP NULL;
