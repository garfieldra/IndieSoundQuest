package com.indiesoundquest.listening;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class AppleItunesClientTest {
  private final ObjectMapper json = new ObjectMapper();

  @Test
  void acceptsExactTitleAndExplicitArtistAlias() throws Exception {
    var root = json.readTree("""
        {"results":[{"trackId":317557863,"trackName":"南国的孩子","artistName":"张悬",
        "collectionName":"城市","previewUrl":"https://audio-ssl.itunes.apple.com/preview.m4a",
        "trackViewUrl":"https://music.apple.com/cn/album/example"}]}
        """);

    var result = AppleItunesClient.selectMatch(root, "南国的孩子", "张悬／安溥", "城市");

    assertThat(result).isPresent();
    assertThat(result.orElseThrow().trackId()).isEqualTo("317557863");
  }

  @Test
  void rejectsAmbiguousVersionsWithoutAlbumEvidence() throws Exception {
    var root = json.readTree("""
        {"results":[
          {"trackId":1,"trackName":"测试歌","artistName":"测试艺人","collectionName":"版本一",
           "previewUrl":"https://audio-ssl.itunes.apple.com/one.m4a","trackViewUrl":"https://music.apple.com/cn/one"},
          {"trackId":2,"trackName":"测试歌","artistName":"测试艺人","collectionName":"版本二",
           "previewUrl":"https://audio-ssl.itunes.apple.com/two.m4a","trackViewUrl":"https://music.apple.com/cn/two"}
        ]}
        """);

    assertThat(AppleItunesClient.selectMatch(root, "测试歌", "测试艺人", null)).isEmpty();
  }

  @Test
  void rejectsNonApplePreviewHost() throws Exception {
    var root = json.readTree("""
        {"results":[{"trackId":1,"trackName":"测试歌","artistName":"测试艺人",
        "previewUrl":"https://example.com/audio.m4a","trackViewUrl":"https://music.apple.com/cn/one"}]}
        """);

    assertThat(AppleItunesClient.selectMatch(root, "测试歌", "测试艺人", null)).isEmpty();
  }
}

