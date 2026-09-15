package com.indiesoundquest.agent.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.agent.domain.*;
import com.indiesoundquest.agent.repository.*;
import com.indiesoundquest.conversation.application.ConversationApplicationService;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AgentRunFollowUpApplicationService {
  private final AgentRunRepository runs;
  private final AgentRunFollowUpRepository followUps;
  private final AgentRunApplicationService agentRuns;
  private final ConversationApplicationService conversations;
  private final ObjectMapper json;

  public AgentRunFollowUpApplicationService(
      AgentRunRepository runs,
      AgentRunFollowUpRepository followUps,
      AgentRunApplicationService agentRuns,
      ConversationApplicationService conversations,
      ObjectMapper json) {
    this.runs = runs;
    this.followUps = followUps;
    this.agentRuns = agentRuns;
    this.conversations = conversations;
    this.json = json;
  }

  @Transactional
  public AgentRunFollowUp queue(UUID runId, UUID guestId, UUID clientMessageId, String content) {
    var run = runs.findLockedById(runId).orElseThrow(NoSuchElementException::new);
    assertOwnedConversationRun(run, guestId);
    if (!(run.getStatus() == AgentRunStatus.QUEUED || run.getStatus() == AgentRunStatus.RUNNING)) {
      throw new IllegalStateException("AGENT_RUN_NOT_ACTIVE");
    }
    var replay = followUps.findByRunIdAndClientMessageId(runId, clientMessageId);
    if (replay.isPresent()) return replay.get();
    if (followUps.findByRunId(runId).isPresent()) throw new IllegalStateException("FOLLOW_UP_ALREADY_WAITING");
    var value = followUps.save(AgentRunFollowUp.waiting(run, clientMessageId, content));
    agentRuns.appendEvent(runId, AgentRunEventType.FOLLOW_UP_QUEUED,
        write(Map.of("phase", "follow_up_waiting", "message", "下一条消息已排队，将在本轮完成后自动发送")));
    return value;
  }

  @Transactional(readOnly = true)
  public Optional<AgentRunFollowUp> owned(UUID runId, UUID guestId) {
    runs.findByIdAndGuestSessionId(runId, guestId).orElseThrow(NoSuchElementException::new);
    return followUps.findByRunId(runId);
  }

  @Transactional
  public AgentRunFollowUp waitingForIntervention(UUID runId, UUID guestId) {
    var run = runs.findLockedById(runId).orElseThrow(NoSuchElementException::new);
    assertOwnedConversationRun(run, guestId);
    if (!(run.getStatus() == AgentRunStatus.QUEUED || run.getStatus() == AgentRunStatus.RUNNING)) {
      throw new IllegalStateException("AGENT_RUN_NOT_ACTIVE");
    }
    var value = followUps.findLockedByRunId(runId).orElseThrow(NoSuchElementException::new);
    if (value.getStatus() != AgentRunFollowUpStatus.WAITING) throw new IllegalStateException("FOLLOW_UP_NOT_WAITING");
    return value;
  }

  @Transactional
  public void markIntervened(AgentRunFollowUp value) {
    value.intervene();
    followUps.save(value);
    agentRuns.appendEvent(value.getRunId(), AgentRunEventType.FOLLOW_UP_INTERVENED,
        write(Map.of("phase", "follow_up_intervened", "message", "排队消息已改为立即调整本轮方向")));
  }

  @Transactional
  public Optional<AgentRunFollowUp> dispatch(UUID runId) {
    var run = runs.findLockedById(runId).orElseThrow(NoSuchElementException::new);
    if (!(run.getStatus() == AgentRunStatus.COMPLETED || run.getStatus() == AgentRunStatus.FAILED || run.getStatus() == AgentRunStatus.CANCELLED)) {
      return Optional.empty();
    }
    var waiting = followUps.findLockedByRunId(runId)
        .filter(value -> value.getStatus() == AgentRunFollowUpStatus.WAITING);
    if (waiting.isEmpty()) return Optional.empty();
    var value = waiting.get();
    var next = conversations.enqueue(value.getConversationId(), value.getGuestSessionId(), value.getClientMessageId(), value.getContent());
    value.dispatch(next.runId());
    followUps.save(value);
    agentRuns.appendEvent(runId, AgentRunEventType.FOLLOW_UP_DISPATCHED,
        write(Map.of("phase", "follow_up_dispatched", "message", "已开始处理排队消息", "nextRunId", next.runId())));
    return Optional.of(value);
  }

  private void assertOwnedConversationRun(AgentRun run, UUID guestId) {
    if (!run.getGuestSessionId().equals(guestId)) throw new NoSuchElementException();
    if (run.getConversationId() == null || !(run.getRunType() == AgentRunType.CONVERSATION || run.getRunType() == AgentRunType.EXPLORATION_REPORT)) {
      throw new IllegalStateException("AGENT_RUN_NOT_STEERABLE");
    }
  }

  private String write(Object value) {
    try { return json.writeValueAsString(value); }
    catch (Exception error) { throw new IllegalStateException("FOLLOW_UP_EVENT_SERIALIZATION_FAILED", error); }
  }
}
