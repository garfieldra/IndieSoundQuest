package com.indiesoundquest.listening;

import com.indiesoundquest.tournament.domain.Recording;
import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(
    name = "recording_listening_asset",
    uniqueConstraints = @UniqueConstraint(
        name = "uk_listening_asset_recording_provider",
        columnNames = {"recording_id", "provider", "storefront"}))
public class RecordingListeningAsset {
  @Id private UUID id;
  @ManyToOne(fetch = FetchType.LAZY) @JoinColumn(name = "recording_id", nullable = false) private Recording recording;
  @Column(nullable = false) private String provider;
  @Column(nullable = false) private String storefront;
  @Enumerated(EnumType.STRING) @Column(nullable = false) private ListeningAssetStatus status;
  @Column(name = "provider_item_id") private String providerItemId;
  @Column(name = "preview_url", length = 1000) private String previewUrl;
  @Column(name = "provider_track_url", length = 1000) private String providerTrackUrl;
  @Column(name = "matched_track_name") private String matchedTrackName;
  @Column(name = "matched_artist_name") private String matchedArtistName;
  @Column(name = "matched_album_title") private String matchedAlbumTitle;
  @Column(name = "checked_at", nullable = false) private Instant checkedAt;
  @Column(name = "expires_at", nullable = false) private Instant expiresAt;

  protected RecordingListeningAsset() {}

  public static RecordingListeningAsset create(Recording recording, String provider, String storefront) {
    var asset = new RecordingListeningAsset();
    asset.id = UUID.randomUUID();
    asset.recording = recording;
    asset.provider = provider;
    asset.storefront = storefront;
    return asset;
  }

  public void markAvailable(AppleItunesClient.Match match, Instant now, Instant expiresAt) {
    status = ListeningAssetStatus.AVAILABLE;
    providerItemId = match.trackId();
    previewUrl = match.previewUrl();
    providerTrackUrl = match.trackViewUrl();
    matchedTrackName = match.trackName();
    matchedArtistName = match.artistName();
    matchedAlbumTitle = match.albumTitle();
    checkedAt = now;
    this.expiresAt = expiresAt;
  }

  public void markUnavailable(Instant now, Instant expiresAt) {
    status = ListeningAssetStatus.UNAVAILABLE;
    providerItemId = null;
    previewUrl = null;
    providerTrackUrl = null;
    matchedTrackName = null;
    matchedArtistName = null;
    matchedAlbumTitle = null;
    checkedAt = now;
    this.expiresAt = expiresAt;
  }

  public boolean isFresh(Instant now) { return expiresAt != null && expiresAt.isAfter(now); }
  public ListeningAssetStatus getStatus() { return status; }
  public String getPreviewUrl() { return previewUrl; }
  public String getProviderTrackUrl() { return providerTrackUrl; }
}

