package com.indiesoundquest.listening;

import com.indiesoundquest.tournament.domain.Recording;
import com.indiesoundquest.tournament.repository.RecordingRepository;
import java.time.Clock;
import java.time.Duration;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class ListeningOptionsService {
  static final String APPLE_PROVIDER = "APPLE_ITUNES";
  private final RecordingRepository recordings;
  private final RecordingListeningAssetRepository assets;
  private final AppleItunesClient apple;
  private final Clock clock;
  private final String storefront;
  private final Duration availableTtl;
  private final Duration unavailableTtl;
  private final ConcurrentHashMap<UUID, Object> recordingLocks = new ConcurrentHashMap<>();

  @Autowired
  public ListeningOptionsService(
      RecordingRepository recordings,
      RecordingListeningAssetRepository assets,
      AppleItunesClient apple,
      @Value("${listening.apple.storefront:CN}") String storefront,
      @Value("${listening.apple.available-cache-hours:168}") long availableCacheHours,
      @Value("${listening.apple.unavailable-cache-hours:24}") long unavailableCacheHours) {
    this(recordings, assets, apple, Clock.systemUTC(), storefront,
        Duration.ofHours(availableCacheHours), Duration.ofHours(unavailableCacheHours));
  }

  ListeningOptionsService(RecordingRepository recordings, RecordingListeningAssetRepository assets,
                          AppleItunesClient apple, Clock clock, String storefront,
                          Duration availableTtl, Duration unavailableTtl) {
    this.recordings = recordings;
    this.assets = assets;
    this.apple = apple;
    this.clock = clock;
    this.storefront = storefront.toUpperCase(java.util.Locale.ROOT);
    this.availableTtl = availableTtl;
    this.unavailableTtl = unavailableTtl;
  }

  public ListeningOptions get(UUID recordingId) {
    var recording = recordings.findByIdWithArtist(recordingId).orElseThrow(NoSuchElementException::new);
    var lock = recordingLocks.computeIfAbsent(recordingId, ignored -> new Object());
    synchronized (lock) {
      var now = clock.instant();
      var asset = assets.findByRecordingIdAndProviderAndStorefront(recordingId, APPLE_PROVIDER, storefront)
          .orElseGet(() -> RecordingListeningAsset.create(recording, APPLE_PROVIDER, storefront));
      if (!asset.isFresh(now)) refresh(asset, recording, now);
      return view(recording, asset);
    }
  }

  private void refresh(RecordingListeningAsset asset, Recording recording, java.time.Instant now) {
    var match = apple.findPreview(recording.getTitle(), recording.getArtist().getName(), recording.getAlbumTitle());
    if (match.isPresent()) asset.markAvailable(match.get(), now, now.plus(availableTtl));
    else asset.markUnavailable(now, now.plus(unavailableTtl));
    assets.save(asset);
  }

  private ListeningOptions view(Recording recording, RecordingListeningAsset asset) {
    Preview preview = null;
    if (asset.getStatus() == ListeningAssetStatus.AVAILABLE) {
      preview = new Preview(APPLE_PROVIDER, asset.getPreviewUrl(), asset.getProviderTrackUrl(),
          "30 秒试听由 Apple 提供");
    }
    return new ListeningOptions(
        recording.getId(),
        asset.getStatus(),
        preview,
        List.of(new PlatformLink("NETEASE_CLOUD_MUSIC", "去网易云搜索",
            ListeningLinkFactory.neteaseSongSearch(recording.getTitle(), recording.getArtist().getName()))));
  }

  public record ListeningOptions(UUID recordingId, ListeningAssetStatus status, Preview preview,
                                 List<PlatformLink> platformLinks) {}
  public record Preview(String provider, String url, String providerTrackUrl, String attribution) {}
  public record PlatformLink(String provider, String label, String url) {}
}
