package com.indiesoundquest.internal;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class MusicBrainzCatalogServiceTest {
  private final ObjectMapper objectMapper = new ObjectMapper();

  @Test
  void recordingResearchUsesExplicitArtistAndTitleFields() {
    assertThat(MusicBrainzCatalogService.researchLuceneQuery(
        "recording", "张悬 宝贝", "张悬", "宝贝"))
        .isEqualTo("recording:\"宝贝\" AND artist:\"张悬\"");
  }

  @Test
  void exactResearchContractRejectsSameTitleFromWrongArtist() throws Exception {
    var wrongArtist = objectMapper.readTree("""
        {"id":"09a7d13b-f9a5-4b95-b42c-f416aaa73b34","title":"宝贝",
         "artist-credit":[{"name":"布仁巴雅尔","artist":{"name":"布仁巴雅尔"}}]}
        """);
    var correctArtist = objectMapper.readTree("""
        {"id":"541523a2-3277-4bdb-9d8e-2ed06ea6115e","title":"寶貝",
         "artist-credit":[{"name":"張懸","artist":{"name":"張懸"}}]}
        """);

    assertThat(MusicBrainzCatalogService.matchesResearchContract(
        "recording", wrongArtist, "张悬", "宝贝")).isFalse();
    assertThat(MusicBrainzCatalogService.matchesResearchContract(
        "recording", correctArtist, "张悬", "宝贝")).isTrue();
  }
}
