package com.indiesoundquest.conversation.web;

import com.indiesoundquest.conversation.application.ConversationApplicationService;
import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.redis.RedisRateLimitService;
import com.indiesoundquest.tournament.domain.GuestSession;
import jakarta.servlet.http.HttpServletRequest;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1/conversations")
public class ConversationExplorationReportController {
  private final ConversationApplicationService service;
  private final RedisRateLimitService rateLimit;

  public ConversationExplorationReportController(ConversationApplicationService service, RedisRateLimitService rateLimit) {
    this.service = service;
    this.rateLimit = rateLimit;
  }

  @PostMapping("/{id}/exploration-report")
  ResponseEntity<QueuedView> explorationReport(@PathVariable UUID id, @RequestHeader("Idempotency-Key") UUID key, HttpServletRequest request) {
    var guest = ((GuestSession) request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();
    rateLimit.assertExplorationReportAllowed(guest);
    var queued = service.enqueueExplorationReport(id, guest, key);
    return ResponseEntity.status(HttpStatus.ACCEPTED).body(new QueuedView(queued.runId(), queued.status(), "/api/v1/agent-runs/" + queued.runId() + "/events:stream", queued.replayed()));
  }

  record QueuedView(UUID runId, String status, String eventsUrl, boolean replayed) {}
}
