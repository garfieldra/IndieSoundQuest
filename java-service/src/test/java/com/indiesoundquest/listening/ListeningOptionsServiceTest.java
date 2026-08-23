package com.indiesoundquest.listening;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

import com.indiesoundquest.tournament.domain.Artist;
import com.indiesoundquest.tournament.domain.Recording;
import com.indiesoundquest.tournament.repository.RecordingRepository;
import java.time.*;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class ListeningOptionsServiceTest {
  private final RecordingRepository recordings = mock(RecordingRepository.class);
  private final RecordingListeningAssetRepository assets = mock(RecordingListeningAssetRepository.class);
  private final AppleItunesClient apple = mock(AppleItunesClient.class);
  private final Instant now = Instant.parse("2026-08-21T10:00:00Z");
  private final Clock clock = Clock.fixed(now, ZoneOffset.UTC);
  private final UUID recordingId = UUID.randomUUID();
  private Recording recording;
  private ListeningOptionsService service;

  @BeforeEach
  void setUp() {
    recording = mock(Recording.class);
    var artist = mock(Artist.class);
    when(recording.getId()).thenReturn(recordingId);
    when(recording.getTitle()).thenReturn("南国的孩子");
    when(recording.getAlbumTitle()).thenReturn("城市");
    when(recording.getArtist()).thenReturn(artist);
    when(artist.getName()).thenReturn("张悬／安溥");
    when(recordings.findByIdWithArtist(recordingId)).thenReturn(Optional.of(recording));
    service = new ListeningOptionsService(
        recordings, assets, apple, clock, "CN", Duration.ofDays(7), Duration.ofDays(1));
  }

  @Test
  void usesFreshCachedPreviewWithoutCallingApple() {
    var cached = mock(RecordingListeningAsset.class);
    when(cached.isFresh(now)).thenReturn(true);
    when(cached.getStatus()).thenReturn(ListeningAssetStatus.AVAILABLE);
    when(cached.getPreviewUrl()).thenReturn("https://audio-ssl.itunes.apple.com/preview.m4a");
    when(cached.getProviderTrackUrl()).thenReturn("https://music.apple.com/cn/track");
    when(assets.findByRecordingIdAndProviderAndStorefront(recordingId, "APPLE_ITUNES", "CN"))
        .thenReturn(Optional.of(cached));

    var result = service.get(recordingId);

    assertThat(result.status()).isEqualTo(ListeningAssetStatus.AVAILABLE);
    assertThat(result.preview().url()).contains("itunes.apple.com");
    assertThat(result.platformLinks()).singleElement().satisfies(link ->
        assertThat(link.url()).contains("music.163.com"));
    verifyNoInteractions(apple);
  }

  @Test
  void resolvesAndCachesPreviewOnFirstRequest() {
    when(assets.findByRecordingIdAndProviderAndStorefront(recordingId, "APPLE_ITUNES", "CN"))
        .thenReturn(Optional.empty());
    when(apple.findPreview("南国的孩子", "张悬／安溥", "城市")).thenReturn(Optional.of(
        new AppleItunesClient.Match("317557863", "南国的孩子", "张悬", "城市",
            "https://audio-ssl.itunes.apple.com/preview.m4a",
            "https://music.apple.com/cn/track")));

    var result = service.get(recordingId);

    assertThat(result.status()).isEqualTo(ListeningAssetStatus.AVAILABLE);
    assertThat(result.preview().attribution()).contains("Apple");
    verify(assets).save(argThat(asset -> asset.getStatus() == ListeningAssetStatus.AVAILABLE));
  }

  @Test
  void degradesToSearchLinkWhenPreviewCannotBeVerified() {
    when(assets.findByRecordingIdAndProviderAndStorefront(recordingId, "APPLE_ITUNES", "CN"))
        .thenReturn(Optional.empty());
    when(apple.findPreview(anyString(), anyString(), anyString())).thenReturn(Optional.empty());

    var result = service.get(recordingId);

    assertThat(result.status()).isEqualTo(ListeningAssetStatus.UNAVAILABLE);
    assertThat(result.preview()).isNull();
    assertThat(result.platformLinks()).singleElement().satisfies(link ->
        assertThat(link.label()).isEqualTo("去网易云搜索"));
  }
}

