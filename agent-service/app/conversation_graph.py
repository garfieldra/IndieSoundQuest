from __future__ import annotations

import json
import re
from typing import Literal, TypedDict
from urllib.parse import quote
from uuid import uuid4

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from .schemas import CandidatePoolRequest, CandidatePoolResult, ConfirmedArtist, ConversationAgentRequest, ConversationAgentResult, ConversationCardIntent, ConversationResumeRequest
from .settings import settings
from .tools import KnowledgeSearchTool, LastFmResearchTool, MusicCatalogTool, WebSearchTool, WikimediaResearchTool
from .registry import SkillRegistry, ToolRegistry, skill_registry, tool_registry
from .music_analysis import (
    EvidenceClaim,
    MusicAnalysisPlan,
    MusicAnalysisReview,
    MusicAnalysisDraft,
    MusicAnalysisWorkspace,
    apply_claim_reviews,
    assess_analysis_evidence,
    classify_source_quality,
    deep_analysis_intent,
    inherited_analysis_sources,
    initial_analysis_workspace,
    latest_analysis_context,
    sanitize_claims,
    workspace_after_research,
    workspace_with_coverage,
)


class ConversationDecision(BaseModel):
    action: Literal["search_web", "read_source", "research_musicbrainz", "search_wikimedia", "search_lastfm", "search_knowledge", "recommend_music", "analyze_music", "clarify", "propose_tournament", "build_candidate_pool", "generate_exploration_report", "respond"]
    public_summary: str = Field(min_length=4, max_length=100)
    query: str | None = Field(default=None, max_length=300)
    source_ref: str | None = Field(default=None, max_length=20)
    entity_type: Literal["artist", "recording", "release-group", "work"] = "recording"
    entity_mbid: str | None = Field(default=None, max_length=60)
    lastfm_mode: Literal["similar_artists", "top_tracks", "artist_tags", "similar_tracks", "track_tags"] = "similar_artists"
    artist_name: str | None = Field(default=None, max_length=180)
    track_title: str | None = Field(default=None, max_length=180)
    clarification_question: str | None = Field(default=None, max_length=300)
    clarification_reason: str | None = Field(default=None, max_length=240)
    clarification_options: list[str] = Field(default_factory=list, max_length=8)


class MusicPreferenceProfile(BaseModel):
    named_artists: list[str] = Field(default_factory=list, max_length=8)
    genres: list[str] = Field(default_factory=list, max_length=8)
    sonic_traits: list[str] = Field(default_factory=list, max_length=8)
    moods: list[str] = Field(default_factory=list, max_length=8)
    scenes: list[str] = Field(default_factory=list, max_length=6)
    languages_regions: list[str] = Field(default_factory=list, max_length=6)
    eras: list[str] = Field(default_factory=list, max_length=5)
    lyrical_themes: list[str] = Field(default_factory=list, max_length=8)
    familiarity: Literal["representative", "deep_cuts", "mixed"] = "mixed"
    exclusions: list[str] = Field(default_factory=list, max_length=8)
    discovery_axes: list[str] = Field(default_factory=list, max_length=6)
    evidence_spans: list[str] = Field(default_factory=list, max_length=8)


class ConversationState(TypedDict, total=False):
    request: ConversationAgentRequest
    decision: ConversationDecision
    action_history: list[dict]
    observations: list[dict]
    web_sources: list[dict]
    knowledge: list[dict]
    iteration: int
    preference_profile: dict
    entity_resolutions: list[dict]
    entity_ambiguities: list[dict]
    research_assessment: dict
    search_attempts: int
    analysis_queries: list[str]
    analysis_workspace: dict
    evidence_coverage: dict
    read_source_refs: list[str]
    applied_intervention_sequence: int
    intervention_revision: int
    result: ConversationAgentResult


class RecommendedSongDraft(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    artist_name: str = Field(min_length=1, max_length=180)
    reason: str = Field(min_length=8, max_length=180)
    source_url: str = Field(min_length=8, max_length=1000)


class RecommendedArtistDraft(BaseModel):
    artist_name: str = Field(min_length=1, max_length=180)
    reason: str = Field(min_length=8, max_length=180)
    source_url: str = Field(min_length=8, max_length=1000)


class MusicRecommendationDraft(BaseModel):
    summary: str = Field(min_length=20, max_length=360)
    # Generate a slightly wider evidence-bound working set because a subset can
    # legitimately fail MusicBrainz identity resolution.  Only 5–7 verified
    # tracks are exposed in the user-facing card below.
    songs: list[RecommendedSongDraft] = Field(default_factory=list, max_length=12)
    artists: list[RecommendedArtistDraft] = Field(default_factory=list, max_length=5)


class ConversationReActRuntime:
    """Unified ReAct music exploration Agent; candidate/report work is delegated as composite tools."""

    def __init__(
        self, web: WebSearchTool, knowledge: KnowledgeSearchTool,
        tools: ToolRegistry = tool_registry, skills: SkillRegistry = skill_registry,
        candidate_graph=None, catalog: MusicCatalogTool | None = None,
        wikimedia: WikimediaResearchTool | None = None,
        lastfm: LastFmResearchTool | None = None,
    ):
        self.web = web
        self.knowledge = knowledge
        self.tools = tools
        self.skills = skills
        self.candidate_graph = candidate_graph
        self.catalog = catalog
        self.wikimedia = wikimedia or WikimediaResearchTool()
        self.lastfm = lastfm or LastFmResearchTool()
        self.model = None if not settings.deepseek_api_key else ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0.35,
            extra_body={"thinking": {"type": "disabled"}},
            max_retries=1,
        )
        self.graph = self._build().compile(checkpointer=MemorySaver())

    async def _invoke_validated(self, schema, prompt: str):
        """Use native structured output, then retry as validated plain JSON.

        DeepSeek's OpenAI-compatible endpoint can occasionally fail to finish
        larger function-calling payloads. The compatibility path remains safe:
        ordinary model text is parsed and validated against the same Pydantic
        schema before it can cross the service boundary.
        """
        try:
            value = await self.model.with_structured_output(
                schema, method="function_calling"
            ).ainvoke(prompt)
            if isinstance(value, schema):
                return value
            return schema.model_validate(value)
        except Exception as structured_error:
            json_prompt = (
                prompt
                + "\n仅返回一个 JSON 对象，不要 Markdown 代码块。JSON 必须满足以下 Schema：\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            )
            try:
                response = await self.model.ainvoke(json_prompt)
                return schema.model_validate(_parse_model_json(response.content))
            except Exception as json_error:
                raise RuntimeError(
                    f"{schema.__name__} validation failed: "
                    f"{type(structured_error).__name__}/{type(json_error).__name__}"
                ) from json_error

    async def analyze_preference(self, request: ConversationAgentRequest) -> MusicPreferenceProfile:
        literal_artists = _explicit_artist_mentions(request.user_message)
        if self.model is None:
            return _fallback_preference_profile(request, literal_artists)
        prompt = f"""你是音乐偏好解析工具，不输出思维链。把本轮请求和必要的近期上下文整理为结构化 MusicPreferenceProfile。
当前消息：{request.user_message}
会话摘要：{request.summary}
最近消息：{request.recent_messages[-12:]}
字面提取的艺人：{json.dumps(literal_artists, ensure_ascii=False)}
要求：namedArtists 只能保留用户实际点名的艺人；声音、情绪、歌词主题和 discoveryAxes 可以根据点名艺人的公开常识作保守推断，但不得写成人格或教育背景事实。evidenceSpans 必须来自用户原话。"""
        try:
            profile = await self.model.with_structured_output(MusicPreferenceProfile, method="function_calling").ainvoke(prompt)
            if not isinstance(profile, MusicPreferenceProfile):
                raise TypeError("invalid preference profile")
        except Exception:
            return _fallback_preference_profile(request, literal_artists)
        profile.named_artists = list(dict.fromkeys([*literal_artists, *[item for item in profile.named_artists if item in request.user_message]]))[:8]
        return profile

    async def plan_analysis_workspace(self, request: ConversationAgentRequest) -> MusicAnalysisWorkspace:
        """Let the unified Agent revise the research workspace for this turn.

        The result describes goals and gaps only. It deliberately does not
        prescribe an ordered tool pipeline.
        """
        fallback = initial_analysis_workspace(request)
        if self.model is None:
            return fallback
        previous = latest_analysis_context(request)
        prompt = f"""你是统一音乐 Agent 的研究规划能力，不输出思维链，也不要生成固定执行步骤。
根据本轮消息、近期对话和上一张已持久化分析卡，修订一个音乐分析工作台。你只决定研究主题、真正相关的分析维度、待核验问题和可选下一动作；具体调用哪个工具仍由后续 ReAct 决策器自主决定。

本轮消息：{request.user_message}
会话摘要：{request.summary}
近期对话：{request.recent_messages[-12:]}
上一轮分析卡：{json.dumps(previous, ensure_ascii=False)}

要求：
- “具体展开”“为什么”“和另一首比较”等省略主语的追问必须继承上一轮 subject，禁止把追问句本身误当成歌曲名；
- 用户明确换了歌曲、版本或艺人时，以本轮为准；
- selectedDimensions 只选择真正能回答本轮问题的 1–5 个维度，不套固定模板；
- openQuestions 表示仍需证据回答的问题；possibleNextActions 只是候选动作，不构成顺序；
- 不得虚构歌词、音频参数、采访或创作事实。
返回 MusicAnalysisPlan。"""
        try:
            plan = await self._invoke_validated(MusicAnalysisPlan, prompt)
        except Exception:
            return fallback
        previous_payload = previous.get("payload", {})
        # A terse follow-up cannot silently switch away from the persisted
        # subject merely because the model paraphrased it poorly.
        if previous and not re.search(r"《[^》]+》", request.user_message):
            plan.subject = fallback.subject
        selected_dimensions = list(dict.fromkeys([
            *fallback.selected_dimensions, *plan.selected_dimensions,
        ]))[:5]
        return MusicAnalysisWorkspace(
            subject=plan.subject,
            question=plan.resolved_question,
            working_thesis=plan.working_thesis or fallback.working_thesis,
            selected_dimensions=selected_dimensions,
            observations=fallback.observations,
            claims=fallback.claims,
            open_questions=plan.open_questions,
            uncertainties=list(previous_payload.get("uncertainties") or [])[:8],
            possible_next_actions=plan.possible_next_actions,
            evidence_source_count=fallback.evidence_source_count,
            revision=fallback.revision,
        )

    async def resolve_entities(
        self, mentions: list[str], confirmed_artists: list[ConfirmedArtist] | None = None
    ) -> tuple[list[dict], list[dict]]:
        if not mentions or self.catalog is None:
            return [], []
        confirmed = {_music_key(item.mention): item for item in (confirmed_artists or [])}
        pre_resolved = [{
            "mention": mention, "status": "RESOLVED", "mbid": str(confirmed[_music_key(mention)].mbid),
            "name": confirmed[_music_key(mention)].name, "confirmedByUser": True,
        } for mention in mentions if _music_key(mention) in confirmed]
        unresolved_mentions = [mention for mention in mentions if _music_key(mention) not in confirmed]
        if not unresolved_mentions:
            return pre_resolved, []
        try:
            raw = await self.catalog.resolve_artist_candidates(unresolved_mentions)
        except Exception:
            return [], []
        resolved: list[dict] = list(pre_resolved)
        ambiguities: list[dict] = []
        resolved_mbids: set[str] = {str(item.get("mbid") or "") for item in pre_resolved if item.get("mbid")}
        for item in raw:
            candidates = list(item.get("candidates") or [])
            mention = str(item.get("mention") or "")
            if not candidates:
                resolved.append({"mention": mention, "status": "UNRESOLVED", "reason": item.get("reason")})
                continue
            top = candidates[0]
            top_score = int(top.get("score") or 0)
            runner_score = int(candidates[1].get("score") or 0) if len(candidates) > 1 else 0
            confident = top_score >= 95 and (len(candidates) == 1 or top_score - runner_score >= 8)
            if confident:
                mbid = str(top.get("mbid") or "")
                resolved.append({
                    "mention": mention, "status": "RESOLVED", "mbid": mbid,
                    "name": str(top.get("name") or mention), "duplicateIdentity": bool(mbid and mbid in resolved_mbids),
                })
                if mbid:
                    resolved_mbids.add(mbid)
            else:
                ambiguities.append({
                    "mention": mention,
                    "candidates": [{
                        "mbid": str(candidate.get("mbid") or ""), "name": str(candidate.get("name") or ""),
                        "country": candidate.get("country"), "type": candidate.get("type"),
                        "disambiguation": candidate.get("disambiguation"), "score": int(candidate.get("score") or 0),
                    } for candidate in candidates if int(candidate.get("score") or 0) >= max(80, top_score - 7)],
                })
        return resolved, ambiguities

    async def decide(self, state: ConversationState) -> ConversationDecision:
        request = state["request"]
        forced = getattr(request, "forced_action", None)
        if forced in {"build_candidate_pool", "generate_exploration_report", "propose_tournament", "clarify", "respond"}:
            summaries = {
                "build_candidate_pool": "开始构建并核验歌曲世界杯候选池",
                "generate_exploration_report": "整理当前对话中的偏好线索并生成探索报告",
                "propose_tournament": "偏好方向已经明确，准备歌曲世界杯入口",
                "clarify": "还需要一个更明确的音乐起点",
                "respond": "结合当前对话整理音乐回应",
            }
            return ConversationDecision(action=forced, public_summary=summaries[forced])
        ambiguities = state.get("entity_ambiguities", [])
        if ambiguities and not any(item["action"] == "clarify" for item in state.get("action_history", [])):
            return _entity_clarification_decision(ambiguities)
        if self.model is None:
            return _fallback_decision(state)
        observation_summary = {
            "actions": [item["action"] for item in state.get("action_history", [])],
            "webSourceCount": len(state.get("web_sources", [])),
            "knowledgeCount": len(state.get("knowledge", [])),
            "iteration": state.get("iteration", 0),
            "researchAssessment": state.get("research_assessment", {}),
            "entityResolutions": state.get("entity_resolutions", []),
            "preferenceProfile": state.get("preference_profile", {}),
            "analysisWorkspace": state.get("analysis_workspace", {}),
            "analysisEvidenceCoverage": state.get("evidence_coverage", {}),
            "analysisSources": [{
                "ref": f"S{index}", "title": item.get("sourceTitle"), "url": item.get("sourceUrl"),
                "summary": str(item.get("summary") or "")[:500],
                "hasFullTextExcerpt": bool(item.get("pageExcerpt")),
            } for index, item in enumerate(state.get("web_sources", [])[:16], start=1)],
            "readSourceRefs": state.get("read_source_refs", []),
            "toolAvailability": {
                "musicbrainzResearch": self.catalog is not None,
                "wikimedia": self.wikimedia.enabled,
                "lastfm": self.lastfm.enabled,
            },
        }
        prompt = f"""你是 IndieSoundQuest 对话运行时的 ReAct 决策器，不输出思维链。
你是统一音乐探索 Agent。世界杯是可选 Skill，不是产品主流程；候选池构建、赛事分析和报告撰写是可调用的复合 Tool，而不是独立 Agent。
你只能选择一个动作：
- search_web：问题需要当前公开音乐资料、外部事实或具体推荐线索；用户要求推荐、相似艺人/歌曲或风格延展时，应先搜索再回答，除非当前观察中已有足够公开资料；
- read_source：已有搜索来源，但摘要不足以支持创作背景、采访、制作署名或关键事实时，选择最有价值的来源精读；在 source_ref 填写 analysisSources 中的 S1、S2 等引用。不要重复读取 readSourceRefs 中的来源；
- research_musicbrainz：需要核对作品/录音版本、ISRC、发行、创作署名、合作人员或规范实体关系时使用。entity_type 选择 artist、recording、release-group 或 work；已知 MBID 时填写 entity_mbid，否则必须尽量把 artist_name 与 track_title 分开填写，query 只作为补充；它提供目录事实，不替代乐评与采访；
- search_wikimedia：需要艺人身份、别名、乐队成员、地区、年代、作品时间线或音乐史背景时使用。它是百科语境来源，不能单独证明主观音乐评价；
- search_lastfm：需要相似艺人、相似歌曲、热门曲目或听众标签时使用。填写 artist_name，歌曲模式同时填写 track_title；Last.fm 只是听众关系与发现信号，具体歌曲仍需 MusicBrainz 核验。toolAvailability.lastfm=false 时禁止选择；
- search_knowledge：本地主题卡可以补充歌词主题或文化语境，但它不是歌曲发现主来源；
- recommend_music：已有足够公开资料时，提取具体歌曲/艺人推荐，并通过 MusicBrainz 核验可核验的歌曲；这是普通探索 Tool，与世界杯无关；
- analyze_music：当前问题需要解释、拆解、比较或论证，且已有资料足以在明确边界内形成深入回答。深度音乐分析是按需加载的 Skill，不是固定工作流；你应根据 analysisWorkspace 和 analysisEvidenceCoverage 自主决定搜索、精读、补充本地资料或停止。创作背景、采访、制作署名等事实型问题若仍有 blockingGaps，应优先补足最可能改变结论的缺口；
- clarify：仅当同名实体、互相冲突的硬约束或会显著改变结果的缺失信息必须由用户决定时使用。可安全推断并允许之后纠正的细节不得阻塞；选择此动作时填写一个最小 clarificationQuestion、简短 clarificationReason 和实际需要的 clarificationOptions（可以为空，禁止硬凑）；
- propose_tournament：仅当用户明确说想玩歌曲世界杯、淘汰赛或两两对决，但尚未要求立即构建候选时，展示世界杯启动卡；
- build_candidate_pool：用户明确要求开始/直接开赛/生成 16 或 32 首世界杯候选时，调用候选池复合 Tool；
- generate_exploration_report：用户明确要求根据当前对话生成偏好/探索报告时，调用报告复合 Tool；
- respond：普通音乐问答，或已有资料足以回答。
不得声称已经搜索、创建候选池或赛事，除非观察中确有对应结果。候选池复合 Tool 内部会自主决定在线搜索、MusicBrainz 核验和补充探索；你不得编排它的内部步骤。艺人身份歧义由候选池 Tool 在真正生成时核验。
会话摘要：{request.summary}
已确认长期偏好：{request.confirmed_memories}
近期推荐反馈：{request.recent_feedback}
最近消息：{request.recent_messages}
最近可展示卡片摘要：{json.dumps(_recent_card_context(request), ensure_ascii=False)}
本轮用户消息：{request.user_message}
本轮运行中追加的方向调整（越靠后优先级越高）：{request.active_interventions}
当前可观察状态：{json.dumps(observation_summary, ensure_ascii=False)}
可用 Tool 摘要：{json.dumps(self.tools.summaries(), ensure_ascii=False)}
可按需加载的 Skill：{json.dumps(self.skills.summaries(), ensure_ascii=False)}
返回 ConversationDecision。publicSummary 是可展示给用户的安全动作摘要。"""
        try:
            decision = await self.model.with_structured_output(ConversationDecision, method="function_calling").ainvoke(prompt)
        except Exception:
            return _fallback_decision(state)
        analysis_mode = bool(state.get("analysis_workspace")) or deep_analysis_intent(request)
        analysis_search_limit = 5
        if decision.action == "search_lastfm" and not self.lastfm.enabled:
            return ConversationDecision(
                action="search_web", public_summary="当前听众关系源未启用，改用公开音乐资料继续发现",
                query=(decision.query or request.user_message)[:300],
            )
        if decision.action == "research_musicbrainz" and self.catalog is None:
            return ConversationDecision(
                action="search_wikimedia", public_summary="目录关系服务暂不可用，先补充公开实体语境",
                query=(decision.query or request.user_message)[:300],
            )
        recent_no_gain = next((
            item for item in reversed(state.get("observations", []))
            if item.get("action") == decision.action
        ), None)
        if (
            decision.action in {"research_musicbrainz", "search_wikimedia", "search_lastfm"}
            and recent_no_gain and int(recent_no_gain.get("newOutputCount", 0)) == 0
        ):
            if analysis_mode and state.get("evidence_coverage", {}).get("blocking_gaps") and state.get("search_attempts", 0) < analysis_search_limit:
                return ConversationDecision(
                    action="search_web", public_summary="当前研究源没有增加新证据，改换公开资料检索切面",
                    query=_next_analysis_query(state, request),
                )
            if analysis_mode:
                return ConversationDecision(action="analyze_music", public_summary="当前研究源已无新增证据，在现有边界内完成分析")
            return ConversationDecision(action="respond", public_summary="当前研究源已无新增证据，整理已有结果")
        coverage = state.get("evidence_coverage", {})
        read_refs = set(state.get("read_source_refs", []))
        source_refs = {f"S{index}" for index, _ in enumerate(state.get("web_sources", [])[:16], start=1)}
        unread_refs = sorted(source_refs - read_refs, key=lambda value: int(value[1:]))
        if analysis_mode and decision.action == "read_source" and not state.get("web_sources"):
            return ConversationDecision(
                action="search_web", public_summary="先寻找可供精读的公开资料",
                query=_next_analysis_query(state, request),
            )
        if analysis_mode and decision.action == "read_source" and not unread_refs:
            if coverage.get("blocking_gaps") and state.get("search_attempts", 0) < analysis_search_limit:
                return ConversationDecision(
                    action="search_web", public_summary="现有页面仍未覆盖关键证据，调整检索切面",
                    query=_next_analysis_query(state, request),
                )
            return ConversationDecision(action="analyze_music", public_summary="在当前资料边界内形成深入分析")
        # Tournament UI is an explicit-intent capability, never an automatic
        # conversion of an ordinary preference statement. The model remains
        # free to choose research and answer tools, while this product boundary
        # prevents the optional Skill from taking over the conversation.
        if decision.action in {"propose_tournament", "build_candidate_pool"} and not _explicit_tournament_request(request.user_message):
            if analysis_mode:
                if not state.get("web_sources") and state.get("search_attempts", 0) < analysis_search_limit:
                    return ConversationDecision(action="search_web", public_summary="先查找能够支持核心分析的公开资料", query=_next_analysis_query(state, request))
                return ConversationDecision(action="analyze_music", public_summary="根据现有证据形成深入音乐分析")
            if _recommendation_intent(request):
                assessment = state.get("research_assessment", {})
                if (not state.get("web_sources") or (assessment and not assessment.get("ready", True))) and state.get("search_attempts", 0) < 3:
                    return ConversationDecision(
                        action="search_web",
                        public_summary=str(assessment.get("nextStep") or "先检索可核对的音乐推荐资料"),
                        query=_research_gap_query(request, assessment) if assessment else request.user_message[:300],
                    )
                return ConversationDecision(action="recommend_music", public_summary="提取并核验具体歌曲与艺人推荐")
            return ConversationDecision(action="respond", public_summary="结合当前对话继续音乐探索")
        if decision.action == "analyze_music" and not state.get("web_sources") and state.get("search_attempts", 0) < analysis_search_limit:
            return ConversationDecision(action="search_web", public_summary="先查找能够支持核心分析的公开资料", query=_next_analysis_query(state, request))
        if (
            decision.action == "analyze_music" and analysis_mode and coverage.get("blocking_gaps")
            and unread_refs and len(read_refs) < 5
        ):
            return ConversationDecision(
                action="read_source", source_ref=unread_refs[0],
                public_summary="精读最可能支持关键创作事实的来源",
            )
        if (
            decision.action == "analyze_music" and analysis_mode and coverage.get("blocking_gaps")
            and not unread_refs and state.get("search_attempts", 0) < analysis_search_limit
        ):
            return ConversationDecision(
                action="search_web", public_summary="针对仍未覆盖的分析维度补充资料",
                query=_next_analysis_query(state, request),
            )
        if analysis_mode and decision.action == "respond":
            if not state.get("web_sources") and state.get("search_attempts", 0) < analysis_search_limit:
                return ConversationDecision(action="search_web", public_summary="先查找能够支持核心分析的公开资料", query=_next_analysis_query(state, request))
            return ConversationDecision(action="analyze_music", public_summary="根据现有证据形成深入音乐分析")
        if _recommendation_intent(request) and not state.get("web_sources") and decision.action == "respond":
            return ConversationDecision(action="search_web", public_summary="检索符合本轮调整要求的新线索", query=request.user_message[:300])
        if decision.action == "recommend_music" and not state.get("web_sources"):
            return ConversationDecision(action="search_web", public_summary="先检索可核对的音乐推荐资料", query=request.user_message[:300])
        if _explicit_pool_request(request.user_message) and decision.action == "propose_tournament":
            return ConversationDecision(action="build_candidate_pool", public_summary="开始构建并核验歌曲世界杯候选池")
        previous = [item["action"] for item in state.get("action_history", [])]
        # An explicit recommendation request is a user contract, not a reason
        # for the optional World Cup Skill. Once public evidence is available,
        # prevent a model routing wobble from collapsing the requested cards
        # into a generic prose response.
        if "search_web" in previous and _recommendation_intent(request) and decision.action == "respond":
            assessment = state.get("research_assessment", {})
            if assessment and not assessment.get("ready", True) and state.get("search_attempts", 0) < 3:
                return ConversationDecision(
                    action="search_web", public_summary=str(assessment.get("nextStep") or "继续补足推荐证据"),
                    query=_research_gap_query(request, assessment),
                )
            return ConversationDecision(action="recommend_music", public_summary="提取并核验具体歌曲与艺人推荐")
        if decision.action == "recommend_music" and _recommendation_intent(request):
            assessment = state.get("research_assessment", {})
            if assessment and not assessment.get("ready", True) and state.get("search_attempts", 0) < 3:
                return ConversationDecision(
                    action="search_web", public_summary=str(assessment.get("nextStep") or "继续补足尚未覆盖的音乐资料"),
                    query=_research_gap_query(request, assessment),
                )
        if decision.action == "search_web" and "search_web" in previous and state.get("search_attempts", 0) >= (analysis_search_limit if analysis_mode else 3):
            if analysis_mode:
                return ConversationDecision(action="analyze_music", public_summary="研究预算已满足，在证据边界内完成分析")
            if _recommendation_intent(request):
                return ConversationDecision(action="recommend_music", public_summary="研究预算已满足，提取并核验具体推荐")
            return ConversationDecision(action="respond", public_summary="公开资料已经足够，开始整理回答")
        if decision.action == "search_knowledge" and "search_knowledge" in previous:
            if analysis_mode:
                return ConversationDecision(action="analyze_music", public_summary="主题资料已补充，形成深入音乐分析")
            return ConversationDecision(action="respond", public_summary="补充资料已经足够，开始整理回答")
        return decision

    async def answer(self, state: ConversationState, action: str) -> str:
        request = state["request"]
        if action == "clarify":
            return state["decision"].clarification_question or "我还需要一个更明确的起点：你可以告诉我一两位喜欢的艺人、最近反复听的歌，或者想要的情绪、场景与语言范围。"
        if action == "propose_tournament":
            return "这个方向已经足够形成一场歌曲世界杯。我会在你进入候选确认后，通过在线搜索发现歌曲，并用 MusicBrainz 核验身份；候选池由你确认后才会正式开赛。"
        if action == "build_candidate_pool":
            return "我会直接开始构建候选池：先在线发现相关作品，再以 MusicBrainz 核验歌曲身份；候选池完成后仍由你确认才会开赛。"
        if self.model is None:
            return _fallback_answer(state)
        sources = [{"title": item.get("sourceTitle"), "summary": item.get("summary"), "url": item.get("sourceUrl")} for item in state.get("web_sources", [])[:10]]
        knowledge = state.get("knowledge", [])[:6]
        prompt = f"""你是 IndieSoundQuest 的音乐探索 Agent。用中文自然、克制地回答，不输出思维链。
不要虚构歌曲、艺人、歌词、链接或工具结果。外部资料为空时，不得声称已经查询。
用户问题：{request.user_message}
会话摘要：{request.summary}
最近对话：{request.recent_messages}
近期推荐反馈：{request.recent_feedback}
公开资料：{json.dumps(sources, ensure_ascii=False)}
本地补充主题卡：{json.dumps(knowledge, ensure_ascii=False)}
回答篇幅由用户问题的复杂度、已有证据和本轮研究投入共同决定，不设置统一字数上限。简单事实可以简洁；已经进行多轮检索、核验或精读时，必须把真正影响结论的证据、推理依据和边界充分写入正文，不能把长时间研究压缩成几句泛泛总结。普通对话只回答用户当前问题并给出自然的音乐探索方向；除非用户本轮明确要求世界杯、淘汰赛或两两对决，否则不得主动提及比赛、开赛或候选池。"""
        try:
            response = await self.model.ainvoke(prompt)
            return str(response.content).strip()
        except Exception:
            return _fallback_answer(state)

    async def analyze_music(self, state: ConversationState) -> ConversationAgentResult:
        """Produce an evidence-bounded analysis from the mutable workspace.

        Research order is deliberately absent here.  This method is only the
        final expression step selected by the unified ReAct Agent after any
        tool calls it considered useful.
        """
        request = state["request"]
        workspace = MusicAnalysisWorkspace.model_validate(
            state.get("analysis_workspace") or initial_analysis_workspace(request).model_dump()
        )
        all_sources = list(state.get("web_sources", []))
        # Keep a bounded part of the prior turn's evidence visible to the
        # current analysis even when several new searches prepend many hits.
        # This preserves conversational continuity without preventing the
        # Agent from replacing weak evidence with stronger current sources.
        inherited_sources = [item for item in all_sources if item.get("inheritedFromAnalysisCard")]
        current_sources = [item for item in all_sources if not item.get("inheritedFromAnalysisCard")]
        raw_sources = [*inherited_sources[:4], *current_sources][:16]
        sources = [{
            "ref": f"S{index}",
            "title": str(item.get("sourceTitle") or "公开音乐资料")[:180],
            "url": str(item.get("sourceUrl") or ""),
            "summary": str(item.get("summary") or "")[:1000],
            "excerpt": str(item.get("pageExcerpt") or "")[:2000],
            "provider": str(item.get("searchProvider") or "web")[:40],
            "quality": classify_source_quality(item),
        } for index, item in enumerate(raw_sources, start=1) if item.get("sourceUrl")]
        allowed_refs = {item["ref"] for item in sources}
        source_quality = {item["ref"]: item["quality"] for item in sources}
        knowledge = list(state.get("knowledge", []))[:6]
        evidence_coverage = state.get("evidence_coverage") or assess_analysis_evidence(workspace, raw_sources).model_dump()
        review_meta: dict = {"semanticReviewApplied": False, "reviewedClaimCount": 0, "verdictCounts": {}}
        source_conflicts: list[str] = []
        fallback_claim = EvidenceClaim(
            statement="当前只能依据已取得的公开资料整理分析线索，尚不能把主观听感当作创作者的明确意图。",
            claim_type="INTERPRETATION", dimension="证据边界", confidence="low",
            evidence_refs=sorted(allowed_refs)[:2], status="partial",
            boundary="模型或资料不可用时保留已有来源，不补写无法核验的音乐细节。",
        )
        if self.model is None or not sources:
            titles = "、".join(item["title"] for item in sources[:4])
            text = (
                f"关于“{workspace.question}”，当前能确认的公开线索主要来自{titles}。"
                "这些资料还不足以支持对歌词、配器或制作细节作精确判断，因此我不会把一般性的听感描述写成确定事实。"
                "如果你继续指定版本、提供希望分析的短歌词片段，或允许补充更多制作资料，我可以沿当前问题继续深挖。"
                if titles else
                "这确实是一个需要证据支撑的音乐分析问题，但当前没有取得可核验资料。为了避免虚构歌词、编曲或创作意图，我暂时只保留问题本身；你可以继续指定歌曲版本或稍后重试资料检索。"
            )
            claims = [fallback_claim]
            core_judgment = "当前证据不足，不能可靠地把一般听感扩写成作品事实。"
            uncertainties = ["尚未取得足以支持具体音乐细节的公开资料"]
            related_works: list[str] = []
        else:
            prompt = f"""你是统一音乐探索 Agent，当前加载了 deep_music_analysis Skill。不要输出思维链。
你不是在执行固定模板；请只选择真正能回答用户问题的分析角度。基于给定资料形成具体、有论证的中文分析，不虚构歌词、采访、Credits、音频参数或创作者意图。

用户问题：{request.user_message}
会话摘要：{request.summary}
近期对话：{request.recent_messages}
动态分析工作台：{workspace.model_dump_json()}
证据覆盖评估：{json.dumps(evidence_coverage, ensure_ascii=False)}
公开资料（引用只能使用 S1、S2 等 ref）：{json.dumps(sources, ensure_ascii=False)}
本地主题卡（仅作补充，不得凌驾于在线资料）：{json.dumps(knowledge, ensure_ascii=False)}

要求：
- answer 直接回答问题，篇幅由问题复杂度、所选分析维度、证据质量与用户要求动态决定，不设置统一字数上限；
- 已经进行多轮检索、目录核验或来源精读时，必须把真正影响结论的研究成果转化为充分正文；每个与核心问题直接相关且有证据的 selectedDimension 都应得到实质展开，不得只用几个形容词概括；
- 至少形成一条“可观察现象 → 听觉或文本效果 → 情绪/叙事作用 → 与问题的关系”的完整论证链；
- 明确区分 FACT、OBSERVATION、INTERPRETATION、LISTENER_INFERENCE；
- FACT 必须引用给定 ref；资料只支持背景而不支持声音细节时，要明确这是解释或听者推断；
- 无真实音频证据时不得给出精确 BPM、和弦、调性、秒级乐器进入点或混音参数；
- 不大段复现歌词；可以概括公开资料所支持的主题；
- 资料冲突或缺失时写入 uncertainties，不得用常识补成事实；
- relatedWorks 只给 0–3 个真正有比较价值的“艺人 — 作品”。
返回 MusicAnalysisDraft。"""
            try:
                draft = await self._invoke_validated(MusicAnalysisDraft, prompt)
                claims = sanitize_claims(draft.claims, allowed_refs)
                text = draft.answer.strip()
                core_judgment = draft.core_judgment
                uncertainties = draft.uncertainties
                related_works = draft.related_works
                if any(item.claim_type == "FACT" for item in claims) or evidence_coverage.get("requires_full_text"):
                    review_prompt = f"""你仍是同一个统一音乐探索 Agent，现在对自己的分析做一次条件式证据审校，不输出思维链。
这不是新的 Agent，也不是固定工作流：只有本轮包含事实 Claim 或创作/制作事实时才执行。

用户问题：{request.user_message}
待审校正文：{text}
待审校核心判断：{core_judgment}
Claim（claim_index 是数组下标）：{json.dumps([item.model_dump() for item in claims], ensure_ascii=False)}
公开资料：{json.dumps(sources, ensure_ascii=False)}

逐条判断引用正文或摘要是否真正支持 Claim，而不只是“存在一个链接”：
- supported_refs 只能保留确实支持该表述的 S1、S2 等 ref；
- 搜索摘要、社区讨论和单一弱来源不能支撑高置信度的幕后事实；
- 来源互相矛盾时 verdict=conflicting 并记录 conflicts；
- 只有部分支持时 verdict=partial，并用 revised_statement 缩小表述；
- 完全不支持时 verdict=unsupported，revised_answer 必须删除该事实或明确改为无法核验；
- 不得引入原 Claim 和资料之外的新事实；
- revised_answer 保留原回答的自然结构、分析维度和信息密度，只修复证据越界；不得借审校之名把长篇分析压成摘要。如果确需删除不受支持的事实，应以证据支持的解释或明确边界替代，而不是删除其余有依据的分析。
返回 MusicAnalysisReview。"""
                    try:
                        review = await self._invoke_validated(MusicAnalysisReview, review_prompt)
                        revised_text = review.revised_answer.strip()
                        # A semantic review may narrow unsupported facts, but
                        # it must not silently turn a researched analysis into
                        # a short summary. If the provider over-compresses the
                        # revision, retry once with an explicit depth-preserving
                        # contract before accepting it.
                        if _review_overcompressed(text, revised_text):
                            retry_prompt = review_prompt + f"""

上一版审校正文只有初稿信息量的很小一部分，属于过度压缩。请重新审校：
- 保留所有有证据或已明确标为解释/听者推断的实质段落；
- 只删除或收窄确实无法支持的事实；
- revised_answer 不得退化为摘要，且应维持与初稿相当的分析深度；
- 不设置统一字数上限。
初稿字符数：{len(text)}；上一版审校字符数：{len(revised_text)}。
"""
                            review = await self._invoke_validated(MusicAnalysisReview, retry_prompt)
                            revised_text = review.revised_answer.strip()
                        claims, review_meta = apply_claim_reviews(
                            claims, review.items, allowed_refs, source_quality,
                        )
                        review_meta["summary"] = review.review_summary
                        review_meta["overcompressionDetected"] = _review_overcompressed(text, revised_text)
                        # Evidence safety wins if the provider ignores the
                        # depth-preservation retry, while the trace makes the
                        # degraded result observable instead of hiding it.
                        text = revised_text
                        core_judgment = review.revised_core_judgment or core_judgment
                        source_conflicts = review.conflicts
                        uncertainties = list(dict.fromkeys([*uncertainties, *review.conflicts]))[:8]
                    except Exception as review_error:
                        review_meta["failureType"] = type(review_error).__name__
                        review_meta["failureBoundary"] = "条件式语义审校未完成，已保留结构校验与事实引用降级。"
                        uncertainties = list(dict.fromkeys([
                            *uncertainties, "本轮语义审校未完成；事实 Claim 仅通过了引用存在性检查。",
                        ]))[:8]
            except Exception as error:
                review_meta["generationFailureType"] = type(error).__name__
                titles = "、".join(item["title"] for item in sources[:4])
                text = (
                    f"我已经找到与“{workspace.question}”相关的公开资料，包括{titles}，"
                    "但本轮没有形成通过结构校验的深度分析。为了避免把未经核验的歌词、编曲或创作背景写成事实，"
                    "我先保留这些来源；你可以继续指定最想展开的角度，我会沿同一对话重新分析。"
                )
                claims = [fallback_claim]
                core_judgment = "已获得公开资料，但分析草稿未通过证据结构校验。"
                uncertainties = ["本轮结构化分析生成失败，未输出未经核验的细节"]
                related_works = []

        source_items = [{
            "ref": item["ref"], "title": item["title"], "url": item["url"],
            "summary": (item["summary"] or item["excerpt"])[:240], "provider": item["provider"],
            "quality": item["quality"],
        } for item in sources[:8]]
        payload = {
            "title": f"关于{workspace.subject[:80]}的深入分析",
            "subject": workspace.subject,
            "question": workspace.question,
            "coreJudgment": core_judgment,
            "selectedDimensions": workspace.selected_dimensions,
            "claims": [{
                "statement": item.statement, "claimType": item.claim_type,
                "dimension": item.dimension, "evidenceRefs": item.evidence_refs,
                "confidence": item.confidence, "boundary": item.boundary,
                "status": item.status,
            } for item in claims],
            "uncertainties": uncertainties,
            "relatedWorks": related_works,
            "sources": source_items,
            "workspaceRevision": workspace.revision,
            "evidenceCoverage": evidence_coverage,
            "claimReview": review_meta,
            "sourceConflicts": source_conflicts,
        }
        previous_analysis = latest_analysis_context(request)
        if previous_analysis:
            payload.update({
                "contextMode": "REFINED",
                "basedOnCardId": previous_analysis.get("messageId"),
                "appliedInstruction": request.user_message[:240],
            })
        return ConversationAgentResult(
            text=text,
            card_intent=ConversationCardIntent(
                message_type="RECOMMENDATION_CARD", card_type="MUSIC_ANALYSIS", payload=payload,
            ),
            action="analyze_music",
            trace_summary={
                "actions": [item["action"] for item in state.get("action_history", [])],
                "skill": "deep_music_analysis", "workspaceRevision": workspace.revision,
                "selectedDimensions": workspace.selected_dimensions,
                "webSourceCount": len(sources), "knowledgeCount": len(knowledge),
                "fullTextSourceCount": int(evidence_coverage.get("full_text_source_count", 0)),
                "independentDomainCount": int(evidence_coverage.get("independent_domain_count", 0)),
                "claimCount": len(claims),
                "semanticReviewApplied": bool(review_meta.get("semanticReviewApplied")),
                "sourceConflictCount": len(source_conflicts),
                "inheritedAnalysis": bool(previous_analysis),
                "inheritedSourceCount": sum(
                    bool(item.get("inheritedFromAnalysisCard")) for item in raw_sources
                ),
                "researchQueryCount": len(state.get("analysis_queries", [])),
            },
        )

    async def exploration_report(self, state: ConversationState) -> dict:
        request = state["request"]
        fallback = {
            "summary": "这份探索报告基于当前对话整理；继续补充喜欢或跳过的作品，结论会更具体。",
            "dimensions": [{"label": "探索起点", "summary": request.user_message[:120]}],
            "directions": ["从当前提到的艺人或场景继续展开", "挑选一两首最有代表性的作品继续比较声音、文本与创作脉络"],
            "personalityEasterEgg": "你的音乐线索更像一张正在展开的地图，而不是一道需要立刻答完的选择题。",
            "disclaimer": "内容用于音乐探索与娱乐性观察，不构成心理或人格判断。",
            "claims": _exploration_claims({"summary": "这份探索报告基于当前对话整理；继续补充喜欢或跳过的作品，结论会更具体。", "dimensions": [{"label": "探索起点", "summary": request.user_message[:120]}]}, request, []),
        }
        if self.model is None:
            return fallback
        sources = [{"title": item.get("sourceTitle"), "summary": item.get("summary")} for item in state.get("web_sources", [])[:5]]
        prompt = f"""你是 IndieSoundQuest 的探索报告复合工具。只输出 JSON 对象，不输出思维链。
基于对话生成克制、具体的中文音乐探索报告，不能虚构歌曲、艺人、链接或资料。
用户本轮消息：{request.user_message}
会话摘要：{request.summary}
最近对话：{request.recent_messages}
近期推荐反馈：{request.recent_feedback}
可用公开资料：{json.dumps(sources, ensure_ascii=False)}
返回字段：summary（80-220字），dimensions（2-4项，每项有 label/summary），directions（2-4条可执行探索方向），personalityEasterEgg（80-120字娱乐性观察），disclaimer。
"""
        try:
            response = await self.model.ainvoke(prompt)
            data = json.loads(str(response.content).strip().removeprefix("```json").removesuffix("```").strip())
            if not isinstance(data.get("summary"), str) or not isinstance(data.get("dimensions"), list):
                return fallback
            return {**fallback, **data, "claims": _exploration_claims(data, request, sources)}
        except Exception:
            return fallback

    async def run_candidate_pool(self, guest_id: str, preference_text: str, pool_size: int, confirmed_artists: list[ConfirmedArtist] | None = None) -> ConversationAgentResult:
        if self.candidate_graph is None:
            raise RuntimeError("CANDIDATE_POOL_GRAPH_MISSING")
        size = 16 if pool_size <= 16 else 32
        pool_request = CandidatePoolRequest(
            request_id=uuid4(), guest_id=guest_id, size=size, candidate_count=size * 2,
            preference_text=preference_text, confirmed_artists=confirmed_artists or [],
        )
        pool_state = await self.candidate_graph.ainvoke({"request": pool_request}, {"recursion_limit": 128})
        pool_result: CandidatePoolResult | None = pool_state.get("result")
        if pool_result is None:
            raise RuntimeError("CANDIDATE_POOL_RESULT_MISSING")
        card, text = _candidate_pool_card(pool_result, pool_state, preference_text, size)
        return ConversationAgentResult(
            text=text, card_intent=card, action="build_candidate_pool",
            trace_summary={"candidatePoolStatus": pool_result.status, "candidateCount": len(pool_result.recording_ids), "candidateTrace": pool_result.trace_summary},
        )

    def _build(self):
        async def initialize(state: ConversationState):
            request = state["request"]
            profile = await self.analyze_preference(request)
            resolutions, ambiguities = await self.resolve_entities(profile.named_artists, request.confirmed_artists)
            history = [
                {"action": "understand_preference", "summary": "结合本轮消息与会话上下文理解需求"},
                {"action": "analyze_preference", "summary": _preference_plan_summary(profile)},
            ]
            if profile.named_artists:
                history.append({
                    "action": "resolve_named_entities",
                    "summary": f"已核验 {len([item for item in resolutions if item.get('status') == 'RESOLVED'])} 个艺人身份；{len(ambiguities)} 项需要确认",
                })
            analysis_workspace = None
            initial_sources: list[dict] = []
            initial_coverage: dict = {}
            if deep_analysis_intent(request):
                analysis_workspace = await self.plan_analysis_workspace(request)
                initial_sources = inherited_analysis_sources(request)
                coverage = assess_analysis_evidence(analysis_workspace, initial_sources)
                analysis_workspace = workspace_with_coverage(analysis_workspace, coverage)
                initial_coverage = coverage.model_dump(by_alias=True)
                history.append({
                    "action": "load_analysis_skill",
                    "summary": (
                        f"已继承上一轮分析与 {len(initial_sources)} 条公开来源，并修订研究工作台"
                        if initial_sources else
                        "已加载深度音乐分析方法，并建立可随证据修订的研究工作台"
                    ),
                })
            return {
                "action_history": history,
                "observations": [], "web_sources": initial_sources, "knowledge": [], "iteration": 0,
                "preference_profile": profile.model_dump(by_alias=True),
                "entity_resolutions": resolutions, "entity_ambiguities": ambiguities,
                "research_assessment": {}, "search_attempts": 0, "analysis_queries": [],
                "analysis_workspace": analysis_workspace.model_dump() if analysis_workspace else {},
                "evidence_coverage": initial_coverage, "read_source_refs": [],
                "applied_intervention_sequence": 0, "intervention_revision": 0,
            }

        async def supervisor(state: ConversationState, config=None):
            provider = (config or {}).get("configurable", {}).get("intervention_provider")
            incoming: list[dict] = []
            if callable(provider):
                # A persisted user direction must not be silently skipped just
                # because the mailbox is temporarily unavailable. Propagating
                # the failure lets the durable worker retry the same run.
                incoming = list(await provider(state.get("applied_intervention_sequence", 0)) or [])
            effective_state = state
            intervention_history: list[dict] = []
            applied_sequence = state.get("applied_intervention_sequence", 0)
            revision = state.get("intervention_revision", 0)
            iteration = state.get("iteration", 0)
            request = state["request"]
            workspace = state.get("analysis_workspace", {})
            if incoming:
                incoming = sorted(incoming, key=lambda item: int(item.get("sequenceNumber", 0)))
                applied_sequence = max(int(item.get("sequenceNumber", 0)) for item in incoming)
                contents = [str(item.get("content") or "").strip() for item in incoming if str(item.get("content") or "").strip()]
                request = _request_with_interventions(request, contents)
                revision += 1
                iteration = 0
                intervention_history = [{
                    "action": "adjust_direction",
                    "summary": f"已接收第 {applied_sequence} 次运行中补充，并重新评估后续计划",
                }]
                if workspace:
                    values = dict(workspace)
                    values["question"] = _bounded_text(
                        f"{values.get('question', '')}\n运行中最新调整：{'；'.join(contents)}", 1000
                    )
                    values["open_questions"] = ["根据用户最新调整重新判断仍需补足的证据"]
                    values["revision"] = int(values.get("revision", 1)) + 1
                    workspace = MusicAnalysisWorkspace.model_validate(values).model_dump()
                effective_state = {
                    **state, "request": request, "analysis_workspace": workspace,
                    "applied_intervention_sequence": applied_sequence,
                    "intervention_revision": revision, "iteration": iteration,
                }
            decision = await self.decide(effective_state)
            max_iterations = 11 if workspace else 7
            if iteration >= max_iterations:
                decision = ConversationDecision(
                    action="analyze_music" if workspace else "respond",
                    public_summary="运行预算已满足，在当前证据边界内整理最终回应",
                )
            return {
                "request": request,
                "analysis_workspace": workspace,
                "applied_intervention_sequence": applied_sequence,
                "intervention_revision": revision,
                "decision": decision,
                "iteration": iteration + 1,
                "action_history": state.get("action_history", []) + intervention_history + [{"action": decision.action, "summary": decision.public_summary}],
            }

        async def execute(state: ConversationState):
            action = state["decision"].action
            request = state["request"]
            if action == "search_web":
                query = state["decision"].query or request.user_message
                if _recommendation_followup(request):
                    query = _followup_search_query(request, query)
                assessment = state.get("research_assessment", {})
                missing = list(assessment.get("missingArtists") or [])
                confirmed_focus = [
                    f"{item.get('name')} {item.get('mention')}" for item in state.get("entity_resolutions", [])
                    if item.get("confirmedByUser") and item.get("name") and item.get("mention")
                ]
                if state.get("analysis_workspace"):
                    query = _deduplicate_analysis_query(state, request, query)
                    queries = [{"query": query[:300], "purpose": "deep_music_analysis"}]
                else:
                    queries = _recommendation_search_queries(
                        request, query, missing or confirmed_focus or None, state.get("preference_profile", {})
                    )
                if len(queries) > 1 and hasattr(self.web, "search_many"):
                    new_sources = await self.web.search_many(queries)
                else:
                    new_sources = await self.web.search(query, "conversation_research")
                sources = _merge_web_sources(state.get("web_sources", []), new_sources, prefer_incoming=state.get("search_attempts", 0) > 0)
                research = _assess_research(request, state.get("preference_profile", {}), sources)
                updates = {
                    "web_sources": sources,
                    "research_assessment": research,
                    "search_attempts": state.get("search_attempts", 0) + 1,
                    "observations": state["observations"] + [{
                        "action": action, "status": "success", "outputCount": len(sources),
                        "newOutputCount": len(new_sources), "queryCount": len(queries),
                        "namedArtistCount": len(_explicit_artist_mentions(request.user_message)),
                        "missingArtists": research.get("missingArtists", []), "ready": research.get("ready", False),
                    }],
                }
                if state.get("analysis_workspace"):
                    updates["analysis_queries"] = [
                        *state.get("analysis_queries", []), query[:300],
                    ][-8:]
                    workspace = workspace_after_research(
                        state["analysis_workspace"], len(sources),
                        f"本轮公开资料检索新增 {len(new_sources)} 条来源；工作台将由 Agent 据此调整。",
                    )
                    coverage = assess_analysis_evidence(workspace, sources)
                    workspace = workspace_with_coverage(workspace, coverage)
                    updates["analysis_workspace"] = workspace.model_dump()
                    updates["evidence_coverage"] = coverage.model_dump(by_alias=True)
                return updates
            if action == "read_source":
                sources = list(state.get("web_sources", []))
                requested_ref = (state["decision"].source_ref or "").upper()
                read_refs = list(state.get("read_source_refs", []))
                available = [f"S{index}" for index in range(1, min(16, len(sources)) + 1)]
                unread = [ref for ref in available if ref not in read_refs]
                source_ref = requested_ref if requested_ref in unread else (unread[0] if unread else "")
                enriched: list[dict] = []
                if source_ref and hasattr(self.web, "enrich_public_sources"):
                    enriched = await self.web.enrich_public_sources([sources[int(source_ref[1:]) - 1]], limit=1)
                if source_ref:
                    read_refs.append(source_ref)
                if enriched:
                    enriched_by_url = {str(item.get("sourceUrl") or ""): item for item in enriched}
                    sources = [enriched_by_url.get(str(item.get("sourceUrl") or ""), item) for item in sources]
                workspace = MusicAnalysisWorkspace.model_validate(state["analysis_workspace"])
                coverage = assess_analysis_evidence(workspace, sources)
                workspace = workspace_after_research(
                    workspace, len(sources),
                    f"已精读 {source_ref or '所选来源'}；取得 {len(enriched)} 条可用正文摘录。",
                )
                workspace = workspace_with_coverage(workspace, coverage)
                return {
                    "web_sources": sources, "read_source_refs": read_refs,
                    "evidence_coverage": coverage.model_dump(by_alias=True),
                    "analysis_workspace": workspace.model_dump(),
                    "observations": state["observations"] + [{
                        "action": action, "status": "success" if enriched else "unavailable",
                        "sourceRef": source_ref, "excerptCount": len(enriched),
                    }],
                }
            if action == "research_musicbrainz":
                decision = state["decision"]
                entity_mbid = decision.entity_mbid
                if not entity_mbid and decision.entity_type == "artist":
                    entity_mbid = next((
                        str(item.get("mbid")) for item in state.get("entity_resolutions", [])
                        if item.get("status") == "RESOLVED" and item.get("mbid")
                    ), None)
                profile_artists = list(
                    state.get("preference_profile", {}).get("named_artists", [])
                    or state.get("preference_profile", {}).get("namedArtists", [])
                )
                artist_name = decision.artist_name or next(iter(profile_artists), None)
                inferred_title = None
                if state.get("analysis_workspace") and decision.entity_type in {"recording", "work", "release-group"}:
                    inferred_title = str(state["analysis_workspace"].get("subject") or "").strip() or None
                try:
                    found = await self.catalog.research_musicbrainz(
                        decision.query or request.user_message, decision.entity_type, entity_mbid,
                        artist_name, decision.track_title or inferred_title,
                    ) if self.catalog is not None else []
                except Exception:
                    found = []
                previous_urls = {str(item.get("sourceUrl") or "") for item in state.get("web_sources", [])}
                sources = _merge_web_sources(state.get("web_sources", []), found)
                new_count = sum(
                    1 for item in found
                    if str(item.get("sourceUrl") or "") not in previous_urls
                )
                updates = {
                    "web_sources": sources,
                    "observations": state["observations"] + [{
                        "action": action, "status": "success" if found else "unavailable",
                        "outputCount": len(found), "newOutputCount": new_count, "entityType": decision.entity_type,
                        "ambiguousCount": sum(bool(item.get("ambiguous")) for item in found),
                    }],
                }
                if state.get("analysis_workspace"):
                    workspace = workspace_after_research(
                        state["analysis_workspace"], len(sources),
                        f"MusicBrainz 关系研究补充 {len(found)} 条版本、署名或实体事实。",
                    )
                    coverage = assess_analysis_evidence(workspace, sources)
                    updates["analysis_workspace"] = workspace_with_coverage(workspace, coverage).model_dump()
                    updates["evidence_coverage"] = coverage.model_dump(by_alias=True)
                return updates
            if action == "search_wikimedia":
                found = await self.wikimedia.search(state["decision"].query or request.user_message)
                previous_urls = {str(item.get("sourceUrl") or "") for item in state.get("web_sources", [])}
                sources = _merge_web_sources(state.get("web_sources", []), found)
                new_count = sum(1 for item in found if str(item.get("sourceUrl") or "") not in previous_urls)
                updates = {
                    "web_sources": sources,
                    "observations": state["observations"] + [{
                        "action": action, "status": "success" if found else "unavailable",
                        "outputCount": len(found), "newOutputCount": new_count,
                    }],
                }
                if state.get("analysis_workspace"):
                    workspace = workspace_after_research(
                        state["analysis_workspace"], len(sources),
                        f"Wikimedia 研究补充 {len(found)} 条身份、时间线或音乐史语境。",
                    )
                    coverage = assess_analysis_evidence(workspace, sources)
                    updates["analysis_workspace"] = workspace_with_coverage(workspace, coverage).model_dump()
                    updates["evidence_coverage"] = coverage.model_dump(by_alias=True)
                return updates
            if action == "search_lastfm":
                decision = state["decision"]
                artist_name = decision.artist_name or next(iter(
                    state.get("preference_profile", {}).get("named_artists", [])
                    or state.get("preference_profile", {}).get("namedArtists", [])
                ), "")
                try:
                    found = await self.lastfm.discover(
                        artist_name=artist_name, track_title=decision.track_title,
                        mode=decision.lastfm_mode, limit=12,
                    )
                except Exception:
                    found = []
                previous_urls = {str(item.get("sourceUrl") or "") for item in state.get("web_sources", [])}
                sources = _merge_web_sources(state.get("web_sources", []), found)
                new_count = sum(1 for item in found if str(item.get("sourceUrl") or "") not in previous_urls)
                return {
                    "web_sources": sources,
                    "observations": state["observations"] + [{
                        "action": action, "status": "success" if found else "unavailable",
                        "outputCount": len(found), "newOutputCount": new_count, "mode": decision.lastfm_mode,
                    }],
                }
            if action == "search_knowledge":
                cards = await self.knowledge.search_verified(state["decision"].query or request.user_message, [])
                updates = {
                    "knowledge": cards,
                    "observations": state["observations"] + [{"action": action, "status": "success", "outputCount": len(cards)}],
                }
                if state.get("analysis_workspace"):
                    workspace = workspace_after_research(
                        state["analysis_workspace"], len(state.get("web_sources", [])),
                        f"本地主题卡补充了 {len(cards)} 条解释线索；它们只作为在线资料的补充。",
                    )
                    coverage = assess_analysis_evidence(workspace, state.get("web_sources", []))
                    workspace = workspace_with_coverage(workspace, coverage)
                    updates["analysis_workspace"] = workspace.model_dump()
                    updates["evidence_coverage"] = coverage.model_dump(by_alias=True)
                return updates
            if action == "build_candidate_pool":
                if self.candidate_graph is None:
                    return await finalize(state, "propose_tournament")
                size = _resolve_pool_size(request)
                return await self._build_candidate_pool_result(state, request.user_message, size, [])
            if action == "generate_exploration_report":
                web_sources = list(state.get("web_sources", []))
                if not web_sources:
                    query = request.user_message
                    web_sources = await self.web.search(query, "exploration_report")
                    state = {**state, "web_sources": web_sources}
                report = await self.exploration_report(state)
                return {
                    "result": ConversationAgentResult(
                        text="我已把这段对话整理成一份探索报告。它会留在当前对话里；之后的新偏好或反馈也可以继续让结论变得更具体。",
                        card_intent=ConversationCardIntent(message_type="REPORT_CARD", card_type="EXPLORATION_REPORT", payload=report),
                        action=action,
                        trace_summary={"actions": [item["action"] for item in state["action_history"]], "webSourceCount": len(state.get("web_sources", [])), "knowledgeCount": len(state.get("knowledge", [])), "appliedInterventionSequence": state.get("applied_intervention_sequence", 0), "interventionRevision": state.get("intervention_revision", 0)},
                    ),
                    "observations": state["observations"] + [{"action": action, "status": "success"}],
                }
            if action == "recommend_music":
                updates = await self._recommend_music_result(state)
                if updates.get("result"):
                    _add_intervention_trace(updates["result"], state)
                return updates
            if action == "analyze_music":
                result = await self.analyze_music(state)
                _add_intervention_trace(result, state)
                return {
                    "result": result,
                    "observations": state["observations"] + [{
                        "action": action, "status": "success",
                        "sourceCount": result.trace_summary.get("webSourceCount", 0),
                        "claimCount": result.trace_summary.get("claimCount", 0),
                    }],
                }
            return await finalize(state, action)

        async def finalize(state: ConversationState, action: str):
            request = state["request"]
            text = await self.answer(state, action)
            card = None
            if action == "propose_tournament":
                card = ConversationCardIntent(
                    message_type="TOURNAMENT_CARD",
                    card_type="WORLD_CUP_LAUNCH",
                    payload={
                        "preferenceText": request.user_message,
                        "title": "把这轮偏好放进一场比赛",
                        "defaultSize": 32,
                    },
                )
            elif action == "clarify":
                decision = state["decision"]
                question = decision.clarification_question or text
                ambiguities = state.get("entity_ambiguities", [])
                card = ConversationCardIntent(
                    message_type="CLARIFICATION_CARD",
                    card_type="GENERAL_CLARIFICATION",
                    payload={
                        "question": question,
                        "reason": decision.clarification_reason or "这个信息会实质影响下一步的结果。",
                        "options": decision.clarification_options,
                        "entityCandidates": ambiguities,
                        "clarifications": ambiguities,
                        "allowFreeText": True,
                    },
                )
            elif action == "respond" and state.get("web_sources"):
                card = _public_source_card(state["web_sources"])
            return {
                "result": ConversationAgentResult(
                    text=text,
                    card_intent=card,
                    action=action,
                    trace_summary={
                        "actions": [item["action"] for item in state["action_history"]],
                        "webSourceCount": len(state.get("web_sources", [])),
                        "knowledgeCount": len(state.get("knowledge", [])),
                        "preferenceProfile": state.get("preference_profile", {}),
                        "researchAssessment": state.get("research_assessment", {}),
                        "appliedInterventionSequence": state.get("applied_intervention_sequence", 0),
                        "interventionRevision": state.get("intervention_revision", 0),
                    },
                ),
                "observations": state["observations"] + [{"action": action, "status": "success"}],
            }

        def route(state: ConversationState) -> Literal["supervisor", "end"]:
            return "end" if state.get("result") else "supervisor"

        graph = StateGraph(ConversationState)
        graph.add_node("initialize", initialize)
        graph.add_node("supervisor", supervisor)
        graph.add_node("execute_action", execute)
        graph.set_entry_point("initialize")
        graph.add_edge("initialize", "supervisor")
        graph.add_edge("supervisor", "execute_action")
        graph.add_conditional_edges("execute_action", route, {"supervisor": "supervisor", "end": END})
        return graph

    async def compress_memory(self, previous_summary: str, compression_messages: list[dict]) -> str:
        """Incrementally compress an old transcript segment, never the recent raw window."""
        if self.model is None:
            parts = [previous_summary.strip()]
            parts.extend(
                f"{str(item.get('role') or '')}：{str(item.get('content') or '').strip()}"
                for item in compression_messages
                if isinstance(item, dict) and str(item.get("content") or "").strip()
            )
            return "\n".join(dict.fromkeys(item for item in parts if item))[-12000:]
        prompt = f"""你是对话记忆压缩器。只输出可供下一轮使用的中文摘要，不输出思维链或 JSON。
将旧摘要与这一段即将离开近期窗口的公开对话合并为中期记忆。这不是用户画像，不要总结尚未传入的新消息。
保留：持续目标、明确偏好、排除项、已确认实体、未解决问题、有用结论与必要的上下文。新增段与旧摘要冲突时以新增段为准。
不得加入密钥、Cookie、内部地址、未展示工具响应、思维链或自行推断的人格结论。按信息密度组织，最多 2400 字。
旧摘要：{previous_summary}
本次增量压缩段：{compression_messages}"""
        try:
            response = await self.model.ainvoke(prompt)
            value = str(response.content).strip()
            return value[:12000] if value else previous_summary[:12000]
        except Exception:
            additions = "\n".join(
                f"{str(item.get('role') or '')}：{str(item.get('content') or '').strip()}"
                for item in compression_messages if str(item.get("content") or "").strip()
            )
            return f"{previous_summary.strip()}\n{additions}".strip()[-12000:]

    async def build_memory_summary(self, request: ConversationAgentRequest, result: ConversationAgentResult) -> str | None:
        if not request.memory_compression_messages or request.memory_compression_through_sequence <= request.summary_through_sequence:
            return None
        result.memory_summary_through_sequence = request.memory_compression_through_sequence
        return await self.compress_memory(request.summary, request.memory_compression_messages)

    async def resume(self, request: ConversationResumeRequest) -> ConversationAgentResult:
        if request.resume_kind == "ARTIST_IDENTITY":
            return await self.run_candidate_pool(request.guest_id, request.preference_text, request.pool_size, request.confirmed_artists)
        original = dict(request.original_request)
        original.update({
            "requestId": str(request.request_id),
            "agentRunId": str(request.agent_run_id),
            "conversationId": str(request.conversation_id),
            "guestId": request.guest_id,
            "userMessage": f"{str(original.get('userMessage') or request.preference_text)}；针对上一轮必要澄清，用户确认：{request.answer}",
            "forcedAction": None,
            "confirmedArtists": [item.model_dump(by_alias=True) for item in request.confirmed_artists],
        })
        context = list(original.get("recentMessages") or [])
        context.append({"role": "USER", "content": request.answer})
        original["recentMessages"] = context[-12:]
        resumed = ConversationAgentRequest.model_validate(original)
        result = None
        async for state in self.graph.astream(
            {"request": resumed},
            {"configurable": {"thread_id": str(request.agent_run_id)}, "recursion_limit": 24},
            stream_mode="values",
        ):
            result = state.get("result") or result
        if result is None:
            raise RuntimeError("resumed conversation result missing")
        result.memory_summary = await self.build_memory_summary(resumed, result)
        return result

    async def _build_candidate_pool_result(self, state: ConversationState, preference_text: str, size: int, confirmed_artists: list[ConfirmedArtist]):
        request = state["request"]
        result = await self.run_candidate_pool(request.guest_id, preference_text, size, confirmed_artists)
        result.trace_summary = {
            **result.trace_summary,
            "actions": [item["action"] for item in state.get("action_history", [])],
        }
        return {
            "result": result,
            "observations": state["observations"] + [{"action": "build_candidate_pool", "status": "success", "outputCount": result.trace_summary.get("candidateCount", 0)}],
        }

    async def _recommend_music_result(self, state: ConversationState) -> dict:
        request = state["request"]
        sources = list(state.get("web_sources", []))
        result = await self.recommend_music(
            request, sources, state.get("preference_profile", {}), state.get("entity_resolutions", [])
        )
        gaps = _recommendation_result_gaps(
            request, result, state.get("preference_profile", {}), state.get("entity_resolutions", [])
        )
        # The fallback path has no model capable of revising the draft. Retrying it
        # only burns graph iterations and eventually changes a valid degraded
        # recommendation into a generic response. Adaptive gap repair is therefore
        # reserved for real model-backed runs.
        model_degraded = bool(result.trace_summary.get("modelDegraded"))
        if gaps and self.model is not None and not model_degraded and state.get("search_attempts", 0) < 3:
            research = {
                **state.get("research_assessment", {}), "ready": False,
                "resultGaps": gaps,
                "nextStep": "已检查初稿，正在针对" + "、".join(gaps) + "补充新的歌曲来源",
                "nextQuery": _profile_search_query(request, state.get("preference_profile", {}), state.get("search_attempts", 0) + 1),
            }
            return {
                "research_assessment": research,
                "observations": state["observations"] + [{
                    "action": "recommend_music", "status": "incomplete", "resultGaps": gaps,
                    "verifiedCount": int(result.trace_summary.get("verifiedSongCount", 0)),
                }],
            }
        return {
            "result": result,
            "observations": state["observations"] + [{
                "action": "recommend_music", "status": "success",
                "sourceCount": len(sources),
                "verifiedCount": int(result.trace_summary.get("verifiedSongCount", 0)),
            }],
        }

    async def recommend_music(
        self, request: ConversationAgentRequest, sources: list[dict],
        preference_profile: dict | None = None, entity_resolutions: list[dict] | None = None,
    ) -> ConversationAgentResult:
        preference_profile = preference_profile or _fallback_preference_profile(
            request, _explicit_artist_mentions(request.user_message)
        ).model_dump(by_alias=True)
        named_artists = list(preference_profile.get("named_artists") or preference_profile.get("namedArtists") or _explicit_artist_mentions(request.user_message))
        source_urls = {str(item.get("sourceUrl") or "") for item in sources if item.get("sourceUrl")}
        prior_cards = _recommendation_payloads(request)
        for card in prior_cards:
            for item in list(card["payload"].get("songs", [])) + list(card["payload"].get("artists", [])):
                if isinstance(item, dict) and item.get("sourceUrl"):
                    source_urls.add(str(item["sourceUrl"]))
        if self.model is None or not source_urls:
            return ConversationAgentResult(
                text=_fallback_answer({"request": request, "web_sources": sources}),
                card_intent=_public_source_card(sources), action="recommend_music",
                trace_summary={"webSourceCount": len(sources), "verifiedSongCount": 0, "modelDegraded": True},
            )
        compact = [{
            "url": item.get("sourceUrl"), "title": item.get("sourceTitle"),
            "summary": str(item.get("summary") or "")[:900],
            "searchQuery": item.get("searchQuery"),
        } for item in sources[:24]]
        prompt = f"""你是 IndieSoundQuest 的普通音乐推荐复合工具，不输出思维链。
用户请求：{request.user_message}
用户本轮明确写出的艺人：{json.dumps(named_artists, ensure_ascii=False)}
结构化音乐偏好：{json.dumps(preference_profile, ensure_ascii=False)}
已解析艺人身份：{json.dumps(entity_resolutions or [], ensure_ascii=False)}
会话摘要：{request.summary}
最近对话：{request.recent_messages}
上一轮推荐上下文：{json.dumps(_recent_card_context(request), ensure_ascii=False)}
公开资料：{json.dumps(compact, ensure_ascii=False)}
请先给出 8–12 首可供身份核验的歌曲候选和 3–5 位艺人候选；下游会从中筛选 5–7 首已核验歌曲和 2–3 位艺人展示。资料确实不足时可以更少，不能硬凑。
如果用户明确写出一位艺人并要求推荐其歌曲，在公开资料允许时至少给出 5 首该艺人的具体歌曲候选，不能只返回艺人卡。如果用户明确写出多位艺人，必须把这些艺人视作并列的偏好证据，而不是只围绕搜索结果最多的一位展开；歌曲候选至少覆盖其中 {min(5, len(named_artists)) if named_artists else 0} 位，并另外保留相近艺人的探索空间。不得把某一查询结果偏多误写成“其他艺人无资料”。
把本轮话语视为对上一轮推荐的增量约束：正确理解“更冷一点”“换一批”“不要这些艺人”“保留某首”等指代。歌曲名、艺人名及 sourceUrl 必须来自给定公开资料或上一轮已持久化推荐，禁止凭记忆编造；sourceUrl 必须逐字复制。
reason 要具体映射到结构化偏好中的声音、情绪、文本、场景或创作脉络，不能只写“符合你的偏好”。summary 概括推荐逻辑并明确哪些是用户原话、哪些是保守推断以及证据边界。
返回 MusicRecommendationDraft。"""
        draft = None
        # Provider retries do not cover malformed tool arguments. Give the
        # model one bounded schema-repair attempt before degrading to a source
        # card, so a transient formatting miss does not erase recommendations.
        structured_model = self.model.with_structured_output(MusicRecommendationDraft, method="function_calling")
        for attempt in range(2):
            try:
                repair = "" if attempt == 0 else "\n上一次结果结构无效或过度集中于单一艺人。请缩短 reason，严格满足字段类型与 sourceUrl 原样复制要求，并重新检查用户明确列出的每位艺人及其对应查询来源。"
                draft = await structured_model.ainvoke(prompt + repair)
                required_coverage = min(5, len(named_artists))
                single_artist_song_shortfall = len(named_artists) == 1 and sum(
                    1 for item in draft.songs if _artist_key_covered(_music_key(named_artists[0]), {_music_key(item.artist_name)})
                ) < 5
                if named_artists and (_draft_named_artist_coverage(draft, named_artists) < required_coverage or single_artist_song_shortfall) and attempt == 0:
                    continue
                break
            except Exception:
                continue
        if draft is None:
            return ConversationAgentResult(
                text=_fallback_answer({"request": request, "web_sources": sources}),
                card_intent=_public_source_card(sources), action="recommend_music",
                trace_summary={"webSourceCount": len(sources), "verifiedSongCount": 0, "modelDegraded": True},
            )
        songs = _unique_song_drafts([item for item in draft.songs if item.source_url in source_urls])
        artists = _unique_artist_drafts([item for item in draft.artists if item.source_url in source_urls])
        if _requests_fresh_set(request.user_message):
            prior_song_keys = {
                _music_key(str(item.get("title") or "") + "|" + str(item.get("artistName") or ""))
                for card in prior_cards for item in card["payload"].get("songs", []) if isinstance(item, dict)
            }
            songs = [item for item in songs if _music_key(item.title + "|" + item.artist_name) not in prior_song_keys]
        if re.search(r"不要(?:这些|上次|上一轮)(?:推荐的)?艺人|换一批艺人", request.user_message, re.I):
            prior_artist_keys = {
                _music_key(str(item.get("artistName") or ""))
                for card in prior_cards
                for item in list(card["payload"].get("songs", [])) + list(card["payload"].get("artists", []))
                if isinstance(item, dict) and item.get("artistName")
            }
            songs = [item for item in songs if _music_key(item.artist_name) not in prior_artist_keys]
            artists = [item for item in artists if _music_key(item.artist_name) not in prior_artist_keys]
        resolutions: list[dict] = []
        if songs and self.catalog is not None:
            try:
                resolutions = await self.catalog.resolve_and_import([
                    {"title": item.title, "artistName": item.artist_name, "sourceUrl": item.source_url}
                    for item in songs
                ])
            except Exception:
                resolutions = []
        verified_songs = []
        verified_artists: dict[str, dict] = {}
        for suggestion, resolved in zip(songs, resolutions):
            if str(resolved.get("status")) != "RESOLVED" or not resolved.get("recordingId"):
                continue
            artist_name = str(resolved.get("artistName") or suggestion.artist_name)
            verified_songs.append({
                "recordingId": str(resolved["recordingId"]),
                "artistId": str(resolved.get("artistId") or ""),
                "artistMbid": str(resolved.get("artistMbid") or ""),
                "title": str(resolved.get("title") or suggestion.title),
                "artistName": artist_name,
                "albumTitle": str(resolved.get("albumTitle") or ""),
                "coverUrl": str(resolved.get("coverUrl") or ""),
                "reason": suggestion.reason,
                "sourceUrl": suggestion.source_url,
                "searchUrl": _netease_search_url(str(resolved.get("title") or suggestion.title), artist_name),
                "verificationStatus": "MUSICBRAINZ_VERIFIED",
            })
            verified_artists[_music_key(artist_name)] = {
                "artistId": str(resolved.get("artistId") or ""), "artistName": artist_name,
            }
        canonical_artists = list(dict.fromkeys(
            str(item.get("name") or "") for item in (entity_resolutions or [])
            if item.get("status") == "RESOLVED" and item.get("name")
        ))
        verified_songs = await self._supplement_named_artist_tracks(canonical_artists or named_artists, verified_songs, sources)
        artist_items = []
        for suggestion in artists:
            identity = verified_artists.get(_music_key(suggestion.artist_name), {})
            artist_items.append({
                **identity, "artistName": suggestion.artist_name, "reason": suggestion.reason,
                "sourceUrl": suggestion.source_url,
                "searchUrl": _netease_artist_search_url(suggestion.artist_name),
                "verificationStatus": "MUSICBRAINZ_VERIFIED" if identity else "WEB_DISCOVERED",
            })
        if not verified_songs and not artist_items:
            return ConversationAgentResult(
                text="我找到了相关公开资料，但其中的具体歌曲暂时没有通过 MusicBrainz 身份核验；先把原始资料留给你继续查看。",
                card_intent=_public_source_card(sources), action="recommend_music",
                trace_summary={"webSourceCount": len(sources), "verifiedSongCount": 0, "unresolvedSongCount": len(songs)},
            )
        verified_songs = _diverse_song_order(list({item["recordingId"]: item for item in verified_songs}.values()))
        visible_song_count = min(7, len(verified_songs))
        final_summary = (
            f"已从公开资料候选中核验并展示 {visible_song_count} 首歌曲。{draft.summary}"
            if visible_song_count else draft.summary
        )
        payload = {
            "title": "这轮音乐探索的推荐方向", "summary": final_summary,
            "songs": verified_songs[:7], "artists": artist_items[:3],
            "sources": _public_source_items(sources, limit=5),
            "preferenceProfile": preference_profile,
        }
        if prior_cards and _recommendation_followup(request):
            payload.update({
                "contextMode": "REFINED",
                "basedOnCardId": prior_cards[-1]["messageId"],
                "appliedInstruction": request.user_message[:240],
            })
        return ConversationAgentResult(
            text=final_summary,
            card_intent=ConversationCardIntent(message_type="RECOMMENDATION_CARD", card_type="MUSIC_RECOMMENDATIONS", payload=payload),
            action="recommend_music",
            trace_summary={
                "webSourceCount": len(sources), "proposedSongCount": len(songs),
                "verifiedSongCount": len(verified_songs), "artistCount": len(artist_items),
                "preferenceDimensions": _active_preference_dimensions(preference_profile),
            },
        )

    async def _supplement_named_artist_tracks(
        self, mentions: list[str], verified_songs: list[dict], sources: list[dict]
    ) -> list[dict]:
        """Repair a collapsed multi-artist result with MusicBrainz-verified facts.

        This is a postcondition guard for an explicit user contract, not a fixed
        discovery workflow: it runs only when the Supervisor chose recommendation,
        multiple literal artists were supplied, and verified output lost coverage.
        """
        if self.catalog is None or not mentions:
            return verified_songs
        target_coverage = min(5, len(mentions))
        covered = {_music_key(str(item.get("artistName") or "")) for item in verified_songs}
        covered_artist_mbids = {str(item.get("artistMbid") or "") for item in verified_songs if item.get("artistMbid")}
        if len(mentions) == 1:
            missing = list(mentions) if len(verified_songs) < 3 else []
        else:
            missing = [mention for mention in mentions if not _artist_key_covered(_music_key(mention), covered)]
        if not missing or (len(mentions) > 1 and len(mentions) - len(missing) >= target_coverage):
            return verified_songs
        try:
            resolutions = await self.catalog.resolve_artist_candidates(missing)
        except Exception:
            return verified_songs
        seeds: list[dict] = []
        seen_mbids: set[str] = set()
        for resolution in resolutions:
            candidates = resolution.get("candidates") or []
            if not candidates:
                continue
            top = candidates[0]
            score = int(top.get("score") or 0)
            runner_up = int(candidates[1].get("score") or 0) if len(candidates) > 1 else 0
            if score < 95 or (runner_up >= score - 5 and _music_key(str(top.get("name") or "")) != _music_key(str(resolution.get("mention") or ""))):
                continue
            # Stage names and real names can resolve to the same MusicBrainz
            # artist (for example 张悬 and 安溥). Do not manufacture another
            # coverage slot or extra card for the same actual artist.
            if _artist_key_covered(_music_key(str(top.get("name") or "")), covered):
                continue
            if str(top.get("mbid") or "") in covered_artist_mbids:
                continue
            if top.get("mbid") and top.get("name") and str(top["mbid"]) not in seen_mbids:
                seen_mbids.add(str(top["mbid"]))
                seeds.append({"mbid": str(top["mbid"]), "name": str(top["name"]), "mention": str(resolution.get("mention") or top["name"])})
        if not seeds:
            return verified_songs
        try:
            # Browse enough of the catalogue to find titles actually mentioned
            # by public sources. MusicBrainz browse order is not a popularity
            # ranking and its first item can be an obscure recording.
            discovered = await self.catalog.discover_artist_recordings(seeds, per_artist_limit=32)
        except Exception:
            return verified_songs
        existing_ids = {str(item.get("recordingId") or "") for item in verified_songs}
        supplements: list[dict] = []
        for seed in seeds:
            catalog_url = f"https://musicbrainz.org/artist/{seed['mbid']}"
            candidates = [
                item for item in discovered
                if item.get("recordingId")
                and str(item.get("recordingId")) not in existing_ids
                and str(item.get("sourceUrl") or "") == catalog_url
            ]
            desired = max(1, 3 - len(verified_songs)) if len(mentions) == 1 else 1
            for _ in range(desired):
                candidate, evidence_url = _select_evidence_backed_recording(seed, candidates, sources)
                if candidate is None:
                    break
                candidates.remove(candidate)
                title = str(candidate.get("title") or "")
                artist_name = str(candidate.get("artistName") or seed["name"])
                if not title:
                    continue
                supplements.append({
                    "recordingId": str(candidate["recordingId"]), "artistId": str(candidate.get("artistId") or ""),
                    "artistMbid": str(seed["mbid"]), "title": title,
                    "artistName": artist_name, "albumTitle": str(candidate.get("albumTitle") or ""),
                    "coverUrl": str(candidate.get("coverUrl") or ""),
                    "reason": f"你在本轮明确提到{seed['mention']}；这首作品已通过公开目录与 MusicBrainz 身份核验。",
                    "sourceUrl": evidence_url or catalog_url, "searchUrl": _netease_search_url(title, artist_name),
                    "verificationStatus": "MUSICBRAINZ_VERIFIED",
                })
                existing_ids.add(str(candidate["recordingId"]))
                covered.add(_music_key(artist_name))
        # Put coverage repairs before the model-ranked tail so the seven-card
        # presentation limit cannot silently discard every repaired artist.
        return supplements + verified_songs


def _query_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _next_analysis_query(state: ConversationState, request: ConversationAgentRequest) -> str:
    """Choose an untried evidence-gap query without imposing a tool order."""
    attempted = {_query_key(item) for item in state.get("analysis_queries", []) if item}
    suggestions = list(state.get("evidence_coverage", {}).get("suggested_queries") or [])
    workspace = state.get("analysis_workspace", {})
    subject = str(workspace.get("subject") or "").strip()
    open_questions = list(workspace.get("open_questions") or [])
    candidates = [
        *suggestions,
        *(f"{subject} {question}" for question in open_questions[:3] if subject and question),
        f"{subject} 创作背景 采访 制作 编曲" if subject else "",
        request.user_message,
    ]
    for candidate in candidates:
        value = str(candidate).strip()[:300]
        if value and _query_key(value) not in attempted:
            return value
    suffix = len(attempted) + 1
    return f"{subject or request.user_message[:160]} 音乐资料 独立来源 补充检索 {suffix}"[:300]


def _deduplicate_analysis_query(
    state: ConversationState, request: ConversationAgentRequest, proposed: str,
) -> str:
    attempted = {_query_key(item) for item in state.get("analysis_queries", []) if item}
    return _next_analysis_query(state, request) if _query_key(proposed) in attempted else proposed[:300]


def _fallback_decision(state: ConversationState) -> ConversationDecision:
    text = state["request"].user_message.strip()
    request = state["request"]
    previous = {item["action"] for item in state.get("action_history", [])}
    report_request = bool(re.search(r"(?:生成|给我|看看).{0,8}(?:报告|偏好分析|探索报告)|根据.{0,8}(?:对话|聊天).{0,8}(?:报告|分析)", text, re.I))
    recommendation_signal = _recommendation_intent(request)
    analysis_signal = bool(state.get("analysis_workspace")) or deep_analysis_intent(request)
    if report_request and ("search_web" in previous or "search_knowledge" in previous):
        return ConversationDecision(action="generate_exploration_report", public_summary="整理当前对话中的偏好线索并生成探索报告")
    if "search_web" in previous or "search_knowledge" in previous:
        if analysis_signal:
            return ConversationDecision(action="analyze_music", public_summary="根据已有资料形成深入音乐分析")
        if recommendation_signal:
            return ConversationDecision(action="recommend_music", public_summary="提取并核验具体歌曲与艺人推荐")
        return ConversationDecision(action="respond", public_summary="资料已经准备好，开始整理回答")
    immediate_pool = _explicit_pool_request(text)
    question_signal = bool(re.search(r"为什么|是什么|介绍|背景|历史|最近|最新|谁是|有哪些", text))
    if immediate_pool:
        return ConversationDecision(action="build_candidate_pool", public_summary="开始构建并核验歌曲世界杯候选池")
    if report_request:
        return ConversationDecision(action="generate_exploration_report", public_summary="整理当前对话中的偏好线索并生成探索报告")
    if _explicit_tournament_request(text):
        return ConversationDecision(action="propose_tournament", public_summary="偏好方向已经明确，准备歌曲世界杯入口")
    if analysis_signal:
        return ConversationDecision(action="search_web", public_summary="正在查找能够支持核心分析的公开资料", query=text[:300])
    if question_signal or recommendation_signal:
        query = text if question_signal else f"{text} 音乐推荐 相似艺人 歌曲"
        return ConversationDecision(action="search_web", public_summary="正在检索相关音乐资料与推荐线索", query=query[:300])
    if len(text) < 5:
        return ConversationDecision(
            action="clarify", public_summary="还需要一个更明确的音乐起点",
            clarification_question="你希望从哪位艺人、哪首歌，或哪种情绪与场景开始？",
            clarification_reason="当前信息不足以确定你想探索的音乐方向。",
        )
    return ConversationDecision(action="respond", public_summary="结合当前对话整理音乐回应")


def _recommendation_request(text: str) -> bool:
    return bool(re.search(r"推荐|相似|相近|类似|适合|歌单|听什么|探索方向|共同点", text, re.I))


def _explicit_artist_mentions(text: str) -> list[str]:
    """Best-effort literal extraction used only to guarantee search coverage.

    The model remains responsible for semantic intent.  Values returned here
    must occur in the user's text and are never treated as resolved identities.
    """
    match = re.search(r"(?:我最喜欢|我喜欢|最喜欢|喜欢|常听|爱听)\s*([^。！？；\n]{1,240})", text, re.I)
    if not match:
        direct = re.search(
            r"(?:给我推荐|推荐给我|推荐|想听|听听)(?:一下|几首|一些|点)?\s*([^。！？；，,\n]{2,60}?)\s*的(?:歌曲|歌|作品)",
            text, re.I,
        )
        if not direct:
            return []
        value = direct.group(1).strip()
        return [value] if value and value in text else []
    clause = re.split(
        r"[,，](?=(?:根据|想|希望|要|请|可以|适合|用于|并|但|然后|探索|寻找|做|来|推荐|分析))",
        match.group(1), maxsplit=1,
    )[0]
    clause = re.split(r"(?:的歌曲|的歌)(?:\s|$)", clause, maxsplit=1)[0]
    values = re.split(r"[、,，/]|(?:和|与|及)", clause)
    non_artist_terms = {"克制", "温柔", "忧郁", "明亮", "治愈", "孤独", "浪漫", "热烈", "安静", "冷峻", "中文", "华语", "独立音乐", "民谣", "摇滚", "电子", "爵士", "流行", "说唱"}
    mentions: list[str] = []
    for value in values:
        cleaned = re.sub(r"(?:等)?(?:歌手|艺人|樂隊|乐队)$", "", value.strip(), flags=re.I).strip()
        if 1 < len(cleaned) <= 60 and cleaned not in non_artist_terms and cleaned in text:
            mentions.append(cleaned)
    return list(dict.fromkeys(mentions))[:8]


def _recommendation_search_queries(
    request: ConversationAgentRequest, planned_query: str, focus_artists: list[str] | None = None,
    preference_profile: dict | None = None,
) -> list[dict]:
    mentions = focus_artists or _explicit_artist_mentions(request.user_message)
    if not mentions:
        direct = f"{request.user_message} 具体歌曲 歌手 专辑"
        axes = list((preference_profile or {}).get("discovery_axes") or (preference_profile or {}).get("discoveryAxes") or [])
        language = " ".join((preference_profile or {}).get("languages_regions") or (preference_profile or {}).get("languagesRegions") or [])
        queries = [
            {"query": direct[:300], "purpose": "explicit_preference_coverage"},
            {"query": planned_query, "purpose": "conversation_research"},
        ]
        queries.extend({
            "query": f"{language} {axis} 推荐 具体歌曲 歌手 专辑"[:300],
            "purpose": "preference_axis_coverage",
        } for axis in axes[:3])
        unique: list[dict] = []
        seen: set[str] = set()
        for item in queries:
            value = str(item["query"]).strip()
            if value and value not in seen:
                seen.add(value); unique.append(item)
        return unique[:5]
    queries = [
        {"query": f'"{artist}" 代表作 歌曲 专辑 曲目', "purpose": "explicit_artist_coverage"}
        for artist in mentions
    ]
    if len(queries) < 8:
        queries.append({"query": planned_query, "purpose": "conversation_research"})
    return queries[:8]


def _fallback_preference_profile(
    request: ConversationAgentRequest, literal_artists: list[str]
) -> MusicPreferenceProfile:
    text = " ".join([
        request.summary, *(str(item.get("content") or "") for item in request.recent_messages[-12:]), request.user_message,
    ])
    mappings = {
        "genres": ["民谣", "摇滚", "电子", "爵士", "流行", "说唱", "R&B", "灵魂乐", "后摇", "古典"],
        "moods": ["克制", "温柔", "忧郁", "明亮", "治愈", "孤独", "浪漫", "热烈", "安静", "冷峻", "松弛"],
        "scenes": ["深夜", "写代码", "通勤", "散步", "学习", "工作", "开车", "睡前", "运动"],
        "languages_regions": ["华语", "中文", "台语", "粤语", "英语", "欧美", "日本", "韩国"],
        "eras": ["八十年代", "九十年代", "千禧年", "00年代", "10年代", "近年"],
        "lyrical_themes": ["城市", "成长", "亲密关系", "自我", "社会观察", "家庭", "青春", "孤独"],
    }
    values = {key: [term for term in terms if term.casefold() in text.casefold()] for key, terms in mappings.items()}
    familiarity = "deep_cuts" if re.search(r"冷门|小众|深挖|非热门|不要代表作", text) else "representative" if re.search(r"代表作|入门|热门", text) else "mixed"
    axes = [*values["genres"][:2], *values["moods"][:2], *values["scenes"][:1]]
    if literal_artists:
        axes.extend(f"从{artist}的相邻创作脉络展开" for artist in literal_artists[:3])
    return MusicPreferenceProfile(
        named_artists=literal_artists, familiarity=familiarity,
        evidence_spans=[request.user_message[:240]], discovery_axes=list(dict.fromkeys(axes))[:6],
        **values,
    )


def _active_preference_dimensions(profile: dict) -> list[str]:
    names = []
    for key in ("named_artists", "namedArtists", "genres", "sonic_traits", "sonicTraits", "moods", "scenes", "languages_regions", "languagesRegions", "eras", "lyrical_themes", "lyricalThemes"):
        if profile.get(key):
            names.append(key)
    return list(dict.fromkeys(names))


def _preference_plan_summary(profile: MusicPreferenceProfile) -> str:
    dimensions = [
        *profile.named_artists[:3], *profile.genres[:2], *profile.sonic_traits[:2],
        *profile.moods[:2], *profile.scenes[:1],
    ]
    return "已提取本轮偏好维度" + ("：" + "、".join(dict.fromkeys(dimensions)) if dimensions else "，将从用户原话继续判断")


def _merge_web_sources(existing: list[dict], incoming: list[dict], prefer_incoming: bool = False) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for item in ([*incoming, *existing] if prefer_incoming else [*existing, *incoming]):
        url = str(item.get("sourceUrl") or "")
        key = url or _music_key(str(item.get("sourceTitle") or "") + str(item.get("summary") or ""))
        if key and key not in seen:
            seen.add(key)
            merged.append(item)
    return merged[:80]


def _assess_research(request: ConversationAgentRequest, profile: dict, sources: list[dict]) -> dict:
    artists = list(profile.get("named_artists") or profile.get("namedArtists") or _explicit_artist_mentions(request.user_message))
    coverage: dict[str, int] = {}
    for artist in artists:
        key = _music_key(artist)
        coverage[artist] = sum(
            1 for item in sources
            if key and key in _music_key(" ".join(str(item.get(field) or "") for field in ("searchQuery", "sourceTitle", "summary")))
        )
    missing = [artist for artist, count in coverage.items() if count == 0]
    if artists:
        ready = not missing and len(sources) >= min(5, len(artists) * 2)
        next_step = "继续补足" + "、".join(missing) + "的公开歌曲资料" if missing else "公开资料已覆盖明确艺人，可以进入歌曲提取与核验"
    else:
        axes = list(profile.get("discovery_axes") or profile.get("discoveryAxes") or [])
        ready = len(sources) >= 6
        next_step = "沿着" + "、".join(axes[:3]) + "继续扩展搜索切面" if axes else "继续补充与本轮场景和声音要求相关的音乐资料"
    return {
        "ready": ready, "sourceCount": len(sources), "artistCoverage": coverage,
        "missingArtists": missing, "activeDimensions": _active_preference_dimensions(profile),
        "nextStep": next_step,
    }


def _research_gap_query(request: ConversationAgentRequest, assessment: dict) -> str:
    missing = list(assessment.get("missingArtists") or [])
    if missing:
        return "；".join(f'"{artist}" 代表作 歌曲 专辑 曲目' for artist in missing)[:300]
    return str(assessment.get("nextQuery") or f"{request.user_message} 具体歌曲 专辑 曲目 推荐")[:300]


def _profile_search_query(request: ConversationAgentRequest, profile: dict, attempt: int) -> str:
    languages = " ".join(profile.get("languages_regions") or profile.get("languagesRegions") or [])
    genres = " ".join((profile.get("genres") or [])[:3])
    traits = " ".join((profile.get("sonic_traits") or profile.get("sonicTraits") or [])[:3])
    moods = " ".join((profile.get("moods") or [])[:2])
    scenes = " ".join((profile.get("scenes") or [])[:2])
    suffix = "代表歌曲 曲目 专辑 乐评" if attempt % 2 else "具体歌名 音乐人 歌单"
    return " ".join(value for value in [languages, genres, traits, moods, scenes, suffix] if value)[:300] or request.user_message[:300]


def _recommendation_result_gaps(
    request: ConversationAgentRequest, result: ConversationAgentResult, profile: dict,
    entity_resolutions: list[dict] | None = None,
) -> list[str]:
    if not result.card_intent or result.card_intent.card_type != "MUSIC_RECOMMENDATIONS":
        return ["具体歌曲结果"] if _recommendation_intent(request) else []
    songs = list(result.card_intent.payload.get("songs") or [])
    gaps: list[str] = []
    if len(songs) < 3:
        gaps.append("至少三首可核验歌曲")
    named = list(profile.get("named_artists") or profile.get("namedArtists") or [])
    if len(named) > 1:
        expected_mbids = {
            str(item.get("mbid") or "") for item in (entity_resolutions or [])
            if item.get("status") == "RESOLVED" and item.get("mbid")
        }
        represented_mbids = {str(item.get("artistMbid") or "") for item in songs if item.get("artistMbid")}
        represented_names = {_music_key(str(item.get("artistName") or "")) for item in songs}
        coverage = len(expected_mbids & represented_mbids) if expected_mbids else sum(
            1 for artist in named if _artist_key_covered(_music_key(artist), represented_names)
        )
        target = min(5, len(expected_mbids) if expected_mbids else len(named))
        if coverage < target:
            gaps.append("明确艺人的均衡覆盖")
    languages = " ".join(profile.get("languages_regions") or profile.get("languagesRegions") or [])
    if re.search(r"华语|中文|台语|粤语", languages) and songs:
        cjk_count = sum(bool(re.search(r"[\u3400-\u9fff]", str(item.get("title") or "") + str(item.get("artistName") or ""))) for item in songs)
        if cjk_count < max(2, (len(songs) + 1) // 2):
            gaps.append("华语音乐方向")
    return gaps


def _entity_clarification_decision(ambiguities: list[dict]) -> ConversationDecision:
    first = ambiguities[0]
    mention = str(first.get("mention") or "这位艺人")
    options = [
        " · ".join(value for value in [str(item.get("name") or ""), str(item.get("country") or ""), str(item.get("disambiguation") or "")] if value)
        for item in first.get("candidates", [])
    ]
    return ConversationDecision(
        action="clarify", public_summary=f"发现“{mention}”可能对应多个艺人，等待确认",
        clarification_question=f"你提到的“{mention}”具体是哪一位？",
        clarification_reason="不同身份会得到完全不同的歌曲结果，需要先确认再继续。",
        clarification_options=options,
    )


def _artist_key_covered(mention_key: str, covered_keys: set[str]) -> bool:
    return any(mention_key == value or mention_key in value or value in mention_key for value in covered_keys if value)


def _draft_named_artist_coverage(draft: MusicRecommendationDraft, mentions: list[str]) -> int:
    # An artist-only suggestion cannot satisfy a request for recommended music.
    # Coverage is therefore measured on concrete song candidates only.
    represented = {_music_key(item.artist_name) for item in draft.songs}
    return sum(1 for mention in mentions if _artist_key_covered(_music_key(mention), represented))


def _diverse_song_order(items: list[dict]) -> list[dict]:
    """Keep one verified song per artist before additional tracks."""
    first_by_artist: dict[str, dict] = {}
    remainder: list[dict] = []
    for item in items:
        key = _music_key(str(item.get("artistName") or ""))
        if key and key not in first_by_artist:
            first_by_artist[key] = item
        else:
            remainder.append(item)
    return [*first_by_artist.values(), *remainder]


def _select_evidence_backed_recording(
    seed: dict, candidates: list[dict], sources: list[dict]
) -> tuple[dict | None, str]:
    """Choose a catalogue recording by public evidence, never browse order.

    Search results issued for this explicit artist are preferred. A title that
    occurs in their title/snippet/excerpt is strong evidence; metadata quality
    only breaks ties. If snippets do not expose a track list, prefer a useful
    catalogue entry over a short generic one-word title.
    """
    if not candidates:
        return None, ""
    mention_keys = {
        _music_key(str(seed.get("mention") or "")),
        _music_key(str(seed.get("name") or "")),
    }
    relevant_sources: list[dict] = []
    for source in sources:
        query_key = _music_key(str(source.get("searchQuery") or ""))
        if any(key and key in query_key for key in mention_keys):
            relevant_sources.append(source)
    evidence_sources = relevant_sources or sources

    def evidence(candidate: dict) -> tuple[int, int, str]:
        title = str(candidate.get("title") or "").strip()
        title_key = _music_key(title)
        matched_url = ""
        evidence_score = 0
        if len(title_key) >= 2:
            for source in evidence_sources:
                haystack = _music_key(" ".join(str(source.get(field) or "") for field in ("sourceTitle", "summary", "pageExcerpt")))
                if title_key in haystack:
                    evidence_score = 100
                    matched_url = str(source.get("sourceUrl") or "")
                    break
        quality = 0
        if candidate.get("albumTitle"):
            quality += 8
        if candidate.get("coverUrl"):
            quality += 4
        if re.search(r"[\u3400-\u9fff]", title):
            quality += 4
        if 3 <= len(title_key) <= 24:
            quality += 2
        if re.fullmatch(r"[A-Za-z]{1,5}", title):
            quality -= 12
        return evidence_score, quality, matched_url

    ranked = [(evidence(item), item) for item in candidates]
    ranked.sort(key=lambda pair: (pair[0][0], pair[0][1]), reverse=True)
    best_evidence, best = ranked[0]
    return best, best_evidence[2]


def _recommendation_payloads(request: ConversationAgentRequest) -> list[dict]:
    values: list[dict] = []
    for card in request.recent_cards:
        if not isinstance(card, dict) or card.get("cardType") != "MUSIC_RECOMMENDATIONS":
            continue
        payload = card.get("payload")
        if isinstance(payload, dict):
            values.append({"messageId": str(card.get("messageId") or ""), "payload": payload})
    return values


def _recent_card_context(request: ConversationAgentRequest) -> list[dict]:
    context: list[dict] = []
    for card in _recommendation_payloads(request)[-2:]:
        payload = card["payload"]
        context.append({
            "messageId": card["messageId"],
            "summary": str(payload.get("summary") or "")[:360],
            "songs": [
                {"title": str(item.get("title") or ""), "artistName": str(item.get("artistName") or ""), "sourceUrl": str(item.get("sourceUrl") or "")}
                for item in payload.get("songs", [])[:7] if isinstance(item, dict)
            ],
            "artists": [
                {"artistName": str(item.get("artistName") or ""), "sourceUrl": str(item.get("sourceUrl") or "")}
                for item in payload.get("artists", [])[:3] if isinstance(item, dict)
            ],
        })
    for card in request.recent_cards:
        if not isinstance(card, dict) or card.get("cardType") != "MUSIC_ANALYSIS":
            continue
        payload = card.get("payload")
        if not isinstance(payload, dict):
            continue
        context.append({
            "messageId": str(card.get("messageId") or ""),
            "cardType": "MUSIC_ANALYSIS",
            "summary": str(payload.get("coreJudgment") or "")[:600],
            "selectedDimensions": list(payload.get("selectedDimensions") or [])[:8],
            "claims": [
                {
                    "statement": str(item.get("statement") or "")[:420],
                    "claimType": str(item.get("claimType") or ""),
                    "confidence": str(item.get("confidence") or ""),
                    "status": str(item.get("status") or ""),
                    "evidenceRefs": list(item.get("evidenceRefs") or [])[:6],
                }
                for item in list(payload.get("claims") or [])[:6] if isinstance(item, dict)
            ],
            "sources": [
                {"ref": str(item.get("ref") or ""), "title": str(item.get("title") or "")[:180], "url": str(item.get("url") or "")}
                for item in list(payload.get("sources") or [])[:8] if isinstance(item, dict)
            ],
            "songs": [], "artists": [],
        })
    return context[-4:]


def _recommendation_followup(request: ConversationAgentRequest) -> bool:
    if not _recommendation_payloads(request):
        return False
    return bool(re.search(r"更.{0,8}(?:一点|一些)|换一批|换一些|不要|别要|排除|去掉|保留|留下|继续(?:推荐)?|再来|重新推荐|不喜欢|太.{1,10}|这(?:些|批|几首|几位)|上一轮|上次", request.user_message, re.I))


def _recommendation_intent(request: ConversationAgentRequest) -> bool:
    return _recommendation_request(request.user_message) or _recommendation_followup(request)


def _requests_fresh_set(text: str) -> bool:
    return bool(re.search(r"换一批|换一些|不要重复|别重复|不要一样|全换|重新推荐|新的(?:歌|推荐)", text, re.I))


def _followup_search_query(request: ConversationAgentRequest, query: str) -> str:
    context = _recent_card_context(request)
    anchors: list[str] = []
    for card in context[-1:]:
        anchors.extend(f"{item['artistName']} {item['title']}".strip() for item in card["songs"][:5])
        anchors.extend(item["artistName"] for item in card["artists"][:2])
    joined = "、".join(value for value in anchors if value)
    return f"{query}；基于上一轮：{joined}；本轮调整：{request.user_message}"[:300]


def _fallback_answer(state: ConversationState) -> str:
    """Evidence-bound response used only when the configured model is unavailable."""
    sources = state.get("web_sources", [])
    if sources:
        titles = [str(item.get("sourceTitle") or "").strip() for item in sources[:4]]
        titles = [title for title in titles if title]
        if titles:
            return "我先从公开音乐资料里找到了几条可继续核对的线索：" + "；".join(titles) + "。相关页面已经整理在下方卡片中，你可以指定其中一条继续深挖。"
        return "我已经找到一些公开音乐资料，并把可核对的来源整理在下方。你可以继续指定想深入的艺人、歌曲或声音方向。"
    return "我已经结合这轮对话整理了你的音乐方向。你可以继续追问喜欢的作品、相近艺人、风格脉络或下一步试听方向。"


def _review_overcompressed(draft: str, revised: str) -> bool:
    """Detect destructive review shrinkage without imposing an answer ceiling."""
    draft_size = len(draft.strip())
    revised_size = len(revised.strip())
    return draft_size >= 600 and revised_size < max(320, int(draft_size * 0.65))


def _public_source_card(sources: list[dict]) -> ConversationCardIntent | None:
    items = _public_source_items(sources)
    if not items:
        return None
    return ConversationCardIntent(
        message_type="RECOMMENDATION_CARD",
        card_type="PUBLIC_MUSIC_SOURCES",
        payload={"title": "沿着这些公开资料继续探索", "items": items},
    )


def _public_source_items(sources: list[dict], limit: int = 6) -> list[dict]:
    items = []
    seen: set[str] = set()
    for source in sources:
        url = str(source.get("sourceUrl") or "").strip()
        if not re.match(r"^https?://", url, re.I) or url in seen:
            continue
        seen.add(url)
        summary = re.sub(r"\s+", " ", str(source.get("summary") or "").replace("|", " ")).strip()
        items.append({
            "title": str(source.get("sourceTitle") or "公开音乐资料").strip()[:180],
            "url": url,
            "summary": summary[:240],
            "provider": str(source.get("searchProvider") or "web").strip()[:40],
        })
        if len(items) >= limit:
            break
    return items


def _music_key(value: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]", "", value.casefold())


def _unique_song_drafts(items: list[RecommendedSongDraft]) -> list[RecommendedSongDraft]:
    unique: dict[str, RecommendedSongDraft] = {}
    for item in items:
        unique.setdefault(_music_key(f"{item.artist_name}|{item.title}"), item)
    return list(unique.values())


def _unique_artist_drafts(items: list[RecommendedArtistDraft]) -> list[RecommendedArtistDraft]:
    unique: dict[str, RecommendedArtistDraft] = {}
    for item in items:
        unique.setdefault(_music_key(item.artist_name), item)
    return list(unique.values())


def _netease_search_url(title: str, artist_name: str) -> str:
    return f"https://music.163.com/#/search/m/?s={quote(f'{artist_name} {title}')}&type=1"


def _netease_artist_search_url(artist_name: str) -> str:
    return f"https://music.163.com/#/search/m/?s={quote(artist_name)}&type=100"


def _resolve_pool_size(request: ConversationAgentRequest) -> int:
    if request.pool_size in {16, 32}:
        return request.pool_size
    return _parse_pool_size(request.user_message)


def _parse_pool_size(text: str) -> int:
    return 16 if re.search(r"(?<!\d)16.{0,5}(?:首|歌)", text, re.I) else 32


def _bounded_text(value: str, limit: int) -> str:
    value = str(value or "").strip()
    return value if len(value) <= limit else value[: max(0, limit - 1)].rstrip() + "…"


def _request_with_interventions(request: ConversationAgentRequest, contents: list[str]) -> ConversationAgentRequest:
    active = list(dict.fromkeys([*request.active_interventions, *contents]))[-12:]
    base = request.user_message.split("\n\n[运行中方向调整]", 1)[0].strip()
    effective = _bounded_text(base, 1200)
    if active:
        effective += "\n\n[运行中方向调整]\n" + "\n".join(
            f"{index}. {_bounded_text(content, 500)}" for index, content in enumerate(active, start=1)
        )
    recent = [*request.recent_messages, *({"role": "USER", "content": content} for content in contents)]
    return request.model_copy(update={
        "user_message": _bounded_text(effective, 2000),
        "active_interventions": active,
        "recent_messages": recent[-1000:],
    })


def _add_intervention_trace(result: ConversationAgentResult, state: ConversationState) -> None:
    result.trace_summary = {
        **result.trace_summary,
        "appliedInterventionSequence": state.get("applied_intervention_sequence", 0),
        "interventionRevision": state.get("intervention_revision", 0),
    }


def _parse_model_json(content) -> dict:
    """Extract one JSON object from common OpenAI-compatible response shapes."""
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    text = str(content or "").strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise TypeError("model JSON response must be an object")
    return value


def _exploration_claims(data: dict, request: ConversationAgentRequest, sources: list[dict]) -> list[dict]:
    claims = [{"text": data.get("summary", ""), "confidence": "medium", "signalRefs": ["conversation"], "evidenceRefs": [], "boundary": "基于当前对话整理，不含未确认的外部事实"}]
    for item in data.get("dimensions") or []:
        if isinstance(item, dict) and item.get("label"):
            claims.append({"text": f"{item.get('label')}：{item.get('summary', '')}", "confidence": "medium", "signalRefs": ["conversation"], "evidenceRefs": [], "boundary": "对话偏好归纳"})
    if sources:
        claims[0]["evidenceRefs"] = [{"type": "PUBLIC_SOURCE", "title": sources[0].get("title"), "url": sources[0].get("url")}]
    if request.confirmed_memories:
        claims[0]["signalRefs"] = ["conversation", "confirmed_memory"]
    if request.recent_feedback:
        claims[0]["signalRefs"] = list(dict.fromkeys(claims[0]["signalRefs"] + ["preference_feedback"]))
    return claims[:6]


def _explicit_pool_request(text: str) -> bool:
    # Negated launch language is still ordinary conversation. Evaluate it
    # before positive keywords so “先不要开赛” cannot create a candidate pool
    # merely because the sentence contains “开赛”.
    if re.search(
        r"(?:不要|别|暂时不|先不|不想|无需).{0,10}(?:开赛|开始比赛|构建.{0,4}候选|生成.{0,4}候选)",
        text,
        re.I,
    ):
        return False
    return bool(re.search(
        r"(?:直接|现在|马上)?开赛|(?:生成|构建|准备).{0,8}候选|开始.{0,8}(?:世界杯|淘汰赛|歌曲比赛)|"
        r"(?:世界杯|淘汰赛|歌曲比赛).{0,8}(?:开始|开赛)|(?:16|32).{0,5}(?:首|歌).{0,8}(?:候选|世界杯|淘汰赛|比赛)",
        text, re.I,
    ))


def _explicit_tournament_request(text: str) -> bool:
    if re.search(
        r"(?:不想|不要|别|暂时不|先不|无需).{0,10}(?:歌曲)?(?:世界杯|淘汰赛|比赛|两两对决|二选一)",
        text,
        re.I,
    ):
        return False
    if _explicit_pool_request(text):
        return True
    return bool(re.search(
        r"(?:想|要|玩|来|办|做|开启|进入|试试).{0,12}(?:歌曲)?(?:世界杯|淘汰赛|比赛|两两对决|二选一)|"
        r"(?:歌曲)?(?:世界杯|淘汰赛|比赛|两两对决|二选一).{0,12}(?:想玩|怎么玩|来一场|办一场|做一场|试试)",
        text, re.I,
    ))


def _candidate_pool_card(result: CandidatePoolResult, pool_state: dict, preference_text: str, pool_size: int | None = None) -> tuple[ConversationCardIntent, str]:
    size = pool_size or result.size
    if result.status == "needs_clarification":
        return ConversationCardIntent(
            message_type="CLARIFICATION_CARD", card_type="ARTIST_IDENTITY",
            payload={"clarifications": result.clarifications, "preferenceText": preference_text, "poolSize": size},
        ), "开始构建前发现了艺人身份歧义。请先确认卡片中的艺人，避免把错误作品放进这场比赛。"
    if result.status != "ready_for_confirmation":
        warning = next((item.get("message") for item in result.warnings if item.get("message")), "可核验歌曲暂时不足以开赛。")
        return ConversationCardIntent(
            message_type="RECOMMENDATION_CARD", card_type="CANDIDATE_POOL_INSUFFICIENT",
            payload={"preferenceText": preference_text, "summary": result.candidate_summary, "warnings": result.warnings},
        ), f"我已完成在线发现与身份核验，但{warning} 你可以补充艺人、语言、年代或场景后继续。"
    recordings = {str(item.get("id")): item for item in pool_state.get("recordings", [])}
    items = []
    for item in result.items:
        recording = recordings.get(str(item.recording_id), {})
        items.append({
            "recordingId": str(item.recording_id), "title": recording.get("title", "未命名歌曲"),
            "artistName": recording.get("artistName", "未知艺人"), "coverUrl": recording.get("coverUrl"),
            "reason": item.reason,
        })
    return ConversationCardIntent(
        message_type="CANDIDATE_POOL_CARD", card_type="CANDIDATE_POOL",
        payload={"size": result.size, "status": result.status, "summary": result.candidate_summary,
                 "preferenceText": preference_text, "items": items, "reserveSize": result.reserve_size,
                 "terminationReason": result.termination_reason},
    ), f"候选池已完成：为 {result.size} 首赛事整理了 {len(items)} 首已核验歌曲（含 {result.reserve_size} 首候补）。你可以在卡片中移除不想要的作品，确认后再开赛。"
