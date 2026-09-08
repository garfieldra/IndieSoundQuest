package com.indiesoundquest.agent.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.agent.domain.*;
import com.indiesoundquest.agent.repository.*;
import com.indiesoundquest.redis.IdempotencyCacheService;
import com.indiesoundquest.redis.IdempotencyOperations;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RecommendationFeedbackService {
  private final PreferenceEventRepository events;
  private final IdempotencyCacheService idempotency;
  private final ObjectMapper json;

  public RecommendationFeedbackService(PreferenceEventRepository events, IdempotencyCacheService idempotency, ObjectMapper json) {
    this.events = events;
    this.idempotency = idempotency;
    this.json = json;
  }

  @Transactional
  public PreferenceEvent record(UUID guestSessionId, UUID conversationId, UUID agentRunId, String targetType, Map<String, Object> targetRef, PreferenceFeedback feedback, String idempotencyKey) {
    var cached = idempotency.get(guestSessionId, IdempotencyOperations.RECOMMENDATION_FEEDBACK, idempotencyKey);
    if (cached.isPresent()) {
      return events.findById(UUID.fromString(cached.get().payload())).orElseThrow();
    }
    var existing = events.findByGuestSessionIdAndIdempotencyKey(guestSessionId, idempotencyKey);
    if (existing.isPresent()) {
      cacheFeedback(guestSessionId, idempotencyKey, existing.get());
      return existing.get();
    }
    try {
      var saved = events.save(PreferenceEvent.create(guestSessionId, conversationId, agentRunId, targetType, json.writeValueAsString(targetRef), feedback, idempotencyKey));
      cacheFeedback(guestSessionId, idempotencyKey, saved);
      return saved;
    } catch (Exception e) {
      throw new IllegalStateException("PREFERENCE_EVENT_SERIALIZATION_FAILED", e);
    }
  }

  private void cacheFeedback(UUID guestSessionId, String idempotencyKey, PreferenceEvent event) {
    idempotency.put(guestSessionId, IdempotencyOperations.RECOMMENDATION_FEEDBACK, idempotencyKey, event.getFeedback().name(), event.getId().toString());
  }
}
