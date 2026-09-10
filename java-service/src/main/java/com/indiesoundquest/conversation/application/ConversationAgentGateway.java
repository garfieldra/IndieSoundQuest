package com.indiesoundquest.conversation.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class ConversationAgentGateway {
  private final ObjectMapper json;
  private final String baseUrl;
  private final String token;
  private final HttpClient client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(5)).build();

  public ConversationAgentGateway(ObjectMapper json, @Value("${agent.internal.base-url:http://agent-service:8000}") String baseUrl, @Value("${agent.internal.service-token}") String token) {
    this.json = json; this.baseUrl = baseUrl; this.token = token;
  }
  public Result stream(Map<String,Object> body, UUID requestId, Consumer<Event> events) { return invoke(baseUrl + "/internal/v1/workflows/conversation:stream", body, requestId, events); }
  public Result resume(Map<String,Object> body, UUID requestId, Consumer<Event> events) { return invoke(baseUrl + "/internal/v1/workflows/conversation:resume", body, requestId, events); }
  public CompressionResult compress(Map<String,Object> body, UUID requestId) {
    try {
      var request = HttpRequest.newBuilder(URI.create(baseUrl + "/internal/v1/memory:compress")).timeout(Duration.ofSeconds(90)).header("Authorization", "Bearer " + token).header("X-Request-Id", requestId.toString()).header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body))).build();
      var response = client.send(request, HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() != 200) throw new IllegalStateException("AGENT_MEMORY_HTTP_" + response.statusCode());
      var node = json.readTree(response.body());
      return new CompressionResult(node.path("summary").asText(), node.path("throughSequence").asLong());
    } catch (Exception e) { throw new IllegalStateException("conversation memory compression unavailable", e); }
  }

  private Result invoke(String url, Map<String,Object> body, UUID requestId, Consumer<Event> events) {
    try {
      var request = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(900)).header("Authorization", "Bearer " + token).header("X-Request-Id", requestId.toString()).header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body))).build();
      var response = client.send(request, HttpResponse.BodyHandlers.ofInputStream());
      if (response.statusCode() != 200) throw new IllegalStateException("AGENT_HTTP_" + response.statusCode());
      try (var lines = new BufferedReader(new InputStreamReader(response.body()))) {
        String event = null, data = null, line;
        while ((line = lines.readLine()) != null) {
          if (line.startsWith("event: ")) event = line.substring(7).trim();
          else if (line.startsWith("data: ")) data = line.substring(6).trim();
          else if (line.isEmpty()) {
            if (("progress".equals(event) || "plan_updated".equals(event)) && data != null) events.accept(new Event(event, data));
            else if ("result".equals(event) && data != null) {
              var node = json.readTree(data); var card = node.path("cardIntent");
              return new Result(node.path("text").asText(), card.isMissingNode() || card.isNull() ? null : new Card(card.path("messageType").asText(), card.path("cardType").asText(), json.writeValueAsString(card.path("payload"))), node.path("memorySummary").isTextual() ? node.path("memorySummary").asText() : null, node.path("memorySummaryThroughSequence").isNumber() ? node.path("memorySummaryThroughSequence").asLong() : null);
            } else if ("error".equals(event)) throw new IllegalStateException("AGENT_WORKFLOW_ERROR");
            event = null; data = null;
          }
        }
        throw new IllegalStateException("AGENT_RESULT_MISSING");
      }
    } catch (Exception e) { throw new IllegalStateException("conversation agent unavailable", e); }
  }
  public record Event(String type, String data) {}
  public record Card(String messageType, String cardType, String payloadJson) {}
  public record Result(String text, Card card, String memorySummary, Long memorySummaryThroughSequence) {}
  public record CompressionResult(String summary, long throughSequence) {}
}
