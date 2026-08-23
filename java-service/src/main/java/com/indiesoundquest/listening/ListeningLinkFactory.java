package com.indiesoundquest.listening;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

public final class ListeningLinkFactory {
  private ListeningLinkFactory() {}

  public static String neteaseSongSearch(String title, String artistName) {
    var term = URLEncoder.encode(artistName + " " + title, StandardCharsets.UTF_8);
    return "https://music.163.com/#/search/m/?s=" + term + "&type=1";
  }
}

