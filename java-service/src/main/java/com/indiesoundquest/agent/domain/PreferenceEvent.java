package com.indiesoundquest.agent.domain;
import jakarta.persistence.*; import java.time.Instant; import java.util.UUID;
@Entity @Table(name="preference_event") public class PreferenceEvent {
 @Id private UUID id; @Column(name="guest_session_id",nullable=false) private UUID guestSessionId; @Column(name="conversation_id") private UUID conversationId; @Column(name="agent_run_id") private UUID agentRunId;
 @Column(name="target_type",nullable=false) private String targetType; @Column(name="target_ref_json",nullable=false,columnDefinition="JSON") private String targetRefJson;
 @Enumerated(EnumType.STRING) @Column(nullable=false) private PreferenceFeedback feedback; @Column(name="idempotency_key",nullable=false) private String idempotencyKey; @Column(name="created_at",nullable=false) private Instant createdAt;
 protected PreferenceEvent(){}
 public static PreferenceEvent create(UUID guestSessionId,UUID conversationId,UUID agentRunId,String targetType,String targetRefJson,PreferenceFeedback feedback,String idempotencyKey){
  var event=new PreferenceEvent();event.id=UUID.randomUUID();event.guestSessionId=guestSessionId;event.conversationId=conversationId;event.agentRunId=agentRunId;event.targetType=targetType;event.targetRefJson=targetRefJson;event.feedback=feedback;event.idempotencyKey=idempotencyKey;event.createdAt=Instant.now();return event;
 }
 public UUID getId(){return id;} public PreferenceFeedback getFeedback(){return feedback;} public String getTargetRefJson(){return targetRefJson;}
}
