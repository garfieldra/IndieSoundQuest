package com.indiesoundquest.internal;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.ibm.icu.text.Transliterator;
import com.indiesoundquest.tournament.domain.Artist;
import com.indiesoundquest.tournament.domain.Recording;
import com.indiesoundquest.tournament.repository.ArtistRepository;
import com.indiesoundquest.tournament.repository.RecordingRepository;
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
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class MusicBrainzCatalogService {
  private static final long MIN_INTERVAL_MILLIS = 1_050;
  private static final ThreadLocal<Transliterator> TO_SIMPLIFIED =
      ThreadLocal.withInitial(() -> Transliterator.getInstance("Traditional-Simplified"));
  private final ArtistRepository artists;
  private final RecordingRepository recordings;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient;
  private final String baseUrl;
  private final String userAgent;
  private long lastRequestAt;

  public MusicBrainzCatalogService(
      ArtistRepository artists,
      RecordingRepository recordings,
      ObjectMapper objectMapper,
      @Value("${musicbrainz.base-url:https://musicbrainz.org/ws/2}") String baseUrl,
      @Value("${musicbrainz.user-agent:IndieSoundQuest/0.1 (https://github.com/garfieldra/IndieSoundQuest)}") String userAgent) {
    this.artists = artists;
    this.recordings = recordings;
    this.objectMapper = objectMapper;
    this.baseUrl = baseUrl.replaceAll("/$", "");
    this.userAgent = userAgent;
    this.httpClient = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(12))
        .followRedirects(HttpClient.Redirect.NORMAL)
        .build();
  }

  @Transactional
  public List<Resolution> resolveAndImport(List<Hint> hints) {
    var results = new ArrayList<Resolution>();
    var nextSeedRank = recordings.findMaxSeedRank() + 1;
    // A bounded batch; the ReAct supervisor may plan more batches from outcome evidence.
    for (var hint : hints.stream().limit(16).toList()) {
      try {
        var outcome = search(hint);
        if (outcome.candidate().isEmpty()) {
          results.add(Resolution.unresolved(hint, outcome.reason()));
          continue;
        }
        var candidate = outcome.candidate().get();
        var existing = recordings.findByMusicbrainzMbid(candidate.recordingMbid());
        if (existing.isPresent()) {
          existing.get().attachExternalDiscovery(hint.sourceUrl());
          results.add(Resolution.resolved(hint, existing.get(), candidate.score(), false));
          continue;
        }
        var artist = artists.findByMusicbrainzMbid(candidate.artistMbid())
            .or(() -> artists.findFirstByNameIgnoreCase(candidate.artistName()))
            .orElseGet(() -> artists.save(Artist.imported(candidate.artistName(), candidate.artistSortName(), candidate.artistMbid())));
        artist.attachMusicbrainzIdentity(candidate.artistMbid());
        var sameLocalRecording = recordings.findFirstByArtistIdAndTitleIgnoreCase(artist.getId(), candidate.title());
        if (sameLocalRecording.isPresent()) {
          var recording = sameLocalRecording.get();
          recording.attachMusicbrainzIdentity(candidate.recordingMbid(), candidate.releaseMbid(), candidate.albumTitle());
          recording.attachExternalDiscovery(hint.sourceUrl());
          results.add(Resolution.resolved(hint, recording, candidate.score(), false));
          continue;
        }
        var saved = recordings.save(Recording.imported(
            artist, candidate.title(), candidate.albumTitle(), nextSeedRank++,
            candidate.recordingMbid(), candidate.releaseMbid(), hint.sourceUrl()));
        results.add(Resolution.resolved(hint, saved, candidate.score(), true));
      } catch (InterruptedException exception) {
        Thread.currentThread().interrupt();
        results.add(Resolution.unresolved(hint, "MUSICBRAINZ_INTERRUPTED"));
      } catch (Exception exception) {
        results.add(Resolution.unresolved(hint, "MUSICBRAINZ_UNAVAILABLE"));
      }
    }
    return results;
  }

  public List<ArtistResolution> resolveArtistCandidates(List<String> names) {
    var results = new ArrayList<ArtistResolution>();
    for (var rawName : names.stream().filter(Objects::nonNull).map(String::trim).filter(value -> !value.isBlank()).distinct().limit(8).toList()) {
      try {
        // A number of globally known artists are catalogued under a stage-name
        // change while the user's wording lives in an alias (for example Ye /
        // Kanye West). Search both fields before offering a clarification card.
        var escapedName = escapeLucene(rawName);
        var query = "(artist:\"" + escapedName + "\" OR alias:\"" + escapedName + "\")";
        var uri = URI.create(baseUrl + "/artist?fmt=json&limit=20&query=" + URLEncoder.encode(query, StandardCharsets.UTF_8));
        var response = send(uri);
        if (response.statusCode() != 200) { results.add(new ArtistResolution(rawName, List.of(), "MUSICBRAINZ_UNAVAILABLE")); continue; }
        var candidates = new ArrayList<ArtistCandidate>();
        for (var node : objectMapper.readTree(response.body()).path("artists")) {
          var id = text(node, "id"); var name = text(node, "name");
          if (id == null || name == null) continue;
          candidates.add(new ArtistCandidate(id, name, Optional.ofNullable(text(node, "sort-name")).orElse(name), text(node, "country"), text(node, "type"), text(node, "disambiguation"), text(node.path("life-span"), "begin"), text(node.path("life-span"), "end"), node.path("score").asInt(0)));
        }
        candidates.sort(Comparator.comparingInt(ArtistCandidate::score).reversed());
        results.add(new ArtistResolution(rawName, candidates, candidates.isEmpty() ? "NO_ARTIST_MATCH" : null));
      } catch (InterruptedException exception) {
        Thread.currentThread().interrupt(); results.add(new ArtistResolution(rawName, List.of(), "MUSICBRAINZ_INTERRUPTED"));
      } catch (Exception exception) { results.add(new ArtistResolution(rawName, List.of(), "MUSICBRAINZ_UNAVAILABLE")); }
    }
    return results;
  }

  /**
   * Retrieves canonical recordings directly from MusicBrainz for already-resolved artists.
   * This avoids turning a 32-song pool into dozens of sequential title lookups while keeping
   * MusicBrainz as the sole identity authority.
   */
  @Transactional
  public List<Resolution> discoverArtistRecordings(List<ArtistSeed> seeds, int perArtistLimit) {
    var results = new ArrayList<Resolution>();
    var seen = new HashSet<String>();
    var nextSeedRank = recordings.findMaxSeedRank() + 1;
    for (var seed : seeds.stream().filter(Objects::nonNull).limit(8).toList()) {
      try {
        var uri = URI.create(baseUrl + "/recording?fmt=json&limit=" + Math.min(Math.max(perArtistLimit, 1), 100)
            + "&query=" + URLEncoder.encode("arid:" + seed.mbid(), StandardCharsets.UTF_8));
        var response = send(uri);
        if (response.statusCode() != 200) continue;
        for (var node : objectMapper.readTree(response.body()).path("recordings")) {
          var candidate = parseCandidate(node);
          if (candidate == null || !seed.mbid().equals(candidate.artistMbid()) || !seen.add(candidate.recordingMbid())) continue;
          var hint = new Hint(candidate.title(), candidate.artistName(), "https://musicbrainz.org/artist/" + seed.mbid());
          var existing = recordings.findByMusicbrainzMbid(candidate.recordingMbid());
          if (existing.isPresent()) {
            existing.get().attachExternalDiscovery(hint.sourceUrl());
            results.add(Resolution.resolved(hint, existing.get(), candidate.score(), false));
            continue;
          }
          var artist = artists.findByMusicbrainzMbid(candidate.artistMbid())
              .or(() -> artists.findFirstByNameIgnoreCase(candidate.artistName()))
              .orElseGet(() -> artists.save(Artist.imported(candidate.artistName(), candidate.artistSortName(), candidate.artistMbid())));
          artist.attachMusicbrainzIdentity(candidate.artistMbid());
          var sameTitle = recordings.findFirstByArtistIdAndTitleIgnoreCase(artist.getId(), candidate.title());
          if (sameTitle.isPresent()) {
            var recording = sameTitle.get();
            recording.attachMusicbrainzIdentity(candidate.recordingMbid(), candidate.releaseMbid(), candidate.albumTitle());
            recording.attachExternalDiscovery(hint.sourceUrl());
            results.add(Resolution.resolved(hint, recording, candidate.score(), false));
            continue;
          }
          var saved = recordings.save(Recording.imported(artist, candidate.title(), candidate.albumTitle(), nextSeedRank++, candidate.recordingMbid(), candidate.releaseMbid(), hint.sourceUrl()));
          results.add(Resolution.resolved(hint, saved, candidate.score(), true));
        }
      } catch (Exception ignored) {
        // The agent receives partial verified results and can decide whether further web discovery is useful.
      }
    }
    return results;
  }

  private SearchOutcome search(Hint hint) throws Exception {
    if (hint.title() == null || hint.title().isBlank() || hint.artistName() == null || hint.artistName().isBlank()) {
      return SearchOutcome.unresolved("INVALID_HINT");
    }
    var luceneQuery = "recording:\"" + escapeLucene(hint.title()) + "\" AND artist:\"" + escapeLucene(hint.artistName()) + "\"";
    var uri = URI.create(baseUrl + "/recording?fmt=json&limit=10&query="
        + URLEncoder.encode(luceneQuery, StandardCharsets.UTF_8));
    var response = send(uri);
    if (response.statusCode() != 200) return SearchOutcome.unresolved("MUSICBRAINZ_UNAVAILABLE");
    var root = objectMapper.readTree(response.body());
    var candidates = new ArrayList<Candidate>();
    for (var node : root.path("recordings")) {
      var parsed = parseCandidate(node);
      if (parsed != null && isConfidentMatch(hint, parsed)) candidates.add(parsed);
    }
    candidates.sort(Comparator.comparingInt(Candidate::score).reversed());
    if (candidates.isEmpty()) return SearchOutcome.unresolved("NO_CONFIDENT_MATCH");
    // Multiple releases/editions often expose distinct recording MBIDs for the
    // same exact title and credited artist.  That is version choice, not an
    // artist/song identity ambiguity; select the highest scored verified record.
    if (candidates.size() > 1 && candidates.get(0).score() - candidates.get(1).score() < 3
        && (!normalize(candidates.get(0).title()).equals(normalize(candidates.get(1).title()))
            || !normalize(candidates.get(0).artistName()).equals(normalize(candidates.get(1).artistName())))) {
      return SearchOutcome.unresolved("AMBIGUOUS_MATCH");
    }
    // The search response is itself a MusicBrainz canonical recording document
    // and already contains the recording MBID plus credited artist MBID.  Once
    // the exact normalized title/artist and score checks above pass, a second
    // per-recording lookup repeats the same authority check while halving the
    // usable throughput under MusicBrainz's one-request-per-second etiquette.
    // Keeping this single authoritative response lets an online-first 32-song
    // pool finish within its workflow budget without weakening identity rules.
    return SearchOutcome.resolved(candidates.get(0));
  }

  private Optional<Candidate> lookup(Candidate searched) throws Exception {
    var uri = URI.create(baseUrl + "/recording/" + searched.recordingMbid()
        + "?fmt=json&inc=artist-credits%2Breleases");
    var response = send(uri);
    if (response.statusCode() != 200) return Optional.empty();
    var lookedUp = parseCandidate(objectMapper.readTree(response.body()));
    if (lookedUp == null) return Optional.empty();
    var verified = new Candidate(
        lookedUp.recordingMbid(), lookedUp.title(), lookedUp.artistMbid(), lookedUp.artistName(),
        lookedUp.artistSortName(), lookedUp.releaseMbid(), lookedUp.albumTitle(), searched.score());
    if (!searched.recordingMbid().equals(verified.recordingMbid())) return Optional.empty();
    if (!normalize(searched.title()).equals(normalize(verified.title()))) return Optional.empty();
    if (!searched.artistMbid().equals(verified.artistMbid())) return Optional.empty();
    return Optional.of(verified);
  }

  private Candidate parseCandidate(JsonNode node) {
    var artistCredit = node.path("artist-credit");
    if (!artistCredit.isArray() || artistCredit.isEmpty()) return null;
    var credit = artistCredit.get(0);
    var artistNode = credit.path("artist");
    var recordingMbid = text(node, "id");
    var title = text(node, "title");
    var artistMbid = text(artistNode, "id");
    var artistName = Optional.ofNullable(text(credit, "name")).orElse(text(artistNode, "name"));
    var sortName = Optional.ofNullable(text(artistNode, "sort-name")).orElse(artistName);
    if (recordingMbid == null || title == null || artistMbid == null || artistName == null) return null;
    String releaseMbid = null;
    String albumTitle = null;
    var releases = node.path("releases");
    if (releases.isArray() && !releases.isEmpty()) {
      releaseMbid = text(releases.get(0), "id");
      albumTitle = text(releases.get(0), "title");
    }
    return new Candidate(recordingMbid, title, artistMbid, artistName, sortName, releaseMbid, albumTitle, node.path("score").asInt(0));
  }

  private boolean isConfidentMatch(Hint hint, Candidate candidate) {
    if (candidate.score() < 90) return false;
    var titleMatch = normalize(hint.title()).equals(normalize(candidate.title()));
    var artistMatch = normalize(hint.artistName()).equals(normalize(candidate.artistName()));
    return titleMatch && artistMatch;
  }

  private synchronized HttpResponse<String> send(URI uri) throws Exception {
    HttpResponse<String> response = null;
    // A discovery run contains other recovery paths (web search and subsequent ReAct turns).
    // Do not let one unresponsive upstream request consume a whole user-facing run.
    for (var attempt = 0; attempt < 2; attempt++) {
      var wait = MIN_INTERVAL_MILLIS - (System.currentTimeMillis() - lastRequestAt);
      if (wait > 0) Thread.sleep(wait);
      var request = HttpRequest.newBuilder(uri)
          .timeout(Duration.ofSeconds(8))
          .header("Accept", "application/json")
          .header("User-Agent", userAgent)
          .GET()
          .build();
      try {
        response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
      } catch (java.io.IOException exception) {
        lastRequestAt = System.currentTimeMillis();
        if (attempt == 1) throw exception;
        Thread.sleep((attempt + 1L) * 1_000);
        continue;
      } finally {
        lastRequestAt = System.currentTimeMillis();
      }
      if (response.statusCode() != 429 && response.statusCode() != 503) return response;
      var retryAfter = response.headers().firstValue("Retry-After").flatMap(value -> {
        try { return Optional.of(Long.parseLong(value)); } catch (NumberFormatException ignored) { return Optional.empty(); }
      }).orElse(1L << attempt);
      Thread.sleep(Math.min(retryAfter, 4) * 1_000);
    }
    return response;
  }

  private static String text(JsonNode node, String field) {
    var value = node.path(field);
    return value.isMissingNode() || value.isNull() || value.asText().isBlank() ? null : value.asText();
  }

  private static String normalize(String value) {
    return TO_SIMPLIFIED.get().transliterate(Normalizer.normalize(value, Normalizer.Form.NFKC))
        .toLowerCase(Locale.ROOT)
        .replaceAll("[\\p{P}\\p{S}\\s]", "");
  }

  private static String escapeLucene(String value) {
    return value.replace("\\", "\\\\").replace("\"", "\\\"");
  }

  public record Hint(String title, String artistName, String sourceUrl) {}
  public record ArtistSeed(String mbid, String name) {}
  public record ArtistCandidate(String mbid, String name, String sortName, String country, String type, String disambiguation, String begin, String end, int score) {}
  public record ArtistResolution(String mention, List<ArtistCandidate> candidates, String reason) {}
  private record Candidate(String recordingMbid, String title, String artistMbid, String artistName,
                           String artistSortName, String releaseMbid, String albumTitle, int score) {}
  private record SearchOutcome(Optional<Candidate> candidate, String reason) {
    static SearchOutcome resolved(Candidate candidate) { return new SearchOutcome(Optional.of(candidate), null); }
    static SearchOutcome unresolved(String reason) { return new SearchOutcome(Optional.empty(), reason); }
  }
  public record Resolution(String title, String artistName, String sourceUrl, String status,
                           UUID recordingId, UUID artistId, String recordingMbid, String albumTitle,
                           String coverStatus, String catalogSource, String trustState, int score, boolean imported, String reason) {
    static Resolution resolved(Hint hint, Recording recording, int score, boolean imported) {
      return new Resolution(recording.getTitle(), recording.getArtist().getName(), hint.sourceUrl(), "RESOLVED",
          recording.getId(), recording.getArtist().getId(), recording.getMusicbrainzMbid(), recording.getAlbumTitle(),
          Optional.ofNullable(recording.getCoverStatus()).orElse("UNAVAILABLE"), recording.getCatalogSource(), "CATALOG_IMPORTED", score, imported, null);
    }
    static Resolution unresolved(Hint hint, String reason) {
      var trustState = "AMBIGUOUS_MATCH".equals(reason) ? "MB_AMBIGUOUS" : "REJECTED";
      return new Resolution(hint.title(), hint.artistName(), hint.sourceUrl(), "UNRESOLVED", null, null, null,
          null, null, null, trustState, 0, false, reason);
    }
  }
}
