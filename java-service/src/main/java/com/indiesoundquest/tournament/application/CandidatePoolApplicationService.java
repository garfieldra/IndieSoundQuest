package com.indiesoundquest.tournament.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.indiesoundquest.agent.CandidatePoolGateway;
import com.indiesoundquest.listening.ListeningLinkFactory;
import com.indiesoundquest.tournament.domain.Recording;
import com.indiesoundquest.tournament.repository.ArtistRepository;
import com.indiesoundquest.tournament.repository.RecordingRepository;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

@Service
public class CandidatePoolApplicationService {
  private static final String READY = "ready_for_confirmation";
  private static final String INSUFFICIENT = "insufficient_candidates";
  private static final String CLARIFICATION = "needs_clarification";

  private final CandidatePoolGateway gateway;
  private final ArtistRepository artists;
  private final RecordingRepository recordings;

  public CandidatePoolApplicationService(
      CandidatePoolGateway gateway,
      ArtistRepository artists,
      RecordingRepository recordings) {
    this.gateway = gateway;
    this.artists = artists;
    this.recordings = recordings;
  }

  public CandidatePoolResponse generate(
      UUID requestId,
      String guestId,
      int size,
      String preferenceText,
      List<UUID> seedArtistIds) {
    validateRequest(size, seedArtistIds);
    final JsonNode agentResult;
    try {
      agentResult = gateway.generate(requestId, guestId, size, preferenceText, seedArtistIds);
    } catch (RuntimeException exception) {
      throw new CandidatePoolUnavailableException("候选歌曲服务暂时不可用，请稍后重试");
    }
    return fromAgentResult(requestId, size, agentResult, seedArtistIds);
  }

  public CandidatePoolResponse generate(
      UUID requestId,
      String guestId,
      int size,
      String preferenceText,
      List<UUID> seedArtistIds,
      List<CandidatePoolGateway.ConfirmedArtist> confirmedArtists) {
    validateRequest(size, seedArtistIds);

    final JsonNode agentResult;
    try {
      agentResult = gateway.generate(requestId, guestId, size, preferenceText, seedArtistIds, confirmedArtists);
    } catch (RuntimeException exception) {
      throw new CandidatePoolUnavailableException("候选歌曲服务暂时不可用，请稍后重试");
    }

    return fromAgentResult(requestId, size, agentResult, seedArtistIds);
  }

  /**
   * Converts the Agent's internal response into the stable public API shape.  This is also used
   * by the SSE controller so a streamed run and the legacy synchronous endpoint behave alike.
   */
  public CandidatePoolResponse fromAgentResult(
      UUID requestId, int size, JsonNode agentResult, List<UUID> seedArtistIds) {
    validateEnvelope(agentResult, requestId, size);
    var status = agentResult.path("status").asText();
    if (CLARIFICATION.equals(status)) return clarification(requestId, size, agentResult);
    if (INSUFFICIENT.equals(status)) return insufficient(requestId, size, agentResult);
    if (!READY.equals(status)) {
      throw new CandidatePoolUnavailableException("候选歌曲服务暂时无法完成本次生成");
    }
    return ready(requestId, size, agentResult, seedArtistIds);
  }

  private void validateRequest(int size, List<UUID> seedArtistIds) {
    if (size != 16 && size != 32) throw new IllegalArgumentException("赛事规模只能是 16 或 32 首");
    if (seedArtistIds.size() != new HashSet<>(seedArtistIds).size()) {
      throw new IllegalArgumentException("起点艺人不能重复");
    }
    if (seedArtistIds.stream().anyMatch(id -> !artists.existsById(id))) {
      throw new IllegalArgumentException("起点艺人不存在");
    }
  }

  private void validateEnvelope(JsonNode result, UUID requestId, int size) {
    if (result == null || !result.isObject()) throw contract("Agent 未返回有效候选池");
    UUID returnedRequestId;
    try {
      returnedRequestId = UUID.fromString(result.path("requestId").asText());
    } catch (RuntimeException exception) {
      throw contract("Agent 返回的 requestId 无效");
    }
    if (!requestId.equals(returnedRequestId)) throw contract("Agent 返回了不属于本次请求的候选池");
    if (result.path("size").asInt(-1) != size) throw contract("Agent 返回的赛事规模不一致");
  }

  private CandidatePoolResponse ready(UUID requestId, int size, JsonNode result, List<UUID> seedArtistIds) {
    var orderedIds = parseIds(result.path("recordingIds"));
    if (orderedIds.size() < size) throw contract("Agent 返回的候选歌曲不足以开赛");
    if (orderedIds.size() > size * 2) throw contract("Agent 返回的候选歌曲超过目标上限");
    if (new HashSet<>(orderedIds).size() != orderedIds.size()) throw contract("Agent 返回了重复歌曲");

    var found = recordings.findByIdInWithArtist(orderedIds);
    if (found.size() != orderedIds.size()) throw contract("Agent 返回了本地目录中不存在的歌曲");
    var byId = new HashMap<UUID, Recording>();
    found.forEach(recording -> byId.put(recording.getId(), recording));
    if (orderedIds.stream().anyMatch(id -> !byId.containsKey(id))) {
      throw contract("Agent 返回了本地目录中不存在的歌曲");
    }
    validateLockedScope(result.path("intentPolicy"), found, seedArtistIds);

    var explanations = explanationsByRecordingId(result.path("items"), orderedIds);
    var items = orderedIds.stream().map(id -> toItem(byId.get(id), explanations.get(id))).toList();
    var reserveSize = orderedIds.size() - size;
    var warnings = normalizedWarnings(result.path("warnings"));
    if (reserveSize < size) {
      warnings.putIfAbsent(
          "RESERVE_CANDIDATES_INSUFFICIENT",
          new WarningView("RESERVE_CANDIDATES_INSUFFICIENT", "已满足开赛数量，但候补歌曲少于目标数量。"));
    }
    return new CandidatePoolResponse(
        READY,
        new CandidatePoolView(
            requestId,
            size,
            reserveSize,
            orderedIds,
            summary(result, "候选池已通过歌曲目录校验。"),
            items,
            List.copyOf(warnings.values()),
            text(result.path("intentPolicy"), "intentMode"),
            text(result, "terminationReason")), List.of());
  }

  private CandidatePoolResponse insufficient(UUID requestId, int size, JsonNode result) {
    var warnings = normalizedWarnings(result.path("warnings"));
    warnings.putIfAbsent(
        "INSUFFICIENT_CANDIDATES",
        new WarningView("INSUFFICIENT_CANDIDATES", "可验证歌曲不足，请调整兴趣方向或选择更小的赛事规模。"));
    // A failed pool is not a playable product artifact.  Do not leak a partial,
    // potentially misleading list through the public contract; the next run
    // performs fresh verification and only then publishes candidates.
    var orderedIds = List.<UUID>of();
    var partialItems = List.<CandidateItemView>of();
    return new CandidatePoolResponse(
        INSUFFICIENT,
        new CandidatePoolView(
            requestId,
            size,
            Math.max(0, orderedIds.size() - size),
            orderedIds,
            summary(result, "已尝试可用目录与外部扩展，但可验证歌曲仍不足以开赛。"),
            partialItems,
            List.copyOf(warnings.values()),
            text(result.path("intentPolicy"), "intentMode"),
            text(result, "terminationReason")), List.of());
  }

  private CandidatePoolResponse clarification(UUID requestId, int size, JsonNode result) {
    var items = new ArrayList<ClarificationView>();
    if (result.path("clarifications").isArray()) for (var clarification : result.path("clarifications")) {
      var choices = new ArrayList<ArtistChoiceView>();
      if (clarification.path("candidates").isArray()) for (var candidate : clarification.path("candidates")) {
        var mbid = text(candidate, "mbid"); var name = text(candidate, "name");
        if (mbid != null && name != null) choices.add(new ArtistChoiceView(mbid, name, text(candidate, "country"), text(candidate, "type"), text(candidate, "disambiguation"), text(candidate, "begin"), text(candidate, "end")));
      }
      items.add(new ClarificationView(text(clarification, "mention"), List.copyOf(choices), text(clarification, "reason")));
    }
    return new CandidatePoolResponse(CLARIFICATION, null, List.copyOf(items));
  }

  private void validateLockedScope(JsonNode policy, List<Recording> found, List<UUID> seedArtistIds) {
    if (!"ARTIST_LOCKED".equals(policy.path("intentMode").asText())) return;
    var allowedIds = new HashSet<UUID>();
    if (!seedArtistIds.isEmpty()) {
      allowedIds.addAll(seedArtistIds);
    } else if (policy.path("allowedArtistIds").isArray()) {
      for (var node : policy.path("allowedArtistIds")) {
        try { allowedIds.add(UUID.fromString(node.asText())); }
        catch (RuntimeException exception) { throw contract("Agent 返回的限定艺人 ID 无效"); }
      }
    }
    var allowedNames = new HashSet<String>();
    // A user-selected/Java-resolved ID is authoritative. Model-produced names are
    // only a fallback when no trustworthy ID exists and must never widen the lock.
    if (allowedIds.isEmpty() && policy.path("allowedArtistNames").isArray()) {
      policy.path("allowedArtistNames").forEach(node -> allowedNames.add(normalize(node.asText())));
    }
    if (allowedIds.isEmpty() && allowedNames.isEmpty()) throw contract("限定艺人模式缺少可验证的艺人范围");
    var escaped = found.stream().anyMatch(recording ->
        !allowedIds.contains(recording.getArtist().getId())
            && !allowedNames.contains(normalize(recording.getArtist().getName())));
    if (escaped) throw contract("Agent 返回了限定艺人范围之外的歌曲");
  }

  private String normalize(String value) {
    return value == null ? "" : value.toLowerCase(java.util.Locale.ROOT).replaceAll("[^\\p{L}\\p{N}]", "");
  }

  private String text(JsonNode node, String field) {
    var value = node.path(field).asText("").trim();
    return value.isBlank() ? null : value;
  }

  private List<UUID> parseIds(JsonNode node) {
    if (!node.isArray()) throw contract("Agent 未返回候选歌曲 ID 列表");
    var result = new ArrayList<UUID>();
    for (var item : node) {
      try {
        result.add(UUID.fromString(item.asText()));
      } catch (RuntimeException exception) {
        throw contract("Agent 返回了无效的歌曲 ID");
      }
    }
    return result;
  }

  private Map<UUID, CandidateExplanation> explanationsByRecordingId(JsonNode node, List<UUID> orderedIds) {
    var allowed = new HashSet<>(orderedIds);
    var result = new HashMap<UUID, CandidateExplanation>();
    if (!node.isArray()) return result;
    for (var item : node) {
      try {
        var id = UUID.fromString(item.path("recordingId").asText());
        var reason = item.path("reason").asText("").trim();
        if (allowed.contains(id) && !reason.isBlank()) result.putIfAbsent(id, new CandidateExplanation(reason, rationales(item.path("explorationRationale")), evidence(item.path("evidenceSummary")), sources(item.path("discoverySources")), stringMap(item.path("qualityDimensions")), text(item,"poolRole"), text(item,"verificationStatus")));
      } catch (RuntimeException ignored) {
        // 展示理由不是歌曲事实；损坏的理由会被安全默认值替代。
      }
    }
    return result;
  }

  private List<RationaleView> rationales(JsonNode node) {
    if (!node.isArray()) return List.of();
    var values = new ArrayList<RationaleView>();
    for (var item : node) {
      var text = item.path("text").asText("").trim();
      if (!text.isBlank() && values.size() < 2) values.add(new RationaleView(text(item, "kind"), text.length() > 280 ? text.substring(0, 280) : text));
    }
    return List.copyOf(values);
  }

  private List<EvidenceView> evidence(JsonNode node) {
    if (!node.isArray()) return List.of();
    var values = new ArrayList<EvidenceView>();
    for (var item : node) {
      var url = text(item, "url");
      if (url != null && (url.startsWith("https://") || url.startsWith("http://")) && values.size() < 2) values.add(new EvidenceView(text(item, "title"), text(item, "domain"), url, text(item, "trustLevel")));
    }
    return List.copyOf(values);
  }

  private List<DiscoverySourceView> sources(JsonNode node) {
    if (!node.isArray()) return List.of();
    var values = new ArrayList<DiscoverySourceView>();
    for (var item : node) if (values.size() < 3) values.add(new DiscoverySourceView(text(item,"type"),text(item,"provider"),text(item,"url"),text(item,"query")));
    return List.copyOf(values);
  }

  private Map<String,String> stringMap(JsonNode node) {
    if (!node.isObject()) return Map.of();
    var values=new LinkedHashMap<String,String>();
    node.fields().forEachRemaining(item->values.put(item.getKey(),item.getValue().asText("")));
    return Map.copyOf(values);
  }

  private Map<String, WarningView> normalizedWarnings(JsonNode node) {
    var result = new LinkedHashMap<String, WarningView>();
    if (!node.isArray()) return result;
    for (var warning : node) {
      var code = warning.path("code").asText("").trim();
      var message = warning.path("message").asText("").trim();
      if (code.isBlank() || "WEB_SEARCH_UNUSED".equals(code)) continue;
      if ("MUSICBRAINZ_UNAVAILABLE".equals(code)) {
        code = "EXTERNAL_DISCOVERY_DEGRADED";
        message = "外部歌曲核验暂不可用，本次候选范围可能较少。";
      }
      if (message.isBlank()) message = "本次候选生成采用了降级结果。";
      result.putIfAbsent(code, new WarningView(code, message));
    }
    return result;
  }

  private CandidateItemView toItem(Recording recording, CandidateExplanation explanation) {
    var reason = explanation == null ? null : explanation.reason();
    return new CandidateItemView(
        recording.getId(),
        recording.getTitle(),
        recording.getArtist().getName(),
        Optional.ofNullable(recording.getAlbumTitle()).orElse(""),
      Optional.ofNullable(recording.getCoverUrl()).orElse(""),
      Optional.ofNullable(recording.getCoverStatus()).orElse("UNAVAILABLE"),
      Optional.ofNullable(recording.getCatalogSource()).orElse("LOCAL_SEED"),
      ListeningLinkFactory.neteaseSongSearch(recording.getTitle(), recording.getArtist().getName()),
      reason == null || reason.isBlank() ? "来自已验证的音乐目录。" : reason,
      explanation == null ? List.of() : explanation.rationales(),
      explanation == null ? List.of() : explanation.evidence(),
      explanation == null ? List.of() : explanation.sources(),
      explanation == null ? Map.of() : explanation.quality(),
      explanation == null || explanation.poolRole()==null ? "MAIN" : explanation.poolRole(),
      explanation == null || explanation.verificationStatus()==null ? "CATALOG_VERIFIED" : explanation.verificationStatus());
  }

  private String summary(JsonNode result, String fallback) {
    var value = result.path("candidateSummary").asText("").trim();
    return value.isBlank() ? fallback : value;
  }

  private CandidatePoolContractException contract(String message) {
    return new CandidatePoolContractException(message);
  }

  public record CandidatePoolResponse(String status, CandidatePoolView candidatePool, List<ClarificationView> clarifications) {}

  public record ClarificationView(String mention, List<ArtistChoiceView> candidates, String reason) {}
  public record ArtistChoiceView(String mbid, String name, String country, String type, String disambiguation, String begin, String end) {}

  public record CandidatePoolView(
      UUID requestId,
      int size,
      int reserveSize,
      List<UUID> recordingIds,
      String candidateSummary,
      List<CandidateItemView> items,
      List<WarningView> warnings,
      String intentMode,
      String terminationReason) {}

  public record CandidateItemView(
      UUID recordingId,
      String title,
      String artistName,
      String albumTitle,
      String coverUrl,
      String coverStatus,
      String catalogSource,
      String listeningSearchUrl,
      String reason,
      List<RationaleView> explorationRationale,
      List<EvidenceView> evidenceSummary,
      List<DiscoverySourceView> discoverySources,
      Map<String,String> qualityDimensions,
      String poolRole,
      String verificationStatus) {}

  public record RationaleView(String kind, String text) {}
  public record EvidenceView(String title, String domain, String url, String trustLevel) {}
  public record DiscoverySourceView(String type,String provider,String url,String query) {}
  private record CandidateExplanation(String reason, List<RationaleView> rationales, List<EvidenceView> evidence,List<DiscoverySourceView> sources,Map<String,String> quality,String poolRole,String verificationStatus) {}

  public record WarningView(String code, String message) {}
}
