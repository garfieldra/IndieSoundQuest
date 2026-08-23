ALTER TABLE artist
    ADD COLUMN musicbrainz_mbid VARCHAR(36) NULL,
    ADD CONSTRAINT uk_artist_musicbrainz_mbid UNIQUE (musicbrainz_mbid);

ALTER TABLE recording
    ADD COLUMN musicbrainz_mbid VARCHAR(36) NULL,
    ADD COLUMN release_musicbrainz_mbid VARCHAR(36) NULL,
    ADD CONSTRAINT uk_recording_musicbrainz_mbid UNIQUE (musicbrainz_mbid);
