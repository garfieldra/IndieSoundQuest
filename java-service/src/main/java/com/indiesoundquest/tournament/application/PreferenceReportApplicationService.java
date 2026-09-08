package com.indiesoundquest.tournament.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.tournament.domain.TournamentPreferenceReport;
import com.indiesoundquest.tournament.repository.TournamentPreferenceReportRepository;
import com.indiesoundquest.async.AsyncOutboxEvent;
import com.indiesoundquest.async.AsyncOutboxEventRepository;
import com.indiesoundquest.agent.application.AgentRunApplicationService;
import com.indiesoundquest.agent.domain.AgentRunEventType;
import com.indiesoundquest.agent.domain.AgentRunType;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PreferenceReportApplicationService {
  private final TournamentPreferenceReportRepository reports;
  private final AsyncOutboxEventRepository outbox;
  private final AgentRunApplicationService agentRuns;
  private final ObjectMapper objectMapper;
  public PreferenceReportApplicationService(TournamentPreferenceReportRepository reports, AsyncOutboxEventRepository outbox,
      AgentRunApplicationService agentRuns, ObjectMapper objectMapper) {
    this.reports = reports;
    this.outbox = outbox;
    this.agentRuns = agentRuns;
    this.objectMapper = objectMapper;
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

  /** Creates the durable report run and its Outbox message in the caller's report transaction. */
  @Transactional
  public void enqueueAgentRun(UUID runId, UUID reportId, UUID tournamentId, UUID guestSessionId, int version, String traceId) {
    try {
      var input = new java.util.LinkedHashMap<String,Object>();
      input.put("requestId", runId);
      input.put("reportId", reportId);
      input.put("tournamentId", tournamentId);
      input.put("guestId", guestSessionId.toString());
      input.put("tournamentVersion", version);
      input.put("includePersonalityEasterEgg", true);
      var task = java.util.Map.of("runId", runId, "runType", AgentRunType.TOURNAMENT_REPORT.name(), "input", input);
      agentRuns.createQueuedForTournament(runId, guestSessionId, tournamentId, AgentRunType.TOURNAMENT_REPORT, objectMapper.writeValueAsString(input));
      agentRuns.appendEvent(runId, AgentRunEventType.PROGRESS, "{\"phase\":\"queued\",\"message\":\"赛后报告任务已入队\"}");
      outbox.save(AsyncOutboxEvent.pendingAgentRun(runId, objectMapper.writeValueAsString(task), traceId == null ? runId.toString() : traceId));
    } catch (Exception e) {
      throw new IllegalStateException("REPORT_AGENT_RUN_ENQUEUE_FAILED", e);
    }
  }

  @Transactional
  public void markAgentRunRunning(UUID runId, String leaseToken, UUID reportId) {
    agentRuns.assertLease(runId, leaseToken);
    markRunning(reportId);
  }

  /** Java owns final contract validation and commits report + AgentRun atomically. */
  @Transactional
  public void completeAgentRun(UUID runId, String leaseToken, UUID reportId, String reportJson) {
    agentRuns.assertLease(runId, leaseToken);
    var report = reports.findById(reportId).orElseThrow();
    validateReport(report, reportJson);
    report.markReady(reportJson);
    reports.save(report);
    try {
      agentRuns.complete(runId, objectMapper.writeValueAsString(java.util.Map.of(
          "reportId", reportId,
          "tournamentId", report.getTournamentId(),
          "version", report.getVersionNumber(),
          "status", "READY")));
    } catch (Exception e) {
      throw new IllegalStateException("REPORT_ARTIFACT_SERIALIZATION_FAILED", e);
    }
    agentRuns.finishLease(runId, leaseToken);
  }

  @Transactional
  public void failAgentRun(UUID runId, String leaseToken, UUID reportId, String code, String message) {
    agentRuns.assertLease(runId, leaseToken);
    var safeMessage = message == null ? code : message.substring(0, Math.min(500, message.length()));
    markFailed(reportId, safeMessage);
    try {
      agentRuns.fail(runId, objectMapper.writeValueAsString(java.util.Map.of("code", code, "message", safeMessage)));
    } catch (Exception e) {
      throw new IllegalStateException("REPORT_FAILURE_SERIALIZATION_FAILED", e);
    }
    agentRuns.finishLease(runId, leaseToken);
  }

  private void validateReport(TournamentPreferenceReport report, String reportJson) {
    try {
      var value = objectMapper.readTree(reportJson);
      if (!"1.0".equals(value.path("schemaVersion").asText())) throw new IllegalArgumentException("REPORT_SCHEMA_VERSION_INVALID");
      if (!report.getTournamentId().toString().equals(value.path("tournamentId").asText())) throw new IllegalArgumentException("REPORT_TOURNAMENT_MISMATCH");
      if (value.path("summary").asText().isBlank()) throw new IllegalArgumentException("REPORT_SUMMARY_MISSING");
      int dimensions = value.path("dimensions").size();
      int songs = value.path("songRecommendations").size();
      int artists = value.path("artistRecommendations").size();
      if (dimensions < 3 || dimensions > 5) throw new IllegalArgumentException("REPORT_DIMENSIONS_INVALID");
      if (songs < 5 || songs > 7) throw new IllegalArgumentException("REPORT_SONG_RECOMMENDATIONS_INVALID");
      if (artists > 3) throw new IllegalArgumentException("REPORT_ARTIST_RECOMMENDATIONS_INVALID");
      if (!value.path("choiceTrajectory").isArray()) throw new IllegalArgumentException("REPORT_TRAJECTORY_INVALID");
    } catch (IllegalArgumentException e) {
      throw e;
    } catch (Exception e) {
      throw new IllegalArgumentException("REPORT_JSON_INVALID", e);
    }
  }
}
