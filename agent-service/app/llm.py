import asyncio
import json
from typing import Literal
from uuid import UUID
from langchain_openai import ChatOpenAI
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from .settings import settings
from .schemas import IntentPolicy
from .intent_rules import classify_intent_rule


class SelectedSong(BaseModel):
    recording_id: UUID
    reason: str = Field(min_length=8, max_length=120)


class Selection(BaseModel):
    selected: list[SelectedSong]
    candidate_summary: str = Field(min_length=12, max_length=240)


class RankingModel(BaseModel):
    model_config = ConfigDict(alias_generator=lambda name: name.split("_")[0] + "".join(part.title() for part in name.split("_")[1:]), populate_by_name=True)


class SelectionFactor(RankingModel):
    kind: Literal["scene", "mood", "genre", "artist_relation", "culture", "lyrics_theme", "creation_context", "language", "era"] = Field(validation_alias=AliasChoices("kind", "factor"))
    text: str = Field(min_length=4, max_length=100, validation_alias=AliasChoices("text", "detail", "reason", "description"))


class RankedSong(RankingModel):
    recording_id: UUID
    rank_score: int = Field(ge=0, le=100)
    ranking_reason: str = Field(min_length=30, max_length=120)
    selection_factors: list[SelectionFactor] = Field(min_length=1, max_length=3)


class RankingBatch(RankingModel):
    ranked: list[RankedSong] = Field(validation_alias=AliasChoices("ranked", "rankingBatch", "RankingBatch", "items"))


class RerankedCandidates(BaseModel):
    items: list[dict]
    candidate_summary: str
    model_batch_count: int = 0
    fallback_batch_count: int = 0


class CandidateDecision(BaseModel):
    action: Literal[
        "understand_preference", "resolve_named_entities", "request_clarification", "search_catalog", "expand_artist_catalog", "search_knowledge", "search_web",
        "resolve_musicbrainz", "rerank_candidates",
        "submit_candidates", "finish_insufficient",
    ]
    reason_code: Literal[
        "intent_policy_missing", "locked_artist_identity_unresolved",
        "verified_candidates_below_active_size", "verified_candidates_below_target",
        "local_scope_exhausted", "cross_artist_expansion_allowed",
        "external_hints_require_resolution", "candidate_pool_requires_rerank",
        "candidate_pool_ready_for_validation", "budget_or_stagnation_limit_reached",
    ]
    decision_summary: str = Field(min_length=4, max_length=120)
    query: str | None = Field(default=None, max_length=300)


class DiscoveryHint(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    artist_name: str = Field(min_length=1, max_length=200)
    source_url: str
    source_title: str = Field(default="", max_length=300)
    evidence_snippet: str = Field(min_length=8, max_length=200)
    query_purpose: str = Field(default="general", max_length=80)


class DiscoveryHints(BaseModel):
    hints: list[DiscoveryHint] = Field(default_factory=list)


class ExternalQuery(BaseModel):
    purpose: Literal["find_curated_song_lists", "find_adjacent_artists", "find_specific_works"]
    query: str = Field(min_length=4, max_length=300)


class ExternalQueryPlan(BaseModel):
    queries: list[ExternalQuery] = Field(default_factory=list, max_length=5)


class PreferenceHypothesis(BaseModel):
    dimension: Literal["scene", "mood", "genre", "language", "era", "culture"]
    value: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=1, max_length=120)
    confidence: Literal["low", "medium", "high"] = "medium"


class PreferenceHypotheses(BaseModel):
    hypotheses: list[PreferenceHypothesis] = Field(default_factory=list, max_length=8)


class NamedArtist(BaseModel):
    mention: str = Field(min_length=1, max_length=120)


class NamedArtistExtraction(BaseModel):
    artists: list[NamedArtist] = Field(default_factory=list, max_length=8)


def classify_intent_locally(preference: str, seed_artist_ids: list[UUID]) -> IntentPolicy:
    """Deterministic safety baseline; model output may enrich facets but cannot invent a lock."""
    return IntentPolicy.model_validate(classify_intent_rule(preference, [str(value) for value in seed_artist_ids]))


class DeepSeekCandidateSelector:
    def __init__(self):
        self.model = None if not settings.deepseek_api_key else ChatOpenAI(
            model=settings.llm_model, api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com", temperature=0.35,
        )

    async def infer_intent(self, preference: str, seed_artist_ids: list[UUID]) -> IntentPolicy:
        baseline = classify_intent_locally(preference, seed_artist_ids)
        if self.model is None:
            return baseline
        prompt = f"""识别用户生成歌曲世界杯候选池的范围意图，不输出思维链。
意图只有三种：
- ARTIST_LOCKED：用户明确说只要、仅限、个人世界杯、不要其他艺人；
- ARTIST_SEEDED：用户以艺人为起点但允许相近探索；
- OPEN_DISCOVERY：没有限定艺人，只描述情绪、场景、风格、年代或语言。
模糊时绝不能锁定，默认 ARTIST_SEEDED 或 OPEN_DISCOVERY。
用户输入：{preference}
前端明确选择的起点艺人 ID：{[str(item) for item in seed_artist_ids]}
提取输入中明确出现的艺人名称；不要凭记忆补充。evidenceSpans 只能引用用户短原文。
只输出符合 IntentPolicy 的 JSON，字段使用 camelCase。"""
        try:
            structured = self.model.with_structured_output(IntentPolicy)
            proposed = await structured.ainvoke(prompt)
        except Exception:
            return baseline
        # Deterministic exclusivity detection is the safety authority. The model may enrich, never invent a lock.
        if baseline.intent_mode == "ARTIST_LOCKED":
            proposed.intent_mode = "ARTIST_LOCKED"
            proposed.allowed_artist_ids = list(seed_artist_ids)
        elif proposed.intent_mode == "ARTIST_LOCKED":
            proposed.intent_mode = "ARTIST_SEEDED" if seed_artist_ids else "OPEN_DISCOVERY"
            proposed.allowed_artist_ids = []
        proposed.seed_artist_ids = list(seed_artist_ids)
        proposed.evidence_spans = [span for span in proposed.evidence_spans if span and span in preference][:5] or baseline.evidence_spans
        return proposed

    async def derive_hypotheses(self, preference: str, policy: IntentPolicy) -> list[dict]:
        """Build auditable search angles; they are hypotheses, not music facts."""
        baseline = _deterministic_hypotheses(preference, policy)
        if self.model is None:
            return baseline
        prompt = f"""从用户音乐偏好中提出最多 6 个可用于公开网页检索的假设，不输出思维链。
它们只能来自用户原文或意图策略，不得编造艺人、歌曲或事实。维度：scene,mood,genre,language,era,culture。
用户：{preference}
意图策略：{policy.model_dump(mode='json', by_alias=True)}
输出 PreferenceHypotheses JSON。"""
        try:
            parsed = await self.model.with_structured_output(PreferenceHypotheses).ainvoke(prompt)
            accepted = [item.model_dump() for item in parsed.hypotheses if item.evidence in preference]
            return accepted or baseline
        except Exception:
            return baseline

    async def extract_named_artists(self, preference: str) -> list[str]:
        """Extract literal artist mentions; MusicBrainz, not the model, resolves identity."""
        if self.model is None:
            return []
        prompt = f"""从用户音乐偏好文本中提取明确写出的歌手或乐队名称。不要推测、翻译、补全或输出作品名。
用户文本：{preference}
每个 mention 必须逐字出现在用户文本中。只输出符合 NamedArtistExtraction 的 JSON。"""
        try:
            parsed = await self.model.with_structured_output(NamedArtistExtraction).ainvoke(prompt)
            mentions = [item.mention for item in parsed.artists if item.mention in preference]
        except Exception:
            mentions = []
        return list(dict.fromkeys([*mentions, *_literal_artist_mentions(preference)]))[:8]

    async def decide(self, preference: str, target_count: int, active_count: int, state_summary: dict) -> CandidateDecision:
        if self.model is None:
            if not state_summary.get("intentPolicyReady"):
                return CandidateDecision(action="understand_preference", reason_code="intent_policy_missing", decision_summary="先形成可审计的用户意图策略")
            if not state_summary["catalogSearched"]:
                return CandidateDecision(action="search_catalog", reason_code="verified_candidates_below_active_size", decision_summary="先读取规范目录")
            if active_count < target_count and not state_summary["webSearched"]:
                return CandidateDecision(action="search_web", reason_code="verified_candidates_below_target", decision_summary="目录不足，搜索相近方向")
            if state_summary["unresolvedHintCount"] and not state_summary["musicBrainzCalled"]:
                return CandidateDecision(action="resolve_musicbrainz", reason_code="external_hints_require_resolution", decision_summary="解析网络发现歌曲")
            if not state_summary["ranked"]:
                return CandidateDecision(action="rerank_candidates", reason_code="candidate_pool_requires_rerank", decision_summary="对规范候选去重排序")
            return CandidateDecision(action="submit_candidates", reason_code="candidate_pool_ready_for_validation", decision_summary="提交通过校验的候选")
        prompt = f"""你是候选池生成 Agent 的 Supervisor，采用 ReAct：观察黑板后只决定下一项动作，不输出思维链。
目标：为 {target_count // 2} 首世界杯准备 {target_count} 首规范歌曲（前半 active，后半 reserve）。
用户兴趣：{preference}
当前规范候选数：{active_count}
黑板摘要：{json.dumps(state_summary, ensure_ascii=False)}
动作白名单：understand_preference, resolve_named_entities, request_clarification, search_catalog, expand_artist_catalog, search_knowledge, search_web, resolve_musicbrainz, rerank_candidates, submit_candidates, finish_insufficient。
reasonCode 白名单：intent_policy_missing, locked_artist_identity_unresolved, verified_candidates_below_active_size, verified_candidates_below_target, local_scope_exhausted, cross_artist_expansion_allowed, external_hints_require_resolution, candidate_pool_requires_rerank, candidate_pool_ready_for_validation, budget_or_stagnation_limit_reached。
规则：没有 intentPolicy 时先理解偏好；命名艺人尚未解析时优先 resolve_named_entities；存在未确认歧义时必须 request_clarification；对已解析的明确艺人，可自主选择 expand_artist_catalog 从 MusicBrainz 批量发现规范曲目；非锁定模式下应把明确艺人作为起点，并通过 search_web 主动寻找相近艺人、专辑或歌曲，再经 MusicBrainz 核验；最终歌曲必须有内部 recording ID；本地目录只是缓存补充；ARTIST_LOCKED 不能扩展到允许集合外；未达到等量 reserve 时不得提交 ready_for_confirmation；不要重复无收益动作。
仅输出 JSON：{{"action":"...","reason_code":"...","decision_summary":"不含思维链的简短理由","query":"可选检索词"}}"""
        response = await self.model.ainvoke(prompt)
        try:
            return CandidateDecision.model_validate(json.loads(self._clean_json(response.content)))
        except (json.JSONDecodeError, ValueError):
            return CandidateDecision(action="rerank_candidates" if active_count else "search_catalog", reason_code="candidate_pool_requires_rerank" if active_count else "verified_candidates_below_active_size", decision_summary="模型决策格式无效，采用安全动作")

    async def extract_discovery_hints(self, preference: str, sources: list[dict]) -> list[DiscoveryHint]:
        if self.model is None or not sources:
            return []
        compact = [{
            "url": item.get("sourceUrl"), "title": item.get("sourceTitle"),
            "summary": item.get("summary", "")[:700], "pageExcerpt": item.get("pageExcerpt", "")[:700],
            "queryPurpose": item.get("queryPurpose", "general"),
        } for item in sources]
        prompt = f"""从搜索资料中提取明确出现的歌曲名与艺人名，用于 MusicBrainz 解析。不得凭模型记忆补充，不确定就不输出。
用户方向：{preference}
资料：{json.dumps(compact, ensure_ascii=False)}
每项必须逐字引用资料中同时出现歌名和艺人名的 evidence_snippet（8–200 字）；source_url 必须逐字复制。最多 16 项。
仅输出 JSON：{{"hints":[{{"title":"歌曲名","artist_name":"艺人名","source_url":"原始URL","source_title":"来源标题","evidence_snippet":"原文片段","query_purpose":"查询目的"}}]}}"""
        response = await self.model.ainvoke(prompt)
        try:
            parsed = DiscoveryHints.model_validate(json.loads(self._clean_json(response.content))).hints
            return parsed or _deterministic_discovery_hints(preference, sources)
        except (json.JSONDecodeError, ValueError):
            return _deterministic_discovery_hints(preference, sources)

    async def plan_external_queries(self, preference: str, policy: IntentPolicy | None, suggested_query: str | None = None, hypotheses: list[dict] | None = None, prior_queries: list[dict] | None = None) -> list[ExternalQuery]:
        if self.model is None:
            return _complete_external_query_plan(preference, [], hypotheses, prior_queries)
        prompt = f"""为候选池生成 Agent 规划不超过 5 条公开网页检索词，不输出思维链。
目标是找到包含明确“歌曲名 + 艺人名”的公开推荐资料，供后续 MusicBrainz 核验；不得把检索结果当作歌曲事实。
用户偏好：{preference}
意图策略：{policy.model_dump(mode='json', by_alias=True) if policy else {}}
Supervisor 建议查询：{suggested_query or '无'}
可检验偏好假设：{json.dumps(hypotheses or [], ensure_ascii=False)}
已使用检索：{json.dumps(prior_queries or [], ensure_ascii=False)}
ARTIST_LOCKED 只能检索允许艺人的作品；其他模式必须在明确艺人检索之外，额外提出 1–2 条“相近艺人/专辑/歌曲”假设检索词。这些仅是待公开资料验证的探索方向，不能直接当作音乐事实。查询目的只能是 find_curated_song_lists、find_adjacent_artists、find_specific_works。
只输出 JSON：{{"queries":[{{"purpose":"...","query":"..."}}]}}"""
        try:
            structured = self.model.with_structured_output(ExternalQueryPlan)
            plan = await structured.ainvoke(prompt)
            return _complete_external_query_plan(preference, plan.queries, hypotheses, prior_queries)
        except Exception:
            fallback = [ExternalQuery(
                purpose="find_curated_song_lists",
                query=suggested_query or f"{preference} 推荐 歌曲 歌单",
            )]
            # LLM planning is optional; a transient model failure must not collapse
            # a multi-artist online discovery request into one unusably broad query.
            return _complete_external_query_plan(preference, fallback, hypotheses, prior_queries)

    async def select(self, preference: str, size: int, recordings: list[dict], intent_policy: IntentPolicy | None = None) -> Selection | None:
        if self.model is None: return None
        catalog = [{"recordingId": x["id"], "title": x["title"], "artist": x["artistName"], "album": x["albumTitle"]} for x in recordings]
        prompt = f"""你是音乐偏好候选池生成助手。只可从给定目录中选择 {size} 首歌，绝不能编造歌曲或 ID。
用户偏好：{preference}
意图策略：{intent_policy.model_dump(mode="json", by_alias=True) if intent_policy else {}}
目录：{catalog}
每首理由需具体连接歌曲元数据与本次偏好的艺人、场景、情绪、风格、语言或年代，20–80 个汉字；不要使用“符合你的偏好”“来自规范目录”等通用模板。
多样性服从意图：ARTIST_LOCKED 不得加入允许艺人外歌曲；其他模式不设置机械艺人比例。
只输出 JSON，不要 Markdown：{{"selected":[{{"recording_id":"uuid","reason":"中文理由"}}],"candidate_summary":"中文总结"}}。"""
        response = await self.model.ainvoke(prompt)
        content = self._clean_json(response.content)
        try:
            return Selection.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError):
            # A model reply must never make the user-facing workflow unavailable.
            # The graph will fall back to verified catalog records only.
            return None

    async def rerank_candidates(self, preference: str, recordings: list[dict], intent_policy: IntentPolicy | None = None) -> RerankedCandidates | None:
        """Score independent, verified batches concurrently, then merge deterministically.

        The caller owns whether this is invoked.  A failed batch gets a transparent
        catalog fallback; it never removes a verified song from a playable pool.
        """
        if self.model is None or not recordings:
            return None
        batches = [recordings[index:index + 16] for index in range(0, len(recordings), 16)]
        # Keep bounded parallelism: it retains the latency benefit of subtask
        # fan-out without causing the provider to reject four JSON calls at once.
        semaphore = asyncio.Semaphore(2)
        tasks = [self._rank_batch_with_retry(semaphore, preference, batch, intent_policy) for batch in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[dict] = []
        model_batches = fallback_batches = 0
        for batch, result in zip(batches, results):
            by_id = {str(item["id"]): item for item in batch}
            if isinstance(result, Exception) or result is None:
                fallback_batches += 1
                merged.extend(_fallback_ranked_items(batch, preference, intent_policy))
                continue
            proposed = {str(item.recording_id): item for item in result.ranked}
            if set(proposed) != set(by_id) or len(proposed) != len(batch):
                fallback_batches += 1
                merged.extend(_fallback_ranked_items(batch, preference, intent_policy))
                continue
            model_batches += 1
            for recording_id, item in by_id.items():
                suggestion = proposed[recording_id]
                merged.append(item | {
                    "rankScore": suggestion.rank_score,
                    "reason": suggestion.ranking_reason,
                    "rankingReason": suggestion.ranking_reason,
                    "selectionFactors": [factor.model_dump(by_alias=True) for factor in suggestion.selection_factors],
                    "explanationStatus": "MODEL_GENERATED",
                })
        merged.sort(key=lambda item: (-int(item.get("rankScore", 0)), str(item.get("title", "")), str(item.get("id", ""))))
        summary = "候选已按本次偏好与可核验音乐资料重新排序，可在主池与候补中继续取舍。"
        if fallback_batches:
            summary += " 部分入选理由采用了目录说明。"
        return RerankedCandidates(items=merged, candidate_summary=summary, model_batch_count=model_batches, fallback_batch_count=fallback_batches)

    async def _rank_batch_with_retry(self, semaphore: asyncio.Semaphore, preference: str, recordings: list[dict], intent_policy: IntentPolicy | None) -> RankingBatch:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with semaphore:
                    return await asyncio.wait_for(self._rank_batch(preference, recordings, intent_policy), timeout=22)
            except Exception as error:
                last_error = error
                if attempt == 0:
                    await asyncio.sleep(0.35)
        assert last_error is not None
        raise last_error

    async def _rank_batch(self, preference: str, recordings: list[dict], intent_policy: IntentPolicy | None) -> RankingBatch:
        catalog = [{
            "recordingId": item["id"], "title": item.get("title", ""), "artistName": item.get("artistName", ""),
            "albumTitle": item.get("albumTitle", ""), "catalogSource": item.get("catalogSource", "EXTERNAL_VERIFIED"),
            "evidenceSummary": _ranking_evidence(item), "themeClaims": item.get("themeClaims", []),
        } for item in recordings]
        prompt = f"""你是候选歌曲池 Agent 的并行排序子任务。只处理给定的已核验歌曲，不输出思维链。
用户本次偏好：{preference}
意图策略：{intent_policy.model_dump(mode='json', by_alias=True) if intent_policy else {}}
候选事实：{json.dumps(catalog, ensure_ascii=False)}
对每一首候选都输出一次。rankScore 为 0-100 的相对贴合排序信号；rankingReason 用 30-100 字，连接用户偏好与给定元数据、公开来源摘要或审核主题卡。不得从记忆补充歌曲、歌词、创作背景或风格事实；未知就用审慎的推荐性表述。selectionFactors 为 1-3 条，仅能使用 scene,mood,genre,artist_relation,culture,lyrics_theme,creation_context,language,era，文字必须可由输入事实或用户原文支持。只输出 RankingBatch JSON。"""
        # DeepSeek's OpenAI-compatible endpoint is reliable for plain JSON but
        # does not consistently accept the schema mechanism used by
        # ``with_structured_output``.  Keep validation local and treat invalid
        # replies as an individual batch fallback instead of losing the pool.
        response = await self.model.ainvoke(prompt)
        return RankingBatch.model_validate(_normalize_ranking_payload(json.loads(self._clean_json(response.content))))

    @staticmethod
    def _clean_json(content: str) -> str:
        content = content.strip()
        if content.startswith("```json") and content.endswith("```"):
            return content[7:-3].strip()
        if content.startswith("```") and content.endswith("```"):
            return content[3:-3].strip()
        return content


def _ranking_evidence(item: dict) -> list[str]:
    values = []
    if item.get("evidenceSnippet"):
        values.append(str(item["evidenceSnippet"])[:240])
    for source in item.get("evidenceSummary", [])[:2]:
        if isinstance(source, dict) and source.get("title"):
            values.append(str(source["title"])[:120])
    return values[:2]


def _normalize_ranking_payload(payload: dict) -> dict:
    """Accept harmless naming drift from JSON-mode providers before validation."""
    if not isinstance(payload, dict):
        return payload
    ranked = next((value for key, value in payload.items() if key.lower() in {"ranked", "rankingbatch", "items", "candidaterankings"} and isinstance(value, list)), None)
    if ranked is None:
        return payload
    for song in ranked:
        if not isinstance(song, dict):
            continue
        factors = song.get("selectionFactors") or song.get("selection_factors") or []
        for factor in factors:
            if not isinstance(factor, dict):
                continue
            factor.setdefault("kind", factor.get("factor"))
            factor.setdefault("text", next((factor.get(key) for key in ("detail", "reason", "description", "evidence") if factor.get(key)), ""))
    return {"ranked": ranked}


def _fallback_ranked_items(recordings: list[dict], preference: str, policy: IntentPolicy | None) -> list[dict]:
    facets = policy.preference_facets if policy else None
    terms = ((facets.mood + facets.scene + facets.genre + facets.language + facets.era)[:2] if facets else [])
    direction = "、".join(terms) or "本次音乐方向"
    result = []
    for index, item in enumerate(recordings):
        reason = f"《{item.get('title', '这首歌')}》是已核验的 {item.get('artistName', '艺人')} 作品，可作为与你所说的{direction}进行比较的一席。"
        result.append(item | {
            "rankScore": 50 - index,
            "reason": reason,
            "rankingReason": reason,
            "selectionFactors": [{"kind": "artist_relation", "text": "基于已核验艺人与本次偏好范围入池。"}],
            "explanationStatus": "CATALOG_FALLBACK",
        })
    return result


def _literal_artist_mentions(preference: str) -> list[str]:
    import re
    match = re.search(r"(?:我喜欢|喜欢|常听|爱听)\s*([^。！？；\n]{1,160})", preference)
    if not match:
        return []
    # Stop when the sentence moves from named artists to a request/scene, then
    # accept Chinese conjunctions without requiring spaces. The former parser
    # read “艾怡良和郑宜农” as one artist and lost two usable MusicBrainz seeds.
    artist_clause = re.split(
        r"[,，](?=(?:想|希望|要|请|可以|适合|用于|并|但|然后|探索|寻找|做|来))",
        match.group(1), maxsplit=1,
    )[0]
    artist_clause = artist_clause.split("的歌曲")[0].split("的歌")[0]
    raw = re.split(r"[、,，/]|(?:和|与|及)", artist_clause)
    non_artist_terms = {"克制", "温柔", "忧郁", "明亮", "治愈", "孤独", "浪漫", "热烈", "安静", "冷峻", "中文", "华语", "独立音乐", "民谣", "摇滚", "电子", "爵士", "流行", "说唱"}
    return [value.strip() for value in raw if 1 < len(value.strip()) <= 60 and value.strip() not in non_artist_terms]


def _complete_external_query_plan(preference: str, planned: list[ExternalQuery], hypotheses: list[dict] | None = None, prior_queries: list[dict] | None = None) -> list[ExternalQuery]:
    # The Supervisor remains free to decide *whether* to search.  Once it has chosen
    # online discovery, a thin query-planning sub-agent must cover each explicit artist
    # rather than relying on one broad mixed-name query that search engines cannot rank.
    literal_artists = _literal_artist_mentions(preference)
    artist_queries: list[ExternalQuery] = [
        ExternalQuery(purpose="find_specific_works", query=f"{artist} 代表作 歌曲 曲目")
        for artist in literal_artists
    ]
    # Explicit names have higher evidence value than a generic recommendation query.
    # Keep model-proposed queries too, after those direct discovery probes.
    result = artist_queries[:5]
    hypothesis_values = [str(item["value"]).strip() for item in (hypotheses or []) if item.get("value")]
    is_chinese_music = any(value in preference for value in ("中文", "华语", "華語", "国语", "國語"))
    # These do not choose music or force a workflow.  They only ask the public
    # search engine for evidence-rich result shapes (playlist rows that contain
    # both a work and its performer) once the ReAct supervisor has decided that
    # online discovery is warranted.
    evidence_queries: list[ExternalQuery] = []
    if is_chinese_music:
        terms = " ".join(hypothesis_values[:3]) or preference
        evidence_queries = [
            ExternalQuery(purpose="find_curated_song_lists", query=f"{terms} 华语 歌单 曲目 歌手 推荐"),
            ExternalQuery(purpose="find_curated_song_lists", query=f"{terms} 华语 歌曲 歌名 歌手 推荐"),
            ExternalQuery(purpose="find_curated_song_lists", query=f"site:music.163.com {terms} 华语 歌单 曲目"),
        ]
    hypothesis_queries = [ExternalQuery(purpose="find_curated_song_lists", query=f"{item['value']} 推荐歌曲 歌单") for item in (hypotheses or []) if item.get("value")]
    # Preserve room for the model's own planned angles while ensuring an open
    # Chinese preference can actually surface auditable track/artist pairs.
    fallbacks: list[ExternalQuery] = [*planned[:2], *evidence_queries, *planned[2:], *hypothesis_queries, *[
        ExternalQuery(purpose="find_curated_song_lists", query=f"{preference} 推荐歌曲 歌单"),
        ExternalQuery(purpose="find_specific_works", query=f"{preference} 必听歌曲 推荐"),
        ExternalQuery(purpose="find_adjacent_artists", query=f"{preference} 相似歌手 推荐"),
    ]]
    seen = {item.query for item in result} | {str(item.get("query", "")) for item in (prior_queries or [])}
    for item in fallbacks:
        if len(result) >= 5: break
        if item.query not in seen: result.append(item); seen.add(item.query)
    if not result:
        result.append(ExternalQuery(purpose="find_curated_song_lists", query=f"{preference} 音乐媒体 推荐曲目"))
    return result


def _deterministic_hypotheses(preference: str, policy: IntentPolicy) -> list[dict]:
    facets = policy.preference_facets.model_dump()
    result = []
    for dimension in ("scene", "mood", "genre", "language", "era"):
        for value in facets.get(dimension, [])[:2]:
            result.append({"dimension": dimension, "value": value, "evidence": value, "confidence": "high"})
    # Preserve a cultural-language angle even where the rule dictionary has no exact term.
    for phrase in ("城市漂泊", "长大", "失落感", "青春", "孤独"):
        if phrase in preference:
            result.append({"dimension": "culture", "value": phrase, "evidence": phrase, "confidence": "medium"})
    return result[:6]


def _deterministic_discovery_hints(preference: str, sources: list[dict]) -> list[DiscoveryHint]:
    """Extract only explicitly paired artist/title facts from public search evidence.

    This is deliberately a parser rather than a recommendation rule: the ReAct
    supervisor still decides whether a web search and a MusicBrainz resolution
    are useful.  It simply prevents a generative extraction call from losing
    well-formed facts already present in a playlist snippet (a common result
    shape for YouTube, Spotify and NetEase Cloud Music).
    """
    import re
    hints: list[DiscoveryHint] = []
    seen: set[tuple[str, str]] = set()

    def clean(value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip(" \t\n\r《》〈〉『』\"'|｜;；,，")
        value = re.sub(r"\s*\([^)]{0,40}\)\s*$", "", value).strip()
        return value[:160]

    def add(title_value: str, artist_value: str, source: dict, evidence: str) -> None:
        title_value, artist_value = clean(title_value), clean(artist_value)
        # A paired fact must have meaningful values on both sides.  The later
        # MusicBrainz exact-match lookup remains the identity authority.
        if not title_value or not artist_value or len(title_value) > 120 or len(artist_value) > 120:
            return
        rejected_markers = ("这首歌", "主題曲", "主题曲", "收录", "精選集", "精选集", "单曲", "單曲", "playlist", "song and lyrics", "官方版", "read more", "various artists")
        if any(marker.casefold() in title_value.casefold() or marker.casefold() in artist_value.casefold() for marker in rejected_markers):
            return
        if title_value.casefold() == artist_value.casefold():
            return
        key = (artist_value.casefold(), title_value.casefold())
        if key in seen:
            return
        seen.add(key)
        snippet = clean(evidence)
        if len(snippet) < 8:
            snippet = clean(f"{source.get('sourceTitle', '')} {evidence}")
        hints.append(DiscoveryHint(
            title=title_value, artist_name=artist_value,
            source_url=str(source.get("sourceUrl", "")),
            source_title=str(source.get("sourceTitle", ""))[:300],
            evidence_snippet=snippet[:200],
            query_purpose=str(source.get("queryPurpose", "general")),
        ))

    for source in sources:
        title = str(source.get("sourceTitle", ""))
        summary = str(source.get("summary", ""))
        corpus = f"{title}\n{summary}\n{str(source.get('pageExcerpt', ''))}"
        # Chinese editorial lists often use “《歌名》：歌手某某”。  This is
        # a direct public title/artist assertion, so retain it as evidence for
        # the later MusicBrainz identity check.
        for match in re.finditer(r"《([^》\n]{1,100})》\s*[:：]\s*(?:歌手|演唱|演唱者)?\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9&.'’· ]{1,80})(?=\s*(?:[。；;《\n]|$))", corpus):
            add(match.group(1), match.group(2), source, match.group(0))
        # 《歌名》歌手 / 〈歌名〉歌手, including playlist timecode rows.
        for match in re.finditer(r"[《〈]([^》〉\n]{1,100})[》〉]\s*(?:[-—–｜|]\s*)?([\u4e00-\u9fffA-Za-z0-9&.'’·\- ]{2,100})", corpus):
            add(match.group(1), match.group(2).split("\n")[0], source, match.group(0))
        # Artist - title is the prevalent editorial/list style.  Normalize
        # numbered rows first, then require the right hand side to terminate
        # at another row or a real separator; without that boundary an entire
        # one-line social-media list would be misread as one giant song title.
        row_corpus = re.sub(r"\s+(?=\d{1,2}[.、]\s*)", "\n", corpus)
        for match in re.finditer(r"(?:^|[\n;；])\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9&.'’· ]{1,70})\s*[-—–]\s*([^\n;；|｜]{1,100}?)(?=\s*(?:[\n;；]|$))", row_corpus):
            add(match.group(2), match.group(1), source, match.group(0))
        # Catalog pages frequently render 'title - artist - 单曲'.  Interpret
        # that explicitly before the generic pair parser can reverse it.
        for match in re.finditer(r"(?:^|[\n;；])\s*([^\n;；|｜]{1,100}?)\s*[-—–]\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9&.'’· ]{1,70})\s*[-—–]\s*(?:单曲|歌曲|song)(?=\s*(?:[\n;；]|$))", row_corpus, re.I):
            add(match.group(1), match.group(2), source, match.group(0))
        # Spotify and similar public pages present 'track · artist'.
        for match in re.finditer(r"(?:^|[\n;；])\s*([^\n;；·]{1,100})\s*·\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9&.'’·, ]{1,100})", corpus):
            add(match.group(1), match.group(2), source, match.group(0))
        # A frequent Chinese list form is '《歌名》歌手' without a separator.
        for match in re.finditer(r"《([^》\n]{1,100})》\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9&.'’· ]{1,60})(?=\s*(?:;|；|\n|$))", corpus):
            add(match.group(1), match.group(2), source, match.group(0))
        if len(hints) >= 48:
            return hints[:48]
    return hints
