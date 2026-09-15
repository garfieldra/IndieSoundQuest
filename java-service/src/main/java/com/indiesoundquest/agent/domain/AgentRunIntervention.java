package com.indiesoundquest.agent.domain;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name="agent_run_intervention")
public class AgentRunIntervention {
  @Id private UUID id;
  @Column(name="run_id",nullable=false) private UUID runId;
  @Column(name="conversation_id",nullable=false) private UUID conversationId;
  @Column(name="guest_session_id",nullable=false) private UUID guestSessionId;
  @Column(name="client_message_id",nullable=false) private UUID clientMessageId;
  @Column(name="sequence_number",nullable=false) private long sequenceNumber;
  @Enumerated(EnumType.STRING) @Column(nullable=false) private AgentRunInterventionType type;
  @Column(nullable=false,columnDefinition="TEXT") private String content;
  @Enumerated(EnumType.STRING) @Column(nullable=false) private AgentRunInterventionStatus status;
  @Column(name="created_at",nullable=false) private Instant createdAt;
  @Column(name="applied_at") private Instant appliedAt;

  protected AgentRunIntervention() {}

  public static AgentRunIntervention create(UUID runId,UUID conversationId,UUID guestSessionId,UUID clientMessageId,long sequenceNumber,String content){
    var value=new AgentRunIntervention();value.id=UUID.randomUUID();value.runId=runId;value.conversationId=conversationId;value.guestSessionId=guestSessionId;value.clientMessageId=clientMessageId;value.sequenceNumber=sequenceNumber;value.type=AgentRunInterventionType.ADJUST_DIRECTION;value.content=content;value.status=AgentRunInterventionStatus.PENDING;value.createdAt=Instant.now();return value;
  }
  public void markApplied(){if(status==AgentRunInterventionStatus.PENDING){status=AgentRunInterventionStatus.APPLIED;appliedAt=Instant.now();}}
  public UUID getId(){return id;} public UUID getRunId(){return runId;} public UUID getConversationId(){return conversationId;} public UUID getGuestSessionId(){return guestSessionId;} public UUID getClientMessageId(){return clientMessageId;} public long getSequenceNumber(){return sequenceNumber;} public AgentRunInterventionType getType(){return type;} public String getContent(){return content;} public AgentRunInterventionStatus getStatus(){return status;} public Instant getCreatedAt(){return createdAt;} public Instant getAppliedAt(){return appliedAt;}
}
