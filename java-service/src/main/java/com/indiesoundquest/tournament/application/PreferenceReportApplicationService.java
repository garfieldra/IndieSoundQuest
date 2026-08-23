package com.indiesoundquest.tournament.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.tournament.domain.TournamentPreferenceReport;
import com.indiesoundquest.tournament.repository.TournamentPreferenceReportRepository;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PreferenceReportApplicationService {
  private final TournamentPreferenceReportRepository reports;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(5)).build();
  private final String agentBaseUrl;
  private final String agentToken;

  public PreferenceReportApplicationService(TournamentPreferenceReportRepository reports, ObjectMapper objectMapper,
      @Value("${agent.internal.base-url:http://agent-service:8000}") String agentBaseUrl,
      @Value("${agent.internal.service-token:change-me}") String agentToken) {
    this.reports = reports; this.objectMapper = objectMapper; this.agentBaseUrl = agentBaseUrl; this.agentToken = agentToken;
  }

  @Transactional
  public void markRunning(UUID reportId) {
    var report = reports.findById(reportId).orElseThrow(); report.markRunning(); reports.save(report);
  }

  @Transactional
  public void markReady(UUID reportId, String json) {
    var report = reports.findById(reportId).orElseThrow(); report.markReady(json); reports.save(report);
  }

  @Transactional
  public void markFailed(UUID reportId, String message) {
    reports.findById(reportId).ifPresent(report -> { report.markFailed(message); reports.save(report); });
  }

  public void startAsync(UUID reportId, UUID tournamentId, UUID guestSessionId, int version) {
    CompletableFuture.runAsync(() -> generate(reportId, tournamentId, guestSessionId, version));
  }

  private void generate(UUID reportId, UUID tournamentId, UUID guestSessionId, int version) {
    try {
      markRunning(reportId);
      var requestId = UUID.randomUUID();
      var body = objectMapper.writeValueAsString(java.util.Map.of("requestId",requestId,"reportId",reportId,"tournamentId",tournamentId,"guestId",guestSessionId.toString(),"tournamentVersion",version,"includePersonalityEasterEgg",true));
      var request = HttpRequest.newBuilder(URI.create(agentBaseUrl + "/internal/v1/workflows/tournament-report:stream"))
          .timeout(Duration.ofSeconds(95)).header("Authorization", "Bearer " + agentToken).header("X-Request-Id", requestId.toString()).header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body)).build();
      var response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() < 200 || response.statusCode() >= 300) throw new IllegalStateException("AGENT_HTTP_" + response.statusCode());
      var reportJson = parseResult(response.body());
      if (reportJson == null) throw new IllegalStateException("AGENT_RESULT_MISSING");
      markReady(reportId, reportJson);
    } catch (Exception ex) {
      String message = ex.getMessage() == null ? "REPORT_WORKFLOW_FAILED" : ex.getMessage().substring(0, Math.min(500, ex.getMessage().length()));
      markFailed(reportId, message);
    }
  }

  private String parseResult(String sse) throws Exception {
    for (String block : sse.split("\\n\\n")) {
      String event = null, data = null;
      for (String line : block.split("\\n")) { if (line.startsWith("event: ")) event=line.substring(7).trim(); if (line.startsWith("data: ")) data=line.substring(6).trim(); }
      if ("result".equals(event) && data != null) { objectMapper.readTree(data); return data; }
      if ("error".equals(event)) throw new IllegalStateException("AGENT_WORKFLOW_ERROR");
    }
    return null;
  }
}
