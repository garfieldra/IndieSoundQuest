package com.indiesoundquest.async;

import jakarta.persistence.*; import java.time.*; import java.util.*;
@Entity @Table(name="async_outbox_event") public class AsyncOutboxEvent {
 @Id private UUID id; @Column(name="aggregate_type",nullable=false) private String aggregateType; @Column(name="aggregate_id",nullable=false) private UUID aggregateId; @Column(name="event_type",nullable=false) private String eventType; @Column(name="payload_json",nullable=false,columnDefinition="json") private String payloadJson; @Column(name="trace_id") private String traceId; @Column(nullable=false) private String status; @Column(name="publish_attempts",nullable=false) private int publishAttempts; @Column(name="next_attempt_at",nullable=false) private Instant nextAttemptAt; @Column(name="published_at") private Instant publishedAt; @Column(name="created_at",nullable=false) private Instant createdAt; @Column(name="updated_at",nullable=false) private Instant updatedAt;
 protected AsyncOutboxEvent(){}
 public static AsyncOutboxEvent pending(UUID aggregateId,String payload,String traceId){var e=new AsyncOutboxEvent();e.id=UUID.randomUUID();e.aggregateType="PREFERENCE_REPORT";e.aggregateId=aggregateId;e.eventType="REPORT_GENERATION_REQUESTED";e.payloadJson=payload;e.traceId=traceId;e.status="PENDING";e.nextAttemptAt=Instant.now();e.createdAt=e.nextAttemptAt;e.updatedAt=e.createdAt;return e;}
 public void published(){status="PUBLISHED";publishedAt=Instant.now();updatedAt=publishedAt;} public void retry(){publishAttempts++;nextAttemptAt=Instant.now().plusSeconds(Math.min(60,1L<<Math.min(6,publishAttempts)));updatedAt=Instant.now();} public UUID getId(){return id;} public String getPayloadJson(){return payloadJson;} public String getTraceId(){return traceId;}
}
