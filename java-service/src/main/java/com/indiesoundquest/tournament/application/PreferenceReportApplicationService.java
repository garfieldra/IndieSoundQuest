package com.indiesoundquest.tournament.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.tournament.domain.TournamentPreferenceReport;
import com.indiesoundquest.tournament.repository.TournamentPreferenceReportRepository;
import com.indiesoundquest.async.AsyncOutboxEvent;
import com.indiesoundquest.async.AsyncOutboxEventRepository;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import java.util.UUID;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.function.Consumer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;

@Service
public class PreferenceReportApplicationService {
  private final TournamentPreferenceReportRepository reports;
  private final AsyncOutboxEventRepository outbox;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(5)).build();
  private final String agentBaseUrl;
  private final String agentToken;

  public PreferenceReportApplicationService(TournamentPreferenceReportRepository reports, AsyncOutboxEventRepository outbox, ObjectMapper objectMapper,
      @Value("${agent.internal.base-url:http://agent-service:8000}") String agentBaseUrl,
      @Value("${agent.internal.service-token:change-me}") String agentToken) {
    this.reports = reports; this.outbox=outbox; this.objectMapper = objectMapper; this.agentBaseUrl = agentBaseUrl; this.agentToken = agentToken;
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

  @Transactional public void enqueue(UUID reportId, UUID tournamentId, UUID guestSessionId, int version, String traceId) {
    try { outbox.save(AsyncOutboxEvent.pending(reportId,objectMapper.writeValueAsString(java.util.Map.of("reportId",reportId,"tournamentId",tournamentId,"guestId",guestSessionId,"version",version)),traceId)); }
    catch(Exception e){throw new IllegalStateException("REPORT_OUTBOX_SERIALIZATION_FAILED",e);}
  }
  /** Idempotent queue consumer entrypoint. */
  @CircuitBreaker(name="reportAgent") public void generateQueued(UUID reportId, UUID tournamentId, UUID guestSessionId, int version) {
    var report=reports.findById(reportId).orElseThrow(); if(report.getStatus()==com.indiesoundquest.tournament.domain.PreferenceReportStatus.READY)return;
    generateStreaming(reportId,tournamentId,guestSessionId,version,ignored->{});
  }

  /** Runs the existing report workflow while forwarding only Agent-approved public progress events. */
  public void generateStreaming(UUID reportId, UUID tournamentId, UUID guestSessionId, int version, Consumer<StreamEvent> onProgress) {
    try {
      markRunning(reportId);
      var requestId = UUID.randomUUID();
      var body = objectMapper.writeValueAsString(java.util.Map.of("requestId",requestId,"reportId",reportId,"tournamentId",tournamentId,"guestId",guestSessionId.toString(),"tournamentVersion",version,"includePersonalityEasterEgg",true));
      var request = HttpRequest.newBuilder(URI.create(agentBaseUrl + "/internal/v1/workflows/tournament-report:stream"))
          .timeout(Duration.ofSeconds(320)).header("Authorization", "Bearer " + agentToken).header("X-Request-Id", requestId.toString()).header("traceparent", traceparent(requestId)).header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body)).build();
      var response = httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());
      if (response.statusCode() < 200 || response.statusCode() >= 300) throw new IllegalStateException("AGENT_HTTP_" + response.statusCode());
      var reportJson = parseResult(response.body(), onProgress);
      if (reportJson == null) throw new IllegalStateException("AGENT_RESULT_MISSING");
      markReady(reportId, reportJson);
    } catch (Exception ex) {
      String message = ex.getMessage() == null ? "REPORT_WORKFLOW_FAILED" : ex.getMessage().substring(0, Math.min(500, ex.getMessage().length()));
      markFailed(reportId, message);
    }
  }

    private String parseResult(java.io.InputStream input, Consumer<StreamEvent> onProgress) throws Exception {
    try (var lines = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
      String event = null, data = null, line;
      while ((line = lines.readLine()) != null) {
        if (line.startsWith("event: ")) event = line.substring(7).trim();
        else if (line.startsWith("data: ")) data = line.substring(6).trim();
        else if (line.isEmpty()) {
          if (("progress".equals(event) || "plan_updated".equals(event)) && data != null) { objectMapper.readTree(data); onProgress.accept(new StreamEvent(event, data)); }
          if ("result".equals(event) && data != null) { objectMapper.readTree(data); return data; }
          if ("error".equals(event)) throw new IllegalStateException("AGENT_WORKFLOW_ERROR");
          event = null; data = null;
        }
      }
    }
    return null;
  }
  public record StreamEvent(String event, String data) {}
  private String traceparent(UUID requestId) { return "00-"+requestId.toString().replace("-","")+"-"+UUID.randomUUID().toString().replace("-","").substring(0,16)+"-01"; }
}
