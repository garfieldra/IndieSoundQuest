package com.indiesoundquest.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.agent.repository.PreferenceEventRepository;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PreferenceEventQueryService {
  private static final int DEFAULT_LIMIT = 12;

  private final PreferenceEventRepository events;
  private final ObjectMapper json;

  public PreferenceEventQueryService(PreferenceEventRepository events, ObjectMapper json) {
    this.events = events;
    this.json = json;
  }

  @Transactional(readOnly = true)
  public List<String> recentSummaries(UUID guestSessionId) {
    return recentSummaries(guestSessionId, DEFAULT_LIMIT);
  }

  @Transactional(readOnly = true)
  public List<String> recentSummaries(UUID guestSessionId, int limit) {
    var summaries = new ArrayList<String>();
    for (var event : events.findTop20ByGuestSessionIdOrderByCreatedAtDesc(guestSessionId).stream().limit(limit).toList()) {
      summaries.add(format(event.getFeedback().name(), event.getTargetRefJson()));
    }
    return summaries;
  }

  private String format(String feedback, String targetRefJson) {
    try {
      JsonNode target = json.readTree(targetRefJson);
      var label = target.path("title").asText(target.path("claimText").asText(target.path("artistName").asText("推荐项")));
      return feedback + " · " + label;
    } catch (Exception exception) {
      return feedback;
    }
  }
}
