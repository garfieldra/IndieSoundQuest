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


class ConversationState(TypedDict, total=False):
    request: ConversationAgentRequest
    decision: ConversationDecision
    action_history: list[dict]
    observations: list[dict]
    web_sources: list[dict]
    knowledge: list[dict]
    iteration: int
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
        if self.model is None:
            return _fallback_decision(state)
        observation_summary = {
            "actions": [item["action"] for item in state.get("action_history", [])],
            "webSourceCount": len(state.get("web_sources", [])),
            "knowledgeCount": len(state.get("knowledge", [])),
            "iteration": state.get("iteration", 0),
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
            return ConversationDecision(action="recommend_music", public_summary="提取并核验具体歌曲与艺人推荐")
        if decision.action == "search_web" and "search_web" in previous:
            if _recommendation_intent(request):
                return ConversationDecision(action="recommend_music", public_summary="提取并核验具体歌曲与艺人推荐")
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
            return {
                "action_history": [{"action": "understand_preference", "summary": "结合本轮消息与会话上下文理解需求"}],
                "observations": [], "web_sources": [], "knowledge": [], "iteration": 0,
            }

        async def supervisor(state: ConversationState):
            decision = await self.decide(state)
            if state.get("iteration", 0) >= 5:
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
                sources = await self.web.search(query, "conversation_research")
                return {
                    "web_sources": sources,
                    "observations": state["observations"] + [{"action": action, "status": "success", "outputCount": len(sources)}],
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
                card = ConversationCardIntent(
                    message_type="CLARIFICATION_CARD",
                    card_type="GENERAL_CLARIFICATION",
                    payload={
                        "question": question,
                        "reason": decision.clarification_reason or "这个信息会实质影响下一步的结果。",
                        "options": decision.clarification_options,
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
            "userMessage": f"针对上一轮必要澄清，用户回答：{request.answer}",
            "forcedAction": None,
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
        result = await self.recommend_music(request, sources)
        return {
            "result": result,
            "observations": state["observations"] + [{
                "action": "recommend_music", "status": "success",
                "sourceCount": len(sources),
                "verifiedCount": int(result.trace_summary.get("verifiedSongCount", 0)),
            }],
        }

    async def recommend_music(self, request: ConversationAgentRequest, sources: list[dict]) -> ConversationAgentResult:
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
        } for item in sources[:10]]
        prompt = f"""你是 IndieSoundQuest 的普通音乐推荐复合工具，不输出思维链。
用户请求：{request.user_message}
会话摘要：{request.summary}
最近对话：{request.recent_messages}
上一轮推荐上下文：{json.dumps(_recent_card_context(request), ensure_ascii=False)}
公开资料：{json.dumps(compact, ensure_ascii=False)}
请先给出 8–10 首可供身份核验的歌曲候选和 3–4 位艺人候选；下游会从中筛选 5–7 首已核验歌曲和 2–3 位艺人展示。资料确实不足时可以更少，不能硬凑。
把本轮话语视为对上一轮推荐的增量约束：正确理解“更冷一点”“换一批”“不要这些艺人”“保留某首”等指代。歌曲名、艺人名及 sourceUrl 必须来自给定公开资料或上一轮已持久化推荐，禁止凭记忆编造；sourceUrl 必须逐字复制。
reason 要具体说明它与用户偏好的声音、情绪、文本、场景或创作脉络关系。summary 概括推荐逻辑并明确证据边界。
返回 MusicRecommendationDraft。"""
        draft = None
        # Provider retries do not cover malformed tool arguments. Give the
        # model one bounded schema-repair attempt before degrading to a source
        # card, so a transient formatting miss does not erase recommendations.
        structured_model = self.model.with_structured_output(MusicRecommendationDraft, method="function_calling")
        for attempt in range(2):
            try:
                repair = "" if attempt == 0 else "\n上一次结构化结果无效。请缩短 reason，并严格满足字段类型、列表上限和 sourceUrl 原样复制要求。"
                draft = await structured_model.ainvoke(prompt + repair)
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
        verified_songs = list({item["recordingId"]: item for item in verified_songs}.values())
        payload = {
            "title": "这轮音乐探索的推荐方向", "summary": draft.summary,
            "songs": verified_songs[:7], "artists": artist_items[:3],
            "sources": _public_source_items(sources, limit=5),
        }
        if prior_cards and _recommendation_followup(request):
            payload.update({
                "contextMode": "REFINED",
                "basedOnCardId": prior_cards[-1]["messageId"],
                "appliedInstruction": request.user_message[:240],
            })
        return ConversationAgentResult(
            text=draft.summary,
            card_intent=ConversationCardIntent(message_type="RECOMMENDATION_CARD", card_type="MUSIC_RECOMMENDATIONS", payload=payload),
            action="recommend_music",
            trace_summary={
                "webSourceCount": len(sources), "proposedSongCount": len(songs),
                "verifiedSongCount": len(verified_songs), "artistCount": len(artist_items),
            },
        )


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
