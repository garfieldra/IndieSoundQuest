package com.indiesoundquest.agent.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.agent.application.AgentRunApplicationService;
import com.indiesoundquest.agent.application.AgentRunFollowUpApplicationService;
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
import org.springframework.transaction.annotation.Transactional;
import java.nio.charset.StandardCharsets;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@RestController
@RequestMapping("/api/v1/agent-runs")
public class AgentRunController {
  private static final Logger log = LoggerFactory.getLogger(AgentRunController.class);
  private final AgentRunApplicationService agentRuns;
  private final AgentRunFollowUpApplicationService followUps;
  private final ConversationApplicationService conversations;
  private final ConversationAgentGateway agent;
  private final RedisRateLimitService rateLimit;
  private final ObjectMapper json;

  public AgentRunController(
      AgentRunApplicationService agentRuns,
      AgentRunFollowUpApplicationService followUps,
      ConversationApplicationService conversations,
      ConversationAgentGateway agent,
      RedisRateLimitService rateLimit,
      ObjectMapper json) {
    this.agentRuns = agentRuns;
    this.followUps = followUps;
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

  @PostMapping("/{id}/interventions")
  @Transactional
  ResponseEntity<InterventionView> intervene(@PathVariable UUID id,@RequestHeader("Idempotency-Key") UUID key,@Valid @RequestBody InterventionBody body,HttpServletRequest request){
    var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();rateLimit.assertAgentRunAnswerAllowed(guest);
    var intervention=agentRuns.submitIntervention(id,guest,key,body.content().trim());
    conversations.appendRunIntervention(intervention.getConversationId(),guest,key,intervention.getContent());
    return ResponseEntity.accepted().body(InterventionView.of(intervention));
  }

  @PostMapping("/{id}/next-message")
  ResponseEntity<FollowUpView> queueNextMessage(@PathVariable UUID id,@RequestHeader("Idempotency-Key") UUID key,@Valid @RequestBody InterventionBody body,HttpServletRequest request){
    var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();rateLimit.assertAgentRunAnswerAllowed(guest);
    return ResponseEntity.accepted().body(FollowUpView.of(followUps.queue(id,guest,key,body.content().trim())));
  }

  @GetMapping("/{id}/next-message")
  ResponseEntity<FollowUpView> nextMessage(@PathVariable UUID id,HttpServletRequest request){
    var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();
    return followUps.owned(id,guest).map(value->ResponseEntity.ok(FollowUpView.of(value))).orElseGet(()->ResponseEntity.noContent().build());
  }

  @PostMapping("/{id}/next-message:intervene")
  @Transactional
  ResponseEntity<InterventionView> interveneWithQueuedMessage(@PathVariable UUID id,HttpServletRequest request){
    var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();rateLimit.assertAgentRunAnswerAllowed(guest);
    var existing=followUps.owned(id,guest).orElseThrow(NoSuchElementException::new);
    if(existing.getStatus()==AgentRunFollowUpStatus.INTERVENED){
      return ResponseEntity.ok(InterventionView.of(agentRuns.intervention(id,existing.getClientMessageId()).orElseThrow()));
    }
    var queued=followUps.waitingForIntervention(id,guest);
    var intervention=agentRuns.submitIntervention(id,guest,queued.getClientMessageId(),queued.getContent());
    conversations.appendRunIntervention(queued.getConversationId(),guest,queued.getClientMessageId(),queued.getContent());
    followUps.markIntervened(queued);
    return ResponseEntity.accepted().body(InterventionView.of(intervention));
  }

  @PostMapping("/{id}/cancel")
  ResponseEntity<Void> cancel(@PathVariable UUID id,HttpServletRequest request){var guest=((GuestSession)request.getAttribute(GuestIdentityFilter.ATTRIBUTE)).getId();var run=agentRuns.cancel(id,guest);if(run.getConversationId()!=null)conversations.cancelRun(run.getConversationId(),guest,id);followUps.dispatch(id);return ResponseEntity.noContent().build();}

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
        var publicAnswer = Optional.ofNullable(body.answer()).filter(value -> !value.isBlank()).orElseGet(() -> Optional.ofNullable(body.selections()).orElse(List.of()).stream().map(item -> item.mention() + "：" + item.name()).reduce((left, right) -> left + "；" + right).orElse("已确认澄清选项"));
        conversations.appendClarificationAnswer(conversationId, guest, key, publicAnswer);
        agentRuns.resume(id);
        var confirmed = Optional.ofNullable(body.selections()).orElse(List.of()).stream().map(item -> Map.<String, Object>of("mention", item.mention(), "mbid", item.mbid().toString(), "name", item.name())).toList();
        var resumeKind = snapshot.path("resumeKind").asText("ARTIST_IDENTITY");
        var payload = new LinkedHashMap<String, Object>();
        payload.put("requestId", id); payload.put("agentRunId", id); payload.put("conversationId", conversationId); payload.put("guestId", guest.toString());
        payload.put("resumeKind", resumeKind); payload.put("preferenceText", snapshot.path("preferenceText").asText()); payload.put("poolSize", snapshot.path("poolSize").asInt(32)); payload.put("confirmedArtists", confirmed);
        payload.put("answer", publicAnswer);
        if (snapshot.has("originalRequest")) payload.put("originalRequest", json.convertValue(snapshot.path("originalRequest"), Map.class));
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
        log.error("Failed to resume Agent Run {} after clarification", id, e);
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

  record AnswersBody(List<@Valid Selection> selections, @Size(max = 2000) String answer) {
    AnswersBody { if ((selections == null || selections.isEmpty()) && (answer == null || answer.isBlank())) throw new IllegalArgumentException("answer or selections is required"); }
  }

  record Selection(@NotBlank @Size(max = 120) String mention, @NotNull UUID mbid, @NotBlank @Size(max = 160) String name) {}
  record InterventionBody(@NotBlank @Size(max=2000) String content) {}
  record InterventionView(UUID id,long sequenceNumber,String status,java.time.Instant createdAt){static InterventionView of(AgentRunIntervention value){return new InterventionView(value.getId(),value.getSequenceNumber(),value.getStatus().name(),value.getCreatedAt());}}
  record FollowUpView(UUID id,UUID clientMessageId,String content,String status,UUID nextRunId,java.time.Instant createdAt){static FollowUpView of(AgentRunFollowUp value){return new FollowUpView(value.getId(),value.getClientMessageId(),value.getContent(),value.getStatus().name(),value.getNextRunId(),value.getCreatedAt());}}

  record EventView(long sequenceNumber, String type, String payloadJson, java.time.Instant createdAt) {
    static EventView of(AgentRunEvent event) {
      return new EventView(event.getSequenceNumber(), event.getType().name(), event.getPayloadJson(), event.getCreatedAt());
    }
  }

  record EventsView(String runStatus, List<EventView> events) {}

  @ExceptionHandler(IllegalStateException.class)
  ResponseEntity<Map<String,String>> conflict(IllegalStateException error){return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("code",error.getMessage()==null?"AGENT_RUN_CONFLICT":error.getMessage()));}
}
