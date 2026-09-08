package com.indiesoundquest.agent.domain;
import jakarta.persistence.*; import java.time.Instant; import java.util.UUID;
@Entity @Table(name="agent_run_event") public class AgentRunEvent {
 @Id private UUID id; @Column(name="run_id",nullable=false) private UUID runId; @Column(name="sequence_number",nullable=false) private long sequenceNumber;
 @Enumerated(EnumType.STRING) @Column(nullable=false) private AgentRunEventType type; @Column(name="payload_json",nullable=false,columnDefinition="JSON") private String payloadJson;
 @Column(name="trace_id") private String traceId; @Column(name="created_at",nullable=false) private Instant createdAt;
 protected AgentRunEvent(){}
 public static AgentRunEvent create(UUID runId,long sequenceNumber,AgentRunEventType type,String payloadJson){var event=new AgentRunEvent();event.id=UUID.randomUUID();event.runId=runId;event.sequenceNumber=sequenceNumber;event.type=type;event.payloadJson=payloadJson;event.createdAt=Instant.now();return event;}
 public UUID getRunId(){return runId;} public long getSequenceNumber(){return sequenceNumber;} public AgentRunEventType getType(){return type;} public String getPayloadJson(){return payloadJson;} public Instant getCreatedAt(){return createdAt;}
}
