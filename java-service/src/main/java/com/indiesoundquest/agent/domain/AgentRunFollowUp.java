package com.indiesoundquest.agent.domain;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "agent_run_follow_up")
public class AgentRunFollowUp {
  @Id private UUID id;
  @Column(name = "run_id", nullable = false) private UUID runId;
  @Column(name = "conversation_id", nullable = false) private UUID conversationId;
  @Column(name = "guest_session_id", nullable = false) private UUID guestSessionId;
  @Column(name = "client_message_id", nullable = false) private UUID clientMessageId;
  @Column(nullable = false, columnDefinition = "TEXT") private String content;
  @Enumerated(EnumType.STRING) @Column(nullable = false) private AgentRunFollowUpStatus status;
  @Column(name = "next_run_id") private UUID nextRunId;
  @Column(name = "created_at", nullable = false) private Instant createdAt;
  @Column(name = "resolved_at") private Instant resolvedAt;

  protected AgentRunFollowUp() {}

  public static AgentRunFollowUp waiting(AgentRun run, UUID clientMessageId, String content) {
    var value = new AgentRunFollowUp();
    value.id = UUID.randomUUID();
    value.runId = run.getId();
    value.conversationId = run.getConversationId();
    value.guestSessionId = run.getGuestSessionId();
    value.clientMessageId = clientMessageId;
    value.content = content;
    value.status = AgentRunFollowUpStatus.WAITING;
    value.createdAt = Instant.now();
    return value;
  }

  public void dispatch(UUID nextRunId) {
    requireWaiting();
    this.nextRunId = nextRunId;
    this.status = AgentRunFollowUpStatus.DISPATCHED;
    this.resolvedAt = Instant.now();
  }

  public void intervene() {
    requireWaiting();
    this.status = AgentRunFollowUpStatus.INTERVENED;
    this.resolvedAt = Instant.now();
  }

  private void requireWaiting() {
    if (status != AgentRunFollowUpStatus.WAITING) throw new IllegalStateException("FOLLOW_UP_NOT_WAITING");
  }

  public UUID getId() { return id; }
  public UUID getRunId() { return runId; }
  public UUID getConversationId() { return conversationId; }
  public UUID getGuestSessionId() { return guestSessionId; }
  public UUID getClientMessageId() { return clientMessageId; }
  public String getContent() { return content; }
  public AgentRunFollowUpStatus getStatus() { return status; }
  public UUID getNextRunId() { return nextRunId; }
  public Instant getCreatedAt() { return createdAt; }
  public Instant getResolvedAt() { return resolvedAt; }
}
