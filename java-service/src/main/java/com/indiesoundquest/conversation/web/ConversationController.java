package com.indiesoundquest.conversation.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.conversation.application.*;
import com.indiesoundquest.conversation.domain.*;
import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.redis.RedisRateLimitService;
import com.indiesoundquest.tournament.domain.GuestSession;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.io.OutputStream;
import java.util.*;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

@RestController
@RequestMapping("/api/v1/conversations")
public class ConversationController {
  private final ConversationApplicationService service;
  private final RedisRateLimitService rateLimit;
  private final ObjectMapper json;

  public ConversationController(ConversationApplicationService service, RedisRateLimitService rateLimit, ObjectMapper json) {
    this.service = service;
    this.rateLimit = rateLimit;
    this.json = json;
  }

  @PostMapping
  ResponseEntity<View> create(HttpServletRequest request) {
    var c = service.create(guest(request).getId());
    return ResponseEntity.status(HttpStatus.CREATED).body(View.of(c));
  }

  @GetMapping
  List<View> list(HttpServletRequest request) {
    return service.list(guest(request).getId()).stream().map(View::of).toList();
  }

  @GetMapping("/{id}")
  View get(@PathVariable UUID id, HttpServletRequest request) {
    return View.of(service.owned(id, guest(request).getId()));
  }

  @GetMapping("/{id}/messages")
  List<MessageView> messages(@PathVariable UUID id, HttpServletRequest request) {
    return service.messages(id, guest(request).getId()).stream().map(MessageView::of).toList();
  }

  @PatchMapping("/{id}")
  View patch(@PathVariable UUID id, @Valid @RequestBody Update body, HttpServletRequest request) {
    if (body.title() == null || body.title().isBlank()) throw new IllegalArgumentException("title is required");
    return View.of(service.rename(id, guest(request).getId(), body.title().trim()));
  }

  @DeleteMapping("/{id}")
  ResponseEntity<Void> delete(@PathVariable UUID id, HttpServletRequest request) {
    service.delete(id, guest(request).getId());
    return ResponseEntity.noContent().build();
  }

  @PostMapping("/{id}/cards")
  MessageView card(@PathVariable UUID id, @RequestHeader("Idempotency-Key") UUID key, @Valid @RequestBody Card body, HttpServletRequest request) {
    return MessageView.of(service.card(id, guest(request).getId(), key, body.type(), body.cardType(), body.payloadJson()));
  }

  @PostMapping(value = "/{id}/messages:stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
  StreamingResponseBody message(@PathVariable UUID id, @RequestHeader("Idempotency-Key") UUID key, @Valid @RequestBody Send body, HttpServletRequest request) {
    var guest = guest(request);
    rateLimit.assertConversationMessageAllowed(guest.getId());
    return out -> {
      try {
        var result = service.reply(id, guest.getId(), key, body.content(), event -> write(out, event.type(), event.data()));
        write(out, "message_completed", json.writeValueAsString(MessageView.of(result)));
      } catch (Exception e) {
        write(out, "error", "{\"code\":\"CONVERSATION_UNAVAILABLE\",\"message\":\"这次音乐对话暂时无法完成，请稍后重试\",\"retryable\":true}");
      }
    };
  }

  @PostMapping("/{id}/messages")
  ResponseEntity<QueuedView> enqueue(@PathVariable UUID id,@RequestHeader("Idempotency-Key") UUID key,@Valid @RequestBody Send body,HttpServletRequest request){var guest=guest(request);rateLimit.assertConversationMessageAllowed(guest.getId());ConversationApplicationService.QueuedTurn queued;try{queued=service.enqueue(id,guest.getId(),key,body.content());}catch(org.springframework.dao.DataIntegrityViolationException race){queued=service.queuedByClientMessage(id,guest.getId(),key);}return ResponseEntity.accepted().body(new QueuedView(queued.runId(),queued.status(),"/api/v1/agent-runs/"+queued.runId()+"/events:stream",queued.replayed()));}

  private void write(OutputStream out, String event, String data) {
    try {
      out.write(("event: " + event + "\n\ndata: " + data + "\n\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));
      out.flush();
    } catch (Exception ignored) {}
  }

  private GuestSession guest(HttpServletRequest r) {
    return (GuestSession) r.getAttribute(GuestIdentityFilter.ATTRIBUTE);
  }

  record Update(@Size(min = 1, max = 120) String title) {}

  record Send(@NotBlank @Size(max = 2000) String content) {}
  record QueuedView(UUID runId,String status,String eventsUrl,boolean replayed) {}

  record Card(@NotNull ConversationMessageType type, @NotBlank @Size(max = 40) String cardType, @NotBlank @Size(max = 20000) String payloadJson) {}

  record View(UUID id, String title, String summary, ConversationStatus status, java.time.Instant lastMessageAt) {
    static View of(Conversation c) {
      return new View(c.getId(), c.getTitle(), c.getSummary(), c.getStatus(), c.getLastMessageAt());
    }
  }

  record MessageView(UUID id, UUID agentRunId, ConversationMessageRole role, ConversationMessageType type, String content, String cardType, String cardPayloadJson, ConversationMessageStatus status, long sequenceNumber, java.time.Instant createdAt) {
    static MessageView of(ConversationMessage m) {
      return new MessageView(m.getId(), m.getAgentRunId(), m.getRole(), m.getType(), m.getTextContent(), m.getCardType(), m.getCardPayloadJson(), m.getStatus(), m.getSequenceNumber(), m.getCreatedAt());
    }
  }
}
