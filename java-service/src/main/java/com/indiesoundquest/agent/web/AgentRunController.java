package com.indiesoundquest.agent.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.agent.application.AgentRunApplicationService;
import com.indiesoundquest.agent.domain.*;
import com.indiesoundquest.conversation.application.ConversationAgentGateway;
import com.indiesoundquest.conversation.application.ConversationApplicationService;
import com.indiesoundquest.identity.GuestIdentityFilter;
import com.indiesoundquest.redis.RedisRateLimitService;
import com.indiesoundquest.tournament.domain.GuestSession;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.util.*;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;
import java.nio.charset.StandardCharsets;

@RestController
@RequestMapping("/api/v1/agent-runs")
public class AgentRunController {
  private final AgentRunApplicationService agentRuns;
  private final ConversationApplicationService conversations;
  private final ConversationAgentGateway agent;
  private final RedisRateLimitService rateLimit;
  private final ObjectMapper json;

  public AgentRunController(
      AgentRunApplicationService agentRuns,
      ConversationApplicationService conversations,
      ConversationAgentGateway agent,
      RedisRateLimitService rateLimit,
      ObjectMapper json) {
    this.agentRuns = agentRuns;
    this.conversations = conversations;
    this.agent = agent;
    this.rateLimit = rateLimit;
    this.json = json;
  }

  @GetMapping("/{id}/events")
  EventsView events(@PathVariable UUID id, @RequestParam(defaultValue = "0") long afterSequence, HttpServletRequest request) {
    var guest = ((GuestSession) request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();
    var run = agentRuns.owned(id, guest);
    return new EventsView(run.getStatus().name(), agentRuns.eventsAfter(id, guest, afterSequence).stream().map(EventView::of).toList());
  }

  @GetMapping(value="/{id}/events:stream",produces=MediaType.TEXT_EVENT_STREAM_VALUE)
  StreamingResponseBody streamEvents(@PathVariable UUID id,@RequestParam(defaultValue="0")long afterSequence,@RequestHeader(value="Last-Event-ID",required=false)Long lastEventId,HttpServletRequest request,HttpServletResponse response){var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();agentRuns.owned(id,guest);response.setContentType(MediaType.TEXT_EVENT_STREAM_VALUE);response.setCharacterEncoding(StandardCharsets.UTF_8.name());response.setHeader(HttpHeaders.CACHE_CONTROL,"no-cache, no-transform");response.setHeader("X-Accel-Buffering","no");return output->{long cursor=Math.max(afterSequence,lastEventId==null?0:lastEventId);for(int idle=0;idle<900;idle++){var batch=agentRuns.eventsAfter(id,guest,cursor);for(var event:batch){cursor=event.getSequenceNumber();var data=json.writeValueAsString(EventView.of(event));output.write(("id: "+cursor+"\nevent: run_event\ndata: "+data+"\n\n").getBytes(StandardCharsets.UTF_8));}var status=agentRuns.owned(id,guest).getStatus();if(isStreamTerminal(status)){output.write(("event: run_status\ndata: {\"status\":\""+status.name()+"\",\"lastSequence\":"+cursor+"}\n\n").getBytes(StandardCharsets.UTF_8));output.flush();return;}if(batch.isEmpty())output.write(": heartbeat\n\n".getBytes(StandardCharsets.UTF_8));output.flush();try{Thread.sleep(1000);}catch(InterruptedException interrupted){Thread.currentThread().interrupt();return;}}};}

  private boolean isStreamTerminal(AgentRunStatus status){return status==AgentRunStatus.COMPLETED||status==AgentRunStatus.WAITING_FOR_USER||status==AgentRunStatus.FAILED||status==AgentRunStatus.CANCELLED||status==AgentRunStatus.EXPIRED;}

  @PostMapping(value = "/{id}/answers", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
  StreamingResponseBody answers(@PathVariable UUID id, @RequestHeader("Idempotency-Key") UUID key, @Valid @RequestBody AnswersBody body, HttpServletRequest request) {
    var guest = ((GuestSession) request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();
    rateLimit.assertAgentRunAnswerAllowed(guest);
    return output -> {
      try {
        var run = agentRuns.owned(id, guest);
        if (run.getStatus() != AgentRunStatus.WAITING_FOR_USER) throw new IllegalStateException("AGENT_RUN_NOT_WAITING");
        var snapshot = json.readTree(run.getContextSnapshotJson() == null ? "{}" : run.getContextSnapshotJson());
        var conversationId = run.getConversationId();
        agentRuns.resume(id);
        var confirmed = body.selections().stream().map(item -> Map.<String, Object>of("mention", item.mention(), "mbid", item.mbid().toString(), "name", item.name())).toList();
        var payload = Map.<String, Object>of(
            "requestId", id,
            "agentRunId", id,
            "conversationId", conversationId,
            "guestId", guest.toString(),
            "preferenceText", snapshot.path("preferenceText").asText(),
            "poolSize", snapshot.path("poolSize").asInt(32),
            "confirmedArtists", confirmed);
        var result = agent.resume(payload, id, event -> {
          try {
            agentRuns.relaySseEvent(id, event.type(), event.data());
            write(output, event.type(), event.data());
          } catch (Exception ignored) {}
        });
        var message = conversations.completeAgentAnswer(conversationId, guest, id, key, result);
        agentRuns.complete(id, json.writeValueAsString(Map.of("messageId", message.getId())));
        write(output, "message_completed", json.writeValueAsString(Map.of("id", message.getId(), "content", message.getTextContent())));
      } catch (Exception e) {
        write(output, "error", "{\"code\":\"AGENT_ANSWER_FAILED\",\"message\":\"澄清答案暂时无法处理，请稍后重试\"}");
      }
    };
  }

  private void write(java.io.OutputStream output, String event, String data) {
    try {
      output.write(("event: " + event + "\n\ndata: " + data + "\n\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));
      output.flush();
    } catch (Exception ignored) {}
  }

  record AnswersBody(@NotEmpty List<@Valid Selection> selections) {}

  record Selection(@NotBlank @Size(max = 120) String mention, @NotNull UUID mbid, @NotBlank @Size(max = 160) String name) {}

  record EventView(long sequenceNumber, String type, String payloadJson, java.time.Instant createdAt) {
    static EventView of(AgentRunEvent event) {
      return new EventView(event.getSequenceNumber(), event.getType().name(), event.getPayloadJson(), event.getCreatedAt());
    }
  }

  record EventsView(String runStatus, List<EventView> events) {}
}
