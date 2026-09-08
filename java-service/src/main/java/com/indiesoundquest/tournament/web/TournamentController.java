package com.indiesoundquest.tournament.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.redis.RedisRateLimitService;
import com.indiesoundquest.redis.TournamentStateCacheService;
import com.indiesoundquest.tournament.application.TournamentApplicationService;
import com.indiesoundquest.tournament.domain.*;
import com.indiesoundquest.tournament.repository.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.*;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class TournamentController {
  private final ArtistRepository artists;
  private final RecordingRepository recordings;
  private final TournamentRepository tournaments;
  private final TournamentEntryRepository entries;
  private final TournamentMatchRepository matches;
  private final TournamentApplicationService service;
  private final TournamentStateCacheService tournamentState;
  private final RedisRateLimitService rateLimit;
  private final ObjectMapper objectMapper;

  public TournamentController(
      ArtistRepository artists,
      RecordingRepository recordings,
      TournamentRepository tournaments,
      TournamentEntryRepository entries,
      TournamentMatchRepository matches,
      TournamentApplicationService service,
      TournamentStateCacheService tournamentState,
      RedisRateLimitService rateLimit,
      ObjectMapper objectMapper) {
    this.artists = artists;
    this.recordings = recordings;
    this.tournaments = tournaments;
    this.entries = entries;
    this.matches = matches;
    this.service = service;
    this.tournamentState = tournamentState;
    this.rateLimit = rateLimit;
    this.objectMapper = objectMapper;
  }

  @GetMapping("/artists")
  List<Map<String, Object>> artists() {
    return artists.findAllByOrderBySortNameAsc().stream().map(a -> Map.<String, Object>of("id", a.getId(), "name", a.getName())).toList();
  }

  @PostMapping("/tournaments")
  ResponseEntity<Map<String, Object>> create(@RequestHeader("Idempotency-Key") String rawKey, @Valid @RequestBody CreateTournament body, HttpServletRequest request) {
    var guest = guest(request);
    rateLimit.assertTournamentCreateAllowed(guest.getId());
    var idempotencyKey = canonicalIdempotencyKey(rawKey);
    var source = body.candidateSource() == null ? CandidateSource.POPULAR : body.candidateSource();
    TournamentApplicationService.CreationResult result;
    if (source == CandidateSource.AGENT_GENERATED) {
      var ids = body.recordingIds() == null ? List.<UUID>of() : body.recordingIds();
      if (ids.size() != body.size() || new HashSet<>(ids).size() != body.size()) throw new IllegalArgumentException("agent recording ids are invalid");
      var loaded = recordings.findByIdInWithArtist(ids);
      if (loaded.size() != body.size()) throw new NoSuchElementException();
      var byId = new HashMap<UUID, Recording>();
      loaded.forEach(r -> byId.put(r.getId(), r));
      result = service.createAgentDraft(guest, ids.stream().map(byId::get).toList(), body.size(), body.explorationBrief(), idempotencyKey);
    } else {
      if (source != CandidateSource.POPULAR) throw new IllegalArgumentException("candidate source is not supported");
      if (body.artistId() == null) throw new IllegalArgumentException("artistId is required");
      var artist = artists.findById(body.artistId()).orElseThrow();
      var list = recordings.findByArtistIdOrderBySeedRankAsc(artist.getId()).stream().limit(body.size()).toList();
      result = service.createDraft(guest, artist, list, body.size(), idempotencyKey);
    }
    var t = result.tournament();
    return ResponseEntity.status(result.replayed() ? HttpStatus.OK : HttpStatus.CREATED)
        .header("Idempotent-Replayed", Boolean.toString(result.replayed()))
        .body(Map.of("id", t.getId(), "status", t.getStatus(), "size", t.getSize(), "candidateSource", t.getCandidateSource()));
  }

  @GetMapping("/tournaments/{id}")
  @org.springframework.transaction.annotation.Transactional(readOnly = true)
  TournamentDetailResponse get(@PathVariable UUID id, HttpServletRequest r) throws Exception {
    var guestId = guest(r).getId();
    tournaments.findByIdAndGuestSessionIdAndDeletedAtIsNull(id, guestId).orElseThrow();
    var cached = tournamentState.get(id);
    if (cached.isPresent()) {
      return objectMapper.readValue(cached.get(), TournamentDetailResponse.class);
    }
    var t = tournaments.findByIdAndGuestSessionIdAndDeletedAtIsNull(id, guestId).orElseThrow();
    var all = matches.findByTournamentIdOrderByRoundNumberAscMatchIndexAsc(id);
    var dto = all.stream().map(TournamentDetailResponse.Match::from).toList();
    var current = dto.stream().filter(m -> m.status() == MatchStatus.PENDING && m.leftEntryId() != null && m.rightEntryId() != null).findFirst().orElse(null);
    var response = new TournamentDetailResponse(
        id,
        t.getStatus(),
        t.getSize(),
        (int) all.stream().filter(m -> m.getStatus() == MatchStatus.COMPLETED).count(),
        t.getCompletedAt(),
        entries.findByTournamentId(id).stream().map(TournamentDetailResponse.Entry::from).toList(),
        dto,
        current);
    tournamentState.put(id, objectMapper.writeValueAsString(response));
    return response;
  }

  @PatchMapping("/tournaments/{id}")
  Map<String, Object> prepare(@PathVariable UUID id, @RequestBody UpdateTournament body, HttpServletRequest r) {
    var t = service.prepare(id, guest(r).getId());
    return Map.of("id", t.getId(), "status", t.getStatus());
  }

  @PostMapping("/tournament-matches/{id}/votes")
  Map<String, Object> vote(@PathVariable UUID id, @RequestHeader("Idempotency-Key") String key, @Valid @RequestBody VoteRequest body, HttpServletRequest r) {
    var guest = guest(r);
    rateLimit.assertVoteAllowed(guest.getId());
    var t = service.vote(id, body.selectedEntryId(), guest.getId(), key);
    return Map.of("tournamentId", t.getId(), "status", t.getStatus());
  }

  private String canonicalIdempotencyKey(String value) {
    try {
      return UUID.fromString(value).toString();
    } catch (Exception exception) {
      throw new IllegalArgumentException("Idempotency-Key must be a UUID");
    }
  }

  private GuestSession guest(HttpServletRequest r) {
    return (GuestSession) r.getAttribute(GuestIdentityFilter.ATTRIBUTE);
  }

  record CreateTournament(UUID artistId, @Min(16) @Max(32) int size, CandidateSource candidateSource, List<UUID> recordingIds, @Size(max = 1000) String explorationBrief) {}

  record UpdateTournament(@NotBlank String status) {}

  record VoteRequest(@NotNull UUID selectedEntryId) {}
}
