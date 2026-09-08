package com.indiesoundquest.agent.domain;
import jakarta.persistence.*; import java.time.Instant; import java.util.UUID;
@Entity @Table(name="agent_run") public class AgentRun {
 @Id private UUID id; @Column(name="guest_session_id",nullable=false) private UUID guestSessionId; @Column(name="conversation_id") private UUID conversationId; @Column(name="tournament_id") private UUID tournamentId;
 @Enumerated(EnumType.STRING) @Column(name="run_type",nullable=false) private AgentRunType runType; @Enumerated(EnumType.STRING) @Column(nullable=false) private AgentRunStatus status;
 @Column(name="model_provider") private String modelProvider; @Column(name="model_name") private String modelName; @Column(name="budget_json",columnDefinition="JSON") private String budgetJson;
 @Column(name="input_version") private String inputVersion; @Column(name="artifact_ref_json",columnDefinition="JSON") private String artifactRefJson; @Column(name="context_snapshot_json",columnDefinition="JSON") private String contextSnapshotJson;
 @Column(name="started_at",nullable=false) private Instant startedAt; @Column(name="completed_at") private Instant completedAt; @Column(name="expires_at") private Instant expiresAt;
 @Column(name="created_at",nullable=false) private Instant createdAt; @Column(name="updated_at",nullable=false) private Instant updatedAt;
 @Column(name="next_event_sequence",nullable=false) private long nextEventSequence;
 protected AgentRun(){}
 public static AgentRun create(UUID id,UUID guestSessionId,UUID conversationId,AgentRunType runType){var run=new AgentRun();run.id=id;run.guestSessionId=guestSessionId;run.conversationId=conversationId;run.runType=runType;run.status=AgentRunStatus.RUNNING;run.startedAt=run.createdAt=run.updatedAt=Instant.now();run.nextEventSequence=1;return run;}
 public static AgentRun queued(UUID id,UUID guestSessionId,UUID conversationId,AgentRunType runType,String snapshot){var run=create(id,guestSessionId,conversationId,runType);run.status=AgentRunStatus.QUEUED;run.contextSnapshotJson=snapshot;return run;}
 public static AgentRun queuedForTournament(UUID id,UUID guestSessionId,UUID tournamentId,AgentRunType runType,String snapshot){var run=queued(id,guestSessionId,null,runType,snapshot);run.tournamentId=tournamentId;return run;}
 public UUID getId(){return id;} public UUID getGuestSessionId(){return guestSessionId;} public UUID getConversationId(){return conversationId;} public UUID getTournamentId(){return tournamentId;} public AgentRunType getRunType(){return runType;} public AgentRunStatus getStatus(){return status;}
 public String getContextSnapshotJson(){return contextSnapshotJson;} public Instant getExpiresAt(){return expiresAt;}
 public Instant getStartedAt(){return startedAt;}
 public long allocateEventSequence(){return nextEventSequence++;}
 public void start(){if(status!=AgentRunStatus.QUEUED)throw new IllegalStateException("AGENT_RUN_NOT_QUEUED");status=AgentRunStatus.RUNNING;updatedAt=Instant.now();}
 public void retry(){if(status!=AgentRunStatus.RUNNING)throw new IllegalStateException("AGENT_RUN_NOT_RUNNING");status=AgentRunStatus.QUEUED;updatedAt=Instant.now();}
 public void markWaitingForUser(String snapshotJson){status=AgentRunStatus.WAITING_FOR_USER;contextSnapshotJson=snapshotJson;expiresAt=Instant.now().plusSeconds(7L*24*3600);updatedAt=Instant.now();}
 public void resume(){status=AgentRunStatus.RUNNING;updatedAt=Instant.now();}
 public void complete(String artifactJson){status=AgentRunStatus.COMPLETED;artifactRefJson=artifactJson;completedAt=updatedAt=Instant.now();}
 public void fail(){status=AgentRunStatus.FAILED;completedAt=updatedAt=Instant.now();}
 public void cancel(){status=AgentRunStatus.CANCELLED;completedAt=updatedAt=Instant.now();}
}
