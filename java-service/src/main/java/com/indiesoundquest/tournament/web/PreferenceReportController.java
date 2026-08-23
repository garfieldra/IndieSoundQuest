package com.indiesoundquest.tournament.web;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.tournament.application.PreferenceReportApplicationService;
import com.indiesoundquest.tournament.domain.*;
import com.indiesoundquest.tournament.repository.*;
import jakarta.servlet.http.HttpServletRequest;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.http.*;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class PreferenceReportController {
  private final TournamentRepository tournaments;
  private final TournamentPreferenceReportRepository reports;
  private final PreferenceReportApplicationService service;
  private final RecordingRepository recordings;
  private final ArtistRepository artists;
  private final ObjectMapper objectMapper;

  public PreferenceReportController(TournamentRepository tournaments, TournamentPreferenceReportRepository reports,
      PreferenceReportApplicationService service, RecordingRepository recordings, ArtistRepository artists, ObjectMapper objectMapper) {
    this.tournaments=tournaments; this.reports=reports; this.service=service; this.recordings=recordings; this.artists=artists; this.objectMapper=objectMapper;
  }

  @PostMapping("/tournaments/{tournamentId}/preference-report")
  ResponseEntity<Map<String,Object>> create(@PathVariable UUID tournamentId, @RequestBody(required=false) CreateBody body, HttpServletRequest request) {
    var guest=guest(request); var tournament=ownedCompletedTournament(tournamentId, guest.getId());
    var latest=reports.findByTournament_IdOrderByVersionNumberDesc(tournamentId).stream().findFirst().orElse(null);
    boolean force=body!=null && body.force();
    if(latest!=null && latest.getStatus()==PreferenceReportStatus.READY && !force) return ResponseEntity.ok(view(latest));
    if(latest!=null && (latest.getStatus()==PreferenceReportStatus.PENDING || latest.getStatus()==PreferenceReportStatus.RUNNING) && !force) return ResponseEntity.accepted().body(view(latest));
    int version=latest==null?1:latest.getVersionNumber()+1;
    var report=reports.save(TournamentPreferenceReport.pending(UUID.randomUUID(),tournament,version));
    service.startAsync(report.getId(),tournamentId,guest.getId(),version);
    return ResponseEntity.accepted().body(view(report));
  }

  @GetMapping("/tournaments/{tournamentId}/preference-report")
  @Transactional(readOnly=true)
  Map<String,Object> get(@PathVariable UUID tournamentId,HttpServletRequest request) {
    ownedCompletedTournament(tournamentId, guest(request).getId());
    var report=reports.findByTournament_IdOrderByVersionNumberDesc(tournamentId).stream().findFirst()
        .orElseThrow(()->new org.springframework.web.server.ResponseStatusException(HttpStatus.NOT_FOUND));
    return view(report);
  }

  private Tournament ownedCompletedTournament(UUID tournamentId, UUID guestId) {
    var tournament=tournaments.findByIdAndGuestSessionIdAndDeletedAtIsNull(tournamentId,guestId).orElseThrow();
    if(tournament.getStatus()!=TournamentStatus.COMPLETED) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.CONFLICT,"tournament is not completed");
    return tournament;
  }

  private Map<String,Object> view(TournamentPreferenceReport report) {
    var result=new LinkedHashMap<String,Object>();
    result.put("reportId",report.getId()); result.put("tournamentId",report.getTournamentId()); result.put("version",report.getVersionNumber()); result.put("status",report.getStatus());
    if(report.getReportJson()!=null) {
      try { result.put("report", enrich(objectMapper.readTree(report.getReportJson()))); }
      catch(Exception ex) { result.put("report", objectMapper.createObjectNode().put("warning", "REPORT_PRESENTATION_ENRICHMENT_FAILED")); }
    }
    if(report.getFailureMessage()!=null) result.put("failureMessage",report.getFailureMessage());
    return result;
  }

  private JsonNode enrich(JsonNode report) {
    var songs=report.path("songRecommendations");
    if(songs.isArray()) for(var song:songs) enrichSong((ObjectNode)song);
    var artistRecommendations=report.path("artistRecommendations");
    if(artistRecommendations.isArray()) for(var artist:artistRecommendations) enrichArtist((ObjectNode)artist);
    return report;
  }

  private void enrichSong(ObjectNode song) {
    if ("web_discovered".equals(song.path("sourceStatus").asText())) {
      String query=song.path("searchQuery").asText(song.path("artistName").asText()+" "+song.path("title").asText());
      song.put("searchUrl","https://music.163.com/#/search/m/?s="+URLEncoder.encode(query, StandardCharsets.UTF_8)+"&type=1");
      return;
    }
    try {
      var id=UUID.fromString(song.path("recordingId").asText());
      var recording=recordings.findByIdInWithArtist(List.of(id)).stream().findFirst().orElse(null);
      if(recording==null) return;
      song.put("title",recording.getTitle()); song.put("artistName",recording.getArtist().getName()); song.put("albumTitle",Optional.ofNullable(recording.getAlbumTitle()).orElse(""));
      song.put("searchUrl","https://music.163.com/#/search/m/?s="+URLEncoder.encode(recording.getArtist().getName()+" "+recording.getTitle(), StandardCharsets.UTF_8)+"&type=1");
    } catch(IllegalArgumentException ignored) { }
  }

  private void enrichArtist(ObjectNode artist) {
    if ("web_discovered".equals(artist.path("sourceStatus").asText())) {
      String query=artist.path("searchQuery").asText(artist.path("artistName").asText());
      artist.put("searchUrl","https://music.163.com/#/search/m/?s="+URLEncoder.encode(query, StandardCharsets.UTF_8)+"&type=100");
      return;
    }
    try {
      var value=artists.findById(UUID.fromString(artist.path("artistId").asText())).orElse(null);
      if(value==null) return;
      artist.put("artistName",value.getName());
      artist.put("searchUrl","https://music.163.com/#/search/m/?s="+URLEncoder.encode(value.getName(), StandardCharsets.UTF_8)+"&type=100");
    } catch(IllegalArgumentException ignored) { }
  }

  private GuestSession guest(HttpServletRequest request) { return (GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE); }
  record CreateBody(boolean force) {}
}
