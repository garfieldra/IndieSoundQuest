package com.indiesoundquest.tournament.domain;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "recording")
public class Recording {
  @Id private UUID id;
  @ManyToOne(fetch = FetchType.LAZY) @JoinColumn(name = "artist_id", nullable = false) private Artist artist;
  @Column(nullable = false) private String title;
  @Column(name = "album_title") private String albumTitle;
  @Column(name = "version_label") private String versionLabel;
  @Column(name = "seed_rank", nullable = false) private int seedRank;
  @Column(name = "cover_url") private String coverUrl;
  @Column(name = "cover_source") private String coverSource;
  @Column(name = "cover_status") private String coverStatus;
  @Column(name = "musicbrainz_mbid", unique = true) private String musicbrainzMbid;
  @Column(name = "release_musicbrainz_mbid") private String releaseMusicbrainzMbid;
  @Column(name = "catalog_source", nullable = false) private String catalogSource;
  @Column(name = "external_source_url", length = 1000) private String externalSourceUrl;
  @Column(name = "external_imported_at") private Instant externalImportedAt;

  protected Recording() {}

  public static Recording imported(Artist artist, String title, String albumTitle, int seedRank,
                                   String musicbrainzMbid, String releaseMusicbrainzMbid, String externalSourceUrl) {
    var recording = new Recording();
    recording.id = UUID.randomUUID();
    recording.artist = artist;
    recording.title = title;
    recording.albumTitle = albumTitle;
    recording.seedRank = seedRank;
    recording.musicbrainzMbid = musicbrainzMbid;
    recording.releaseMusicbrainzMbid = releaseMusicbrainzMbid;
    recording.coverUrl = releaseMusicbrainzMbid == null ? null
        : "https://coverartarchive.org/release/" + releaseMusicbrainzMbid + "/front-500";
    // The database contract is non-null even when a MusicBrainz browse result
    // has no release attached yet. Cover enrichment can upgrade NONE later.
    recording.coverSource = releaseMusicbrainzMbid == null ? "NONE" : "COVER_ART_ARCHIVE";
    recording.coverStatus = releaseMusicbrainzMbid == null ? "UNAVAILABLE" : "PENDING";
    recording.catalogSource = "EXTERNAL_VERIFIED";
    recording.externalSourceUrl = externalSourceUrl;
    recording.externalImportedAt = Instant.now();
    return recording;
  }

  public void attachMusicbrainzIdentity(String musicbrainzMbid, String releaseMusicbrainzMbid, String resolvedAlbumTitle) {
    if (this.musicbrainzMbid != null && !this.musicbrainzMbid.equals(musicbrainzMbid)) {
      throw new IllegalStateException("recording already has a different MusicBrainz identity");
    }
    this.musicbrainzMbid = musicbrainzMbid;
    this.releaseMusicbrainzMbid = releaseMusicbrainzMbid;
    if ((this.albumTitle == null || this.albumTitle.isBlank()) && resolvedAlbumTitle != null) this.albumTitle = resolvedAlbumTitle;
    if (releaseMusicbrainzMbid != null && (this.coverUrl == null || this.coverUrl.isBlank())) {
      this.coverUrl = "https://coverartarchive.org/release/" + releaseMusicbrainzMbid + "/front-500";
      this.coverSource = "COVER_ART_ARCHIVE";
      this.coverStatus = "PENDING";
    }
  }

  public void attachExternalDiscovery(String sourceUrl) {
    if (sourceUrl == null || sourceUrl.isBlank()) return;
    if (this.externalSourceUrl == null || this.externalSourceUrl.isBlank()) this.externalSourceUrl = sourceUrl;
    if (this.externalImportedAt == null) this.externalImportedAt = Instant.now();
  }

  public UUID getId() { return id; }
  public String getTitle() { return title; }
  public Artist getArtist() { return artist; }
  public String getAlbumTitle() { return albumTitle; }
  public String getVersionLabel() { return versionLabel; }
  public String getCoverUrl() { return coverUrl; }
  public String getCoverStatus() { return coverStatus; }
  public String getMusicbrainzMbid() { return musicbrainzMbid; }
  public String getReleaseMusicbrainzMbid() { return releaseMusicbrainzMbid; }
  public String getCatalogSource() { return catalogSource; }
  public String getExternalSourceUrl() { return externalSourceUrl; }
  public Instant getExternalImportedAt() { return externalImportedAt; }
}
