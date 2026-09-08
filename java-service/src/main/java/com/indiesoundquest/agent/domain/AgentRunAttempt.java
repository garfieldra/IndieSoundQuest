package com.indiesoundquest.agent.domain;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "agent_run_attempt")
public class AgentRunAttempt {
  @Id private UUID id;
  @Column(name = "run_id", nullable = false) private UUID runId;
  @Column(name = "attempt_no", nullable = false) private int attemptNo;
  @Column(name = "worker_id", nullable = false) private String workerId;
  @Column(name = "lease_token", nullable = false) private String leaseToken;
  @Column(nullable = false) private String status;
  @Column(name = "started_at", nullable = false) private Instant startedAt;
  @Column(name = "heartbeat_at", nullable = false) private Instant heartbeatAt;
  @Column(name = "lease_expires_at", nullable = false) private Instant leaseExpiresAt;
  @Column(name = "finished_at") private Instant finishedAt;
  @Column(name = "failure_code") private String failureCode;

  protected AgentRunAttempt() {}

  public static AgentRunAttempt start(UUID runId, int attemptNo, String workerId, String leaseToken, Instant now) {
    var value = new AgentRunAttempt();
    value.id = UUID.randomUUID(); value.runId = runId; value.attemptNo = attemptNo;
    value.workerId = workerId; value.leaseToken = leaseToken; value.status = "RUNNING";
    value.startedAt = value.heartbeatAt = now; value.leaseExpiresAt = now.plusSeconds(30);
    return value;
  }

  public boolean valid(String token, Instant now) { return status.equals("RUNNING") && leaseToken.equals(token) && leaseExpiresAt.isAfter(now); }
  public void heartbeat(Instant now) { heartbeatAt = now; leaseExpiresAt = now.plusSeconds(30); }
  public void complete(Instant now) { status = "COMPLETED"; finishedAt = now; }
  public void fail(String code, Instant now) { status = "FAILED"; failureCode = code; finishedAt = now; }
  public int getAttemptNo() { return attemptNo; }
  public String getLeaseToken() { return leaseToken; }
  public UUID getRunId() { return runId; }
  public Instant getLeaseExpiresAt(){return leaseExpiresAt;}
  public String getStatus(){return status;}
}
