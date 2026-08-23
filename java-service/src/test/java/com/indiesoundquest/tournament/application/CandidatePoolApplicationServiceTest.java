package com.indiesoundquest.tournament.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.indiesoundquest.agent.CandidatePoolGateway;
import com.indiesoundquest.tournament.domain.Artist;
import com.indiesoundquest.tournament.domain.Recording;
import com.indiesoundquest.tournament.repository.ArtistRepository;
import com.indiesoundquest.tournament.repository.RecordingRepository;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class CandidatePoolApplicationServiceTest {
  private final ObjectMapper json = new ObjectMapper();

  @Mock CandidatePoolGateway gateway;
  @Mock ArtistRepository artists;
  @Mock RecordingRepository recordings;

  private CandidatePoolApplicationService service;

  @BeforeEach
  void setUp() {
    service = new CandidatePoolApplicationService(gateway, artists, recordings);
  }

  @ParameterizedTest
  @ValueSource(ints = {16, 32})
  void returnsCatalogFactsInAgentOrderForFullCandidatePool(int size) {
    var requestId = UUID.randomUUID();
    var ids = ids(size * 2);
    var result = readyResult(requestId, size, ids);
    var catalog = catalog(ids);
    when(gateway.generate(requestId, "guest", size, "温柔、有留白的中文独立音乐", List.of())).thenReturn(result);
    when(recordings.findByIdInWithArtist(ids)).thenReturn(catalog);

    var response = service.generate(requestId, "guest", size, "温柔、有留白的中文独立音乐", List.of());

    assertThat(response.status()).isEqualTo("ready_for_confirmation");
    assertThat(response.candidatePool().recordingIds()).containsExactlyElementsOf(ids);
    assertThat(response.candidatePool().items()).extracting(item -> item.recordingId()).containsExactlyElementsOf(ids);
    assertThat(response.candidatePool().reserveSize()).isEqualTo(size);
    assertThat(response.candidatePool().warnings()).isEmpty();
  }

  @Test
  void rejectsDuplicateRecordingIds() {
    var requestId = UUID.randomUUID();
    var ids = ids(16);
    ids.set(15, ids.get(0));
    when(gateway.generate(requestId, "guest", 16, "测试偏好", List.of())).thenReturn(readyResult(requestId, 16, ids));

    assertThatThrownBy(() -> service.generate(requestId, "guest", 16, "测试偏好", List.of()))
        .isInstanceOf(CandidatePoolContractException.class)
        .hasMessageContaining("重复");
    verify(recordings, never()).findByIdInWithArtist(ids);
  }

  @Test
  void rejectsUnknownCatalogRecording() {
    var requestId = UUID.randomUUID();
    var ids = ids(16);
    var partialCatalog = ids.subList(0, 15).stream().map(ignored -> mock(Recording.class)).toList();
    when(gateway.generate(requestId, "guest", 16, "测试偏好", List.of())).thenReturn(readyResult(requestId, 16, ids));
    when(recordings.findByIdInWithArtist(ids)).thenReturn(partialCatalog);

    assertThatThrownBy(() -> service.generate(requestId, "guest", 16, "测试偏好", List.of()))
        .isInstanceOf(CandidatePoolContractException.class)
        .hasMessageContaining("不存在");
  }

  @Test
  void returnsNormalizedInsufficientResultWithoutLeakingPartialIds() {
    var requestId = UUID.randomUUID();
    var result = json.createObjectNode();
    result.put("requestId", requestId.toString());
    result.put("status", "insufficient_candidates");
    result.put("size", 32);
    result.put("candidateSummary", "现有可验证歌曲不足。 ");
    result.putArray("recordingIds").add(UUID.randomUUID().toString());
    when(gateway.generate(requestId, "guest", 32, "测试偏好", List.of())).thenReturn(result);

    var response = service.generate(requestId, "guest", 32, "测试偏好", List.of());

    assertThat(response.status()).isEqualTo("insufficient_candidates");
    assertThat(response.candidatePool().recordingIds()).isEmpty();
    assertThat(response.candidatePool().items()).isEmpty();
    assertThat(response.candidatePool().warnings()).extracting(warning -> warning.code())
        .containsExactly("INSUFFICIENT_CANDIDATES");
    verify(recordings, never()).findByIdInWithArtist(org.mockito.ArgumentMatchers.anyList());
  }

  @Test
  void rejectsResponseForAnotherRequest() {
    var requestId = UUID.randomUUID();
    when(gateway.generate(requestId, "guest", 16, "测试偏好", List.of()))
        .thenReturn(readyResult(UUID.randomUUID(), 16, ids(16)));

    assertThatThrownBy(() -> service.generate(requestId, "guest", 16, "测试偏好", List.of()))
        .isInstanceOf(CandidatePoolContractException.class)
        .hasMessageContaining("不属于本次请求");
  }

  @Test
  void rejectsRecordingOutsideLockedArtistScope() {
    var requestId = UUID.randomUUID();
    var allowedArtistId = UUID.randomUUID();
    var otherArtistId = UUID.randomUUID();
    var ids = ids(16);
    var result = readyResult(requestId, 16, ids);
    var policy = result.putObject("intentPolicy");
    policy.put("intentMode", "ARTIST_LOCKED");
    policy.putArray("allowedArtistIds").add(allowedArtistId.toString());
    policy.putArray("allowedArtistNames");
    var catalog = new ArrayList<Recording>();
    for (var index = 0; index < ids.size(); index++) {
      var artist = mock(Artist.class);
      when(artist.getId()).thenReturn(index == ids.size() - 1 ? otherArtistId : allowedArtistId);
      if (index == ids.size() - 1) when(artist.getName()).thenReturn("Other");
      var recording = mock(Recording.class);
      when(recording.getId()).thenReturn(ids.get(index));
      when(recording.getArtist()).thenReturn(artist);
      catalog.add(recording);
    }
    when(gateway.generate(requestId, "guest", 16, "只玩这个歌手的歌曲世界杯", List.of(allowedArtistId))).thenReturn(result);
    when(artists.existsById(allowedArtistId)).thenReturn(true);
    when(recordings.findByIdInWithArtist(ids)).thenReturn(catalog);

    assertThatThrownBy(() -> service.generate(
        requestId, "guest", 16, "只玩这个歌手的歌曲世界杯", List.of(allowedArtistId)))
        .isInstanceOf(CandidatePoolContractException.class)
        .hasMessageContaining("限定艺人范围之外");
  }

  private ObjectNode readyResult(UUID requestId, int size, List<UUID> ids) {
    var result = json.createObjectNode();
    result.put("requestId", requestId.toString());
    result.put("status", "ready_for_confirmation");
    result.put("size", size);
    result.put("candidateSummary", "一组经过验证的候选歌曲");
    var recordingIds = result.putArray("recordingIds");
    var items = result.putArray("items");
    ids.forEach(id -> {
      recordingIds.add(id.toString());
      var item = items.addObject();
      item.put("recordingId", id.toString());
      item.put("reason", "符合本次兴趣方向");
    });
    result.putArray("warnings");
    return result;
  }

  private List<UUID> ids(int count) {
    var result = new ArrayList<UUID>();
    for (var index = 0; index < count; index++) result.add(UUID.randomUUID());
    return result;
  }

  private List<Recording> catalog(List<UUID> ids) {
    var artist = mock(Artist.class);
    when(artist.getName()).thenReturn("测试艺人");
    return ids.stream().map(id -> {
      var recording = mock(Recording.class);
      when(recording.getId()).thenReturn(id);
      when(recording.getTitle()).thenReturn("测试歌曲 " + id.toString().substring(0, 4));
      when(recording.getArtist()).thenReturn(artist);
      when(recording.getCoverStatus()).thenReturn("UNAVAILABLE");
      return recording;
    }).toList();
  }
}
