package com.indiesoundquest.listening;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.ibm.icu.text.Transliterator;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.time.Duration;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class AppleItunesClient {
  private static final ThreadLocal<Transliterator> TO_SIMPLIFIED =
      ThreadLocal.withInitial(() -> Transliterator.getInstance("Traditional-Simplified"));
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient;
  private final String baseUrl;
  private final String storefront;
  private final String userAgent;

  public AppleItunesClient(
      ObjectMapper objectMapper,
      @Value("${listening.apple.base-url:https://itunes.apple.com/search}") String baseUrl,
      @Value("${listening.apple.storefront:CN}") String storefront,
      @Value("${listening.apple.user-agent:IndieSoundQuest/0.1}") String userAgent) {
    this.objectMapper = objectMapper;
    this.baseUrl = baseUrl;
    this.storefront = storefront;
    this.userAgent = userAgent;
    this.httpClient = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(4))
        .followRedirects(HttpClient.Redirect.NORMAL)
        .build();
  }

  public Optional<Match> findPreview(String title, String artistName, String albumTitle) {
    try {
      var query = URLEncoder.encode(artistName + " " + title, StandardCharsets.UTF_8);
      var uri = URI.create(baseUrl + "?term=" + query + "&country="
          + URLEncoder.encode(storefront, StandardCharsets.UTF_8)
          + "&media=music&entity=song&limit=10&explicit=No");
      var request = HttpRequest.newBuilder(uri)
          .timeout(Duration.ofSeconds(7))
          .header("Accept", "application/json")
          .header("User-Agent", userAgent)
          .GET()
          .build();
      var response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() != 200) return Optional.empty();
      return selectMatch(objectMapper.readTree(response.body()), title, artistName, albumTitle);
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      return Optional.empty();
    } catch (Exception exception) {
      return Optional.empty();
    }
  }

  static Optional<Match> selectMatch(JsonNode root, String title, String artistName, String albumTitle) {
    var candidates = new ArrayList<ScoredMatch>();
    for (var node : root.path("results")) {
      var trackName = text(node, "trackName");
      var matchedArtist = text(node, "artistName");
      var matchedAlbum = text(node, "collectionName");
      var previewUrl = text(node, "previewUrl");
      var trackViewUrl = text(node, "trackViewUrl");
      var trackId = text(node, "trackId");
      if (trackName == null || matchedArtist == null || trackId == null) continue;
      if (!normalize(title).equals(normalize(trackName))) continue;
      if (!artistMatches(artistName, matchedArtist)) continue;
      if (!isAllowedPreviewUrl(previewUrl) || !isAllowedTrackUrl(trackViewUrl)) continue;
      var albumScore = albumTitle != null && matchedAlbum != null
          && normalize(albumTitle).equals(normalize(matchedAlbum)) ? 10 : 0;
      candidates.add(new ScoredMatch(
          new Match(trackId, trackName, matchedArtist, matchedAlbum, previewUrl, trackViewUrl),
          100 + albumScore));
    }
    candidates.sort(Comparator.comparingInt(ScoredMatch::score).reversed());
    if (candidates.isEmpty()) return Optional.empty();
    if (candidates.size() > 1 && candidates.get(0).score() == candidates.get(1).score()
        && !candidates.get(0).match().trackId().equals(candidates.get(1).match().trackId())) {
      return Optional.empty();
    }
    return Optional.of(candidates.get(0).match());
  }

  private static boolean artistMatches(String expected, String actual) {
    var normalizedActual = normalize(actual);
    if (normalize(expected).equals(normalizedActual)) return true;
    return Arrays.stream(expected.split("[/／]"))
        .map(AppleItunesClient::normalize)
        .anyMatch(normalizedActual::equals);
  }

  private static boolean isAllowedPreviewUrl(String value) {
    return allowedHttpsHost(value, host -> host.endsWith(".itunes.apple.com"));
  }

  private static boolean isAllowedTrackUrl(String value) {
    return allowedHttpsHost(value, host -> host.equals("music.apple.com") || host.equals("itunes.apple.com"));
  }

  private static boolean allowedHttpsHost(String value, java.util.function.Predicate<String> hostRule) {
    if (value == null) return false;
    try {
      var uri = URI.create(value);
      return "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null
          && hostRule.test(uri.getHost().toLowerCase(Locale.ROOT));
    } catch (IllegalArgumentException exception) {
      return false;
    }
  }

  private static String text(JsonNode node, String field) {
    var value = node.path(field);
    if (value.isMissingNode() || value.isNull()) return null;
    var text = value.asText().trim();
    return text.isBlank() ? null : text;
  }

  private static String normalize(String value) {
    if (value == null) return "";
    return TO_SIMPLIFIED.get().transliterate(Normalizer.normalize(value, Normalizer.Form.NFKC))
        .toLowerCase(Locale.ROOT)
        .replaceAll("[\\p{P}\\p{S}\\s]", "");
  }

  public record Match(String trackId, String trackName, String artistName, String albumTitle,
                      String previewUrl, String trackViewUrl) {}
  private record ScoredMatch(Match match, int score) {}
}

