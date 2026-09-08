package com.indiesoundquest.conversation.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.indiesoundquest.agent.application.AgentRunApplicationService;
import com.indiesoundquest.agent.application.PreferenceEventQueryService;
import com.indiesoundquest.agent.domain.AgentRunType;
import com.indiesoundquest.agent.domain.AgentRunStatus;
import com.indiesoundquest.agent.domain.AgentRunEventType;
import com.indiesoundquest.conversation.domain.*;
import com.indiesoundquest.conversation.repository.*;
import com.indiesoundquest.redis.IdempotencyCacheService;
import com.indiesoundquest.redis.IdempotencyOperations;
import com.indiesoundquest.async.*;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ConversationApplicationService {
  private final ConversationRepository conversations;
  private final ConversationMessageRepository messages;
  private final ConversationAgentGateway agent;
  private final AgentRunApplicationService agentRuns;
  private final MusicPreferenceMemoryService memories;
  private final PreferenceEventQueryService preferenceEvents;
  private final IdempotencyCacheService idempotency;
  private final ObjectMapper json;
  private final AsyncOutboxEventRepository outbox;

  public ConversationApplicationService(
      ConversationRepository conversations,
      ConversationMessageRepository messages,
      ConversationAgentGateway agent,
      AgentRunApplicationService agentRuns,
      MusicPreferenceMemoryService memories,
      PreferenceEventQueryService preferenceEvents,
      IdempotencyCacheService idempotency,
      ObjectMapper json, AsyncOutboxEventRepository outbox) {
    this.conversations = conversations;
    this.messages = messages;
    this.agent = agent;
    this.agentRuns = agentRuns;
    this.memories = memories;
    this.preferenceEvents = preferenceEvents;
    this.idempotency = idempotency;
    this.json = json;
    this.outbox = outbox;
  }

  @Transactional
  public QueuedTurn enqueue(UUID id, UUID guestId, UUID clientMessageId, String content) {
    var c=conversations.findLockedOwned(id,guestId,ConversationStatus.DELETED).orElseThrow(NoSuchElementException::new);
    var replay = messages.findByConversationIdAndClientMessageId(id, clientMessageId);
    if (replay.isPresent()) {
      var existingRun = messages.findByConversationIdOrderBySequenceNumberAsc(id).stream().filter(m -> m.getType()==ConversationMessageType.AGENT_RUN && m.getSequenceNumber()==replay.get().getSequenceNumber()+1).findFirst().orElseThrow();
      return new QueuedTurn(existingRun.getAgentRunId(), AgentRunStatus.QUEUED.name(), true);
    }
    long next=messages.findTopByConversationIdOrderBySequenceNumberDesc(id).map(x->x.getSequenceNumber()+1).orElse(1L);
    messages.save(ConversationMessage.user(id,clientMessageId,content,next));
    if("新的音乐探索".equals(c.getTitle()))c.rename(content.substring(0,Math.min(36,content.length())));c.touch();
    var runId=UUID.randomUUID();messages.save(ConversationMessage.run(id,runId,next+1));
    var transcript=messages.findByConversationIdOrderBySequenceNumberAsc(id);var start=Math.max(0,transcript.size()-12);
    var history=transcript.stream().filter(m->m.getTextContent()!=null).skip(start).map(m->Map.of("role",m.getRole().name(),"content",m.getTextContent())).toList();
    var body=agentBody(runId,id,guestId,content,c.getSummary(),history,null,null);
    try{var payload=json.writeValueAsString(Map.of("runId",runId,"runType","CONVERSATION","input",body));agentRuns.createQueued(runId,guestId,id,AgentRunType.CONVERSATION,json.writeValueAsString(body));agentRuns.appendEvent(runId,AgentRunEventType.PROGRESS,"{\"phase\":\"queued\",\"message\":\"请求已进入 Agent 队列\"}");outbox.save(AsyncOutboxEvent.pendingAgentRun(runId,payload,runId.toString()));}
    catch(Exception e){throw new IllegalStateException("AGENT_RUN_ENQUEUE_FAILED",e);}
    return new QueuedTurn(runId,AgentRunStatus.QUEUED.name(),false);
  }

  public record QueuedTurn(UUID runId,String status,boolean replayed){}

  @Transactional(readOnly=true)
  public QueuedTurn queuedByClientMessage(UUID id,UUID guestId,UUID clientMessageId){owned(id,guestId);var user=messages.findByConversationIdAndClientMessageId(id,clientMessageId).orElseThrow();var run=messages.findByConversationIdOrderBySequenceNumberAsc(id).stream().filter(m->m.getType()==ConversationMessageType.AGENT_RUN&&m.getSequenceNumber()==user.getSequenceNumber()+1).findFirst().orElseThrow();var state=agentRuns.owned(run.getAgentRunId(),guestId).getStatus().name();return new QueuedTurn(run.getAgentRunId(),state,true);}

  @Transactional
  public Conversation create(UUID guestId) {
    var c = conversations.save(new Conversation(UUID.randomUUID(), guestId, "新的音乐探索"));
    messages.save(ConversationMessage.system(c.getId(), "我们的歌曲世界杯应该从哪开始？描述一下你的音乐喜好，或告诉我最近反复听的歌。", 1));
    return c;
  }

  @Transactional(readOnly = true)
  public List<Conversation> list(UUID guestId) {
    return conversations.findByGuestSessionIdAndStatusNotOrderByLastMessageAtDesc(guestId, ConversationStatus.DELETED);
  }

  @Transactional(readOnly = true)
  public Conversation owned(UUID id, UUID guestId) {
    return conversations.findByIdAndGuestSessionIdAndStatusNot(id, guestId, ConversationStatus.DELETED).orElseThrow(NoSuchElementException::new);
  }

  @Transactional(readOnly = true)
  public List<ConversationMessage> messages(UUID id, UUID guestId) {
    owned(id, guestId);
    return messages.findByConversationIdOrderBySequenceNumberAsc(id);
  }

  @Transactional
  public Conversation rename(UUID id, UUID guestId, String title) {
    var c = owned(id, guestId);
    c.rename(title);
    return c;
  }

  @Transactional
  public void archive(UUID id, UUID guestId) {
    owned(id, guestId).archive();
  }

  @Transactional
  public void delete(UUID id, UUID guestId) {
    owned(id, guestId).delete();
  }

  @Transactional
  public ConversationMessage card(UUID id, UUID guestId, UUID clientId, ConversationMessageType type, String cardType, String payload) {
    owned(id, guestId);
    var existing = messages.findByConversationIdAndClientMessageId(id, clientId);
    if (existing.isPresent()) return existing.get();
    long next = messages.findTopByConversationIdOrderBySequenceNumberDesc(id).map(x -> x.getSequenceNumber() + 1).orElse(1L);
    return messages.save(ConversationMessage.card(id, clientId, type, cardType, payload, next));
  }

  @Transactional
  public Turn begin(UUID id, UUID guestId, UUID clientMessageId, String content) {
    var c = owned(id, guestId);
    var replay = messages.findByConversationIdAndClientMessageId(id, clientMessageId);
    if (replay.isPresent()) return new Turn(replay.get(), null, true);
    long next = messages.findTopByConversationIdOrderBySequenceNumberDesc(id).map(x -> x.getSequenceNumber() + 1).orElse(1L);
    var user = messages.save(ConversationMessage.user(id, clientMessageId, content, next));
    if ("新的音乐探索".equals(c.getTitle())) c.rename(content.substring(0, Math.min(36, content.length())));
    c.touch();
    var runId = UUID.randomUUID();
    var run = messages.save(ConversationMessage.run(id, runId, next + 1));
    agentRuns.create(runId, guestId, id, AgentRunType.CONVERSATION);
    return new Turn(user, run, false);
  }

  @Transactional
  public ConversationMessage complete(UUID id, UUID runId, ConversationAgentGateway.Result result) {
    var run = messages.findByConversationIdAndAgentRunId(id, runId).orElseThrow(NoSuchElementException::new);
    run.complete();
    messages.save(run);
    return persistAgentResult(id, runId, run.getSequenceNumber(), result);
  }

  @Transactional
  public ConversationMessage completeAfterResume(UUID id, UUID runId, ConversationAgentGateway.Result result) {
    var run = messages.findByConversationIdAndAgentRunId(id, runId).orElseThrow(NoSuchElementException::new);
    run.complete();
    messages.save(run);
    long next = messages.findTopByConversationIdOrderBySequenceNumberDesc(id).map(x -> x.getSequenceNumber() + 1).orElse(1L);
    var response = messages.save(ConversationMessage.assistant(id, runId, result.text(), next));
    if (result.card() != null) {
      var type = ConversationMessageType.valueOf(result.card().messageType());
      if (type != ConversationMessageType.CLARIFICATION_CARD && type != ConversationMessageType.CANDIDATE_POOL_CARD && type != ConversationMessageType.TOURNAMENT_CARD && type != ConversationMessageType.REPORT_CARD && type != ConversationMessageType.RECOMMENDATION_CARD) {
        throw new IllegalArgumentException("unsupported Agent card type");
      }
      messages.save(ConversationMessage.card(id, UUID.randomUUID(), type, result.card().cardType(), result.card().payloadJson(), next + 1));
    }
    conversations.findById(id).ifPresent(c -> { c.touch(); conversations.save(c); });
    return response;
  }

  @Transactional
  public ConversationMessage completeWaitingForUser(UUID id, UUID runId, ConversationAgentGateway.Result result, Map<String, Object> snapshot) {
    var run = messages.findByConversationIdAndAgentRunId(id, runId).orElseThrow(NoSuchElementException::new);
    var response = messages.save(ConversationMessage.assistant(id, runId, result.text(), run.getSequenceNumber() + 1));
    if (result.card() != null) {
      var type = ConversationMessageType.valueOf(result.card().messageType());
      messages.save(ConversationMessage.card(id, UUID.randomUUID(), type, result.card().cardType(), result.card().payloadJson(), run.getSequenceNumber() + 2));
    }
    agentRuns.markWaitingForUser(runId, snapshot);
    conversations.findById(id).ifPresent(c -> { c.touch(); conversations.save(c); });
    return response;
  }

  private ConversationMessage persistAgentResult(UUID id, UUID runId, long runSequence, ConversationAgentGateway.Result result) {
    var response = messages.save(ConversationMessage.assistant(id, runId, result.text(), runSequence + 1));
    if (result.card() != null) {
      var type = ConversationMessageType.valueOf(result.card().messageType());
      if (type != ConversationMessageType.CLARIFICATION_CARD && type != ConversationMessageType.CANDIDATE_POOL_CARD && type != ConversationMessageType.TOURNAMENT_CARD && type != ConversationMessageType.REPORT_CARD && type != ConversationMessageType.RECOMMENDATION_CARD) {
        throw new IllegalArgumentException("unsupported Agent card type");
      }
      messages.save(ConversationMessage.card(id, UUID.randomUUID(), type, result.card().cardType(), result.card().payloadJson(), runSequence + 2));
    }
    conversations.findById(id).ifPresent(c -> { c.touch(); conversations.save(c); });
    return response;
  }

  @Transactional
  public void fail(UUID id, UUID runId) {
    messages.findByConversationIdAndAgentRunId(id, runId).ifPresent(run -> { run.fail(); messages.save(run); });
    agentRuns.fail(runId, "{\"code\":\"CONVERSATION_UNAVAILABLE\"}");
  }

  public ConversationMessage reply(UUID id, UUID guestId, UUID clientMessageId, String content, java.util.function.Consumer<ConversationAgentGateway.Event> events) {
    var cached = idempotency.get(guestId, IdempotencyOperations.CONVERSATION_MESSAGE, clientMessageId.toString());
    if (cached.isPresent()) {
      return messages.findById(UUID.fromString(cached.get().payload())).orElseThrow();
    }
    var turn = begin(id, guestId, clientMessageId, content);
    if (turn.replayed()) {
      return messages(id, guestId).stream()
          .filter(m -> m.getType() == ConversationMessageType.AGENT_TEXT && m.getSequenceNumber() > turn.user().getSequenceNumber())
          .findFirst()
          .orElse(turn.user());
    }
    var transcript = messages(id, guestId);
    var start = Math.max(0, transcript.size() - 12);
    var history = transcript.stream().filter(m -> m.getTextContent() != null).skip(start).map(m -> Map.of("role", m.getRole().name(), "content", m.getTextContent())).toList();
    var conversation = owned(id, guestId);
    var body = agentBody(turn.run().getAgentRunId(), id, guestId, content, conversation.getSummary(), history, null, null);
    try {
      var result = agent.stream(body, turn.run().getAgentRunId(), event -> {
        agentRuns.relaySseEvent(turn.run().getAgentRunId(), event.type(), event.data());
        events.accept(event);
      });
      ConversationMessage response;
      if (result.card() != null && "CLARIFICATION_CARD".equals(result.card().messageType()) && "ARTIST_IDENTITY".equals(result.card().cardType())) {
        response = completeWaitingForUser(id, turn.run().getAgentRunId(), result, clarificationSnapshot(content, result.card().payloadJson()));
      } else {
        agentRuns.complete(turn.run().getAgentRunId(), result.card() == null ? "{}" : result.card().payloadJson());
        response = complete(id, turn.run().getAgentRunId(), result);
      }
      cacheConversationResult(guestId, clientMessageId, id, content, response.getId());
      return response;
    } catch (RuntimeException e) {
      fail(id, turn.run().getAgentRunId());
      throw e;
    }
  }

  public void streamExplorationReport(UUID id, UUID guestId, UUID clientMessageId, OutputStream output) {
    var cached = idempotency.get(guestId, IdempotencyOperations.EXPLORATION_REPORT, clientMessageId.toString());
    if (cached.isPresent()) {
      try {
        var message = messages.findById(UUID.fromString(cached.get().payload())).orElseThrow();
        write(output, "message_completed", json.writeValueAsString(Map.of("id", message.getId(), "content", message.getTextContent(), "replayed", true)));
      } catch (Exception e) {
        write(output, "error", "{\"code\":\"EXPLORATION_REPORT_UNAVAILABLE\",\"message\":\"探索报告暂时无法生成\"}");
      }
      return;
    }
    var conversation = owned(id, guestId);
    long next = messages.findTopByConversationIdOrderBySequenceNumberDesc(id).map(x -> x.getSequenceNumber() + 1).orElse(1L);
    var runId = UUID.randomUUID();
    messages.save(ConversationMessage.run(id, runId, next));
    agentRuns.create(runId, guestId, id, AgentRunType.EXPLORATION_REPORT);
    var transcript = messages(id, guestId);
    var history = transcript.stream().filter(m -> m.getTextContent() != null).map(m -> Map.of("role", m.getRole().name(), "content", m.getTextContent())).toList();
    var body = agentBody(runId, id, guestId, "请根据当前对话整理一份音乐偏好探索报告。", conversation.getSummary(), history, "generate_exploration_report", null);
    try {
      var result = agent.stream(body, runId, event -> {
        try {
          agentRuns.relaySseEvent(runId, event.type(), event.data());
          write(output, event.type(), event.data());
        } catch (Exception ignored) {}
      });
      agentRuns.complete(runId, result.card() == null ? "{}" : result.card().payloadJson());
      var message = complete(id, runId, result);
      cacheExplorationReport(guestId, clientMessageId, id, message.getId());
      write(output, "message_completed", json.writeValueAsString(Map.of("id", message.getId(), "content", message.getTextContent())));
    } catch (Exception e) {
      fail(id, runId);
      write(output, "error", "{\"code\":\"EXPLORATION_REPORT_UNAVAILABLE\",\"message\":\"探索报告暂时无法生成\"}");
    }
  }

  @Transactional
  public ConversationMessage completeAgentAnswer(UUID conversationId, UUID guestId, UUID runId, UUID idempotencyKey, ConversationAgentGateway.Result result) {
    var cached = idempotency.get(guestId, IdempotencyOperations.AGENT_RUN_ANSWER, idempotencyKey.toString());
    if (cached.isPresent()) {
      return messages.findById(UUID.fromString(cached.get().payload())).orElseThrow();
    }
    var message = completeAfterResume(conversationId, runId, result);
    idempotency.put(guestId, IdempotencyOperations.AGENT_RUN_ANSWER, idempotencyKey.toString(), runId.toString(), message.getId().toString());
    return message;
  }

  private Map<String, Object> agentBody(UUID runId, UUID conversationId, UUID guestId, String content, String summary, List<Map<String, String>> history, String forcedAction, Integer poolSize) {
    var body = new LinkedHashMap<String, Object>();
    body.put("requestId", runId);
    body.put("agentRunId", runId);
    body.put("conversationId", conversationId);
    body.put("guestId", guestId.toString());
    body.put("userMessage", content);
    body.put("summary", summary == null ? "" : summary);
    body.put("recentMessages", history);
    body.put("confirmedMemories", memories.confirmedContents(guestId));
    body.put("recentFeedback", preferenceEvents.recentSummaries(guestId));
    if (forcedAction != null) body.put("forcedAction", forcedAction);
    if (poolSize != null) body.put("poolSize", poolSize);
    return body;
  }

  private void cacheConversationResult(UUID guestId, UUID clientMessageId, UUID conversationId, String content, UUID messageId) {
    idempotency.put(guestId, IdempotencyOperations.CONVERSATION_MESSAGE, clientMessageId.toString(), conversationId + ":" + content.hashCode(), messageId.toString());
  }

  private void cacheExplorationReport(UUID guestId, UUID clientMessageId, UUID conversationId, UUID messageId) {
    idempotency.put(guestId, IdempotencyOperations.EXPLORATION_REPORT, clientMessageId.toString(), conversationId.toString(), messageId.toString());
  }

  private Map<String, Object> clarificationSnapshot(String preferenceText, String payloadJson) {
    try {
      var payload = json.readTree(payloadJson);
      return Map.of("preferenceText", payload.path("preferenceText").asText(preferenceText), "poolSize", payload.path("poolSize").asInt(32));
    } catch (Exception e) {
      return Map.of("preferenceText", preferenceText, "poolSize", 32);
    }
  }

  private void write(OutputStream output, String event, String data) {
    try {
      output.write(("event: " + event + "\n\ndata: " + data + "\n\n").getBytes(StandardCharsets.UTF_8));
      output.flush();
    } catch (Exception ignored) {}
  }

  public record Turn(ConversationMessage user, ConversationMessage run, boolean replayed) {}
}
