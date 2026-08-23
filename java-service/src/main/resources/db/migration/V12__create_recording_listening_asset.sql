CREATE TABLE recording_listening_asset (
    id BINARY(16) NOT NULL,
    recording_id BINARY(16) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    storefront VARCHAR(8) NOT NULL,
    status VARCHAR(24) NOT NULL,
    provider_item_id VARCHAR(128) NULL,
    preview_url VARCHAR(1000) NULL,
    provider_track_url VARCHAR(1000) NULL,
    matched_track_name VARCHAR(255) NULL,
    matched_artist_name VARCHAR(255) NULL,
    matched_album_title VARCHAR(255) NULL,
    checked_at TIMESTAMP(6) NOT NULL,
    expires_at TIMESTAMP(6) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_listening_asset_recording FOREIGN KEY (recording_id) REFERENCES recording (id),
    CONSTRAINT uk_listening_asset_recording_provider UNIQUE (recording_id, provider, storefront),
    INDEX idx_listening_asset_expiry (expires_at)
);

