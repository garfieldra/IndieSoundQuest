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
from .tools import KnowledgeSearchTool, MusicCatalogTool, WebSearchTool
from .registry import SkillRegistry, ToolRegistry, skill_registry, tool_registry


class ConversationDecision(BaseModel):
    action: Literal["search_web", "search_knowledge", "recommend_music", "clarify", "propose_tournament", "build_candidate_pool", "generate_exploration_report", "respond"]
    public_summary: str = Field(min_length=4, max_length=100)
    query: str | None = Field(default=None, max_length=300)
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

    def __init__(self, web: WebSearchTool, knowledge: KnowledgeSearchTool, tools: ToolRegistry = tool_registry, skills: SkillRegistry = skill_registry, candidate_graph=None, catalog: MusicCatalogTool | None = None):
        self.web = web
        self.knowledge = knowledge
        self.tools = tools
        self.skills = skills
        self.candidate_graph = candidate_graph
        self.catalog = catalog
        self.model = None if not settings.deepseek_api_key else ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0.35,
            extra_body={"thinking": {"type": "disabled"}},
            max_retries=1,
        )
        self.graph = self._build().compile(checkpointer=MemorySaver())

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
        }
        prompt = f"""你是 IndieSoundQuest 对话运行时的 ReAct 决策器，不输出思维链。
你是统一音乐探索 Agent。世界杯是可选 Skill，不是产品主流程；候选池构建、赛事分析和报告撰写是可调用的复合 Tool，而不是独立 Agent。
你只能选择一个动作：
- search_web：问题需要当前公开音乐资料、外部事实或具体推荐线索；用户要求推荐、相似艺人/歌曲或风格延展时，应先搜索再回答，除非当前观察中已有足够公开资料；
- search_knowledge：本地主题卡可以补充歌词主题或文化语境，但它不是歌曲发现主来源；
- recommend_music：已有足够公开资料时，提取具体歌曲/艺人推荐，并通过 MusicBrainz 核验可核验的歌曲；这是普通探索 Tool，与世界杯无关；
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
当前可观察状态：{json.dumps(observation_summary, ensure_ascii=False)}
可用 Tool 摘要：{json.dumps(self.tools.summaries(), ensure_ascii=False)}
可按需加载的 Skill：{json.dumps(self.skills.summaries(), ensure_ascii=False)}
返回 ConversationDecision。publicSummary 是可展示给用户的安全动作摘要。"""
        try:
            decision = await self.model.with_structured_output(ConversationDecision, method="function_calling").ainvoke(prompt)
        except Exception:
            return _fallback_decision(state)
        # Tournament UI is an explicit-intent capability, never an automatic
        # conversion of an ordinary preference statement. The model remains
        # free to choose research and answer tools, while this product boundary
        # prevents the optional Skill from taking over the conversation.
        if decision.action in {"propose_tournament", "build_candidate_pool"} and not _explicit_tournament_request(request.user_message):
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
        if decision.action == "search_web" and "search_web" in previous and state.get("search_attempts", 0) >= 3:
            if _recommendation_intent(request):
                return ConversationDecision(action="recommend_music", public_summary="研究预算已满足，提取并核验具体推荐")
            return ConversationDecision(action="respond", public_summary="公开资料已经足够，开始整理回答")
        if decision.action == "search_knowledge" and "search_knowledge" in previous:
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
        sources = [{"title": item.get("sourceTitle"), "summary": item.get("summary"), "url": item.get("sourceUrl")} for item in state.get("web_sources", [])[:5]]
        knowledge = state.get("knowledge", [])[:4]
        prompt = f"""你是 IndieSoundQuest 的音乐探索 Agent。用中文自然、克制地回答，不输出思维链。
不要虚构歌曲、艺人、歌词、链接或工具结果。外部资料为空时，不得声称已经查询。
用户问题：{request.user_message}
会话摘要：{request.summary}
最近对话：{request.recent_messages}
近期推荐反馈：{request.recent_feedback}
公开资料：{json.dumps(sources, ensure_ascii=False)}
本地补充主题卡：{json.dumps(knowledge, ensure_ascii=False)}
回答控制在 500 字内。普通对话只回答用户当前问题并给出自然的音乐探索方向；除非用户本轮明确要求世界杯、淘汰赛或两两对决，否则不得主动提及比赛、开赛或候选池。"""
        try:
            response = await self.model.ainvoke(prompt)
            return str(response.content).strip()[:2000]
        except Exception:
            return _fallback_answer(state)

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
            return {
                "action_history": history,
                "observations": [], "web_sources": [], "knowledge": [], "iteration": 0,
                "preference_profile": profile.model_dump(by_alias=True),
                "entity_resolutions": resolutions, "entity_ambiguities": ambiguities,
                "research_assessment": {}, "search_attempts": 0,
            }

        async def supervisor(state: ConversationState):
            decision = await self.decide(state)
            if state.get("iteration", 0) >= 7:
                decision = ConversationDecision(action="respond", public_summary="运行预算已满足，整理最终回应")
            return {
                "decision": decision,
                "iteration": state.get("iteration", 0) + 1,
                "action_history": state.get("action_history", []) + [{"action": decision.action, "summary": decision.public_summary}],
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
                queries = _recommendation_search_queries(
                    request, query, missing or confirmed_focus or None, state.get("preference_profile", {})
                )
                if len(queries) > 1 and hasattr(self.web, "search_many"):
                    new_sources = await self.web.search_many(queries)
                else:
                    new_sources = await self.web.search(query, "conversation_research")
                sources = _merge_web_sources(state.get("web_sources", []), new_sources, prefer_incoming=state.get("search_attempts", 0) > 0)
                research = _assess_research(request, state.get("preference_profile", {}), sources)
                return {
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
            if action == "search_knowledge":
                cards = await self.knowledge.search_verified(state["decision"].query or request.user_message, [])
                return {
                    "knowledge": cards,
                    "observations": state["observations"] + [{"action": action, "status": "success", "outputCount": len(cards)}],
                }
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
                        trace_summary={"actions": [item["action"] for item in state["action_history"]], "webSourceCount": len(state.get("web_sources", [])), "knowledgeCount": len(state.get("knowledge", []))},
                    ),
                    "observations": state["observations"] + [{"action": action, "status": "success"}],
                }
            if action == "recommend_music":
                return await self._recommend_music_result(state)
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


def _fallback_decision(state: ConversationState) -> ConversationDecision:
    text = state["request"].user_message.strip()
    request = state["request"]
    previous = {item["action"] for item in state.get("action_history", [])}
    report_request = bool(re.search(r"(?:生成|给我|看看).{0,8}(?:报告|偏好分析|探索报告)|根据.{0,8}(?:对话|聊天).{0,8}(?:报告|分析)", text, re.I))
    recommendation_signal = _recommendation_intent(request)
    if report_request and ("search_web" in previous or "search_knowledge" in previous):
        return ConversationDecision(action="generate_exploration_report", public_summary="整理当前对话中的偏好线索并生成探索报告")
    if "search_web" in previous or "search_knowledge" in previous:
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
    return context


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
