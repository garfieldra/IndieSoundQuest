package com.indiesoundquest.conversation.web;

import com.indiesoundquest.conversation.application.ConversationApplicationService;
import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.redis.RedisRateLimitService;
import com.indiesoundquest.tournament.domain.GuestSession;
import jakarta.servlet.http.HttpServletRequest;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

@RestController
@RequestMapping("/api/v1/conversations")
public class ConversationExplorationReportController {
  private final ConversationApplicationService service;
  private final RedisRateLimitService rateLimit;

  public ConversationExplorationReportController(ConversationApplicationService service, RedisRateLimitService rateLimit) {
    this.service = service;
    this.rateLimit = rateLimit;
  }

  @PostMapping(value = "/{id}/exploration-report", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
  StreamingResponseBody explorationReport(@PathVariable UUID id, @RequestHeader("Idempotency-Key") UUID key, HttpServletRequest request) {
    var guest = ((GuestSession) request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();
    rateLimit.assertExplorationReportAllowed(guest);
    return output -> service.streamExplorationReport(id, guest, key, output);
  }
}
