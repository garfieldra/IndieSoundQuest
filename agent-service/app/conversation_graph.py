from __future__ import annotations

import json
import re
from typing import Literal, TypedDict
from uuid import uuid4

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from .schemas import CandidatePoolRequest, CandidatePoolResult, ConfirmedArtist, ConversationAgentRequest, ConversationAgentResult, ConversationCardIntent, ConversationResumeRequest
from .settings import settings
from .tools import KnowledgeSearchTool, WebSearchTool
from .registry import SkillRegistry, ToolRegistry, skill_registry, tool_registry


class ConversationDecision(BaseModel):
    action: Literal["search_web", "search_knowledge", "clarify", "propose_tournament", "build_candidate_pool", "generate_exploration_report", "respond"]
    public_summary: str = Field(min_length=4, max_length=100)
    query: str | None = Field(default=None, max_length=300)


class ConversationState(TypedDict, total=False):
    request: ConversationAgentRequest
    decision: ConversationDecision
    action_history: list[dict]
    observations: list[dict]
    web_sources: list[dict]
    knowledge: list[dict]
    iteration: int
    result: ConversationAgentResult


class ConversationReActRuntime:
    """Unified ReAct music exploration Agent; candidate/report work is delegated as composite tools."""

    def __init__(self, web: WebSearchTool, knowledge: KnowledgeSearchTool, tools: ToolRegistry = tool_registry, skills: SkillRegistry = skill_registry, candidate_graph=None):
        self.web = web
        self.knowledge = knowledge
        self.tools = tools
        self.skills = skills
        self.candidate_graph = candidate_graph
        self.model = None if not settings.deepseek_api_key else ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0.35,
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
- search_web：问题需要当前公开音乐资料或外部事实；
- search_knowledge：本地主题卡可以补充歌词主题或文化语境，但它不是歌曲发现主来源；
- clarify：偏好过于含糊或存在必须由用户选择的信息；
- propose_tournament：用户表达了偏好，但还没有明确要求立即构建候选；展示世界杯启动卡；
- build_candidate_pool：用户明确要求开始/直接开赛/生成 16 或 32 首世界杯候选时，调用候选池复合 Tool；
- generate_exploration_report：用户明确要求根据当前对话生成偏好/探索报告时，调用报告复合 Tool；
- respond：普通音乐问答，或已有资料足以回答。
不得声称已经搜索、创建候选池或赛事，除非观察中确有对应结果。候选池复合 Tool 内部会自主决定在线搜索、MusicBrainz 核验和补充探索；你不得编排它的内部步骤。艺人身份歧义由候选池 Tool 在真正生成时核验。
会话摘要：{request.summary}
已确认长期偏好：{request.confirmed_memories}
近期推荐反馈：{request.recent_feedback}
最近消息：{request.recent_messages}
本轮用户消息：{request.user_message}
当前可观察状态：{json.dumps(observation_summary, ensure_ascii=False)}
可用 Tool 摘要：{json.dumps(self.tools.summaries(), ensure_ascii=False)}
可按需加载的 Skill：{json.dumps(self.skills.summaries(), ensure_ascii=False)}
返回 ConversationDecision。publicSummary 是可展示给用户的安全动作摘要。"""
        try:
            decision = await self.model.with_structured_output(ConversationDecision).ainvoke(prompt)
        except Exception:
            return _fallback_decision(state)
        # Explicitly starting a World Cup is a user command, not a soft product
        # suggestion.  Preserve the ReAct decision for ordinary conversation,
        # but never downgrade this confirmed intent back to a launch prompt.
        if _explicit_pool_request(request.user_message) and decision.action == "propose_tournament":
            return ConversationDecision(action="build_candidate_pool", public_summary="开始构建并核验歌曲世界杯候选池")
        previous = [item["action"] for item in state.get("action_history", [])]
        if decision.action == "search_web" and "search_web" in previous:
            return ConversationDecision(action="respond", public_summary="公开资料已经足够，开始整理回答")
        if decision.action == "search_knowledge" and "search_knowledge" in previous:
            return ConversationDecision(action="respond", public_summary="补充资料已经足够，开始整理回答")
        return decision

    async def answer(self, state: ConversationState, action: str) -> str:
        request = state["request"]
        if action == "clarify":
            return "我还需要一个更明确的起点：你可以告诉我一两位喜欢的艺人、最近反复听的歌，或者想要的情绪、场景与语言范围。"
        if action == "propose_tournament":
            return "这个方向已经足够形成一场歌曲世界杯。我会在你进入候选确认后，通过在线搜索发现歌曲，并用 MusicBrainz 核验身份；候选池由你确认后才会正式开赛。"
        if action == "build_candidate_pool":
            return "我会直接开始构建候选池：先在线发现相关作品，再以 MusicBrainz 核验歌曲身份；候选池完成后仍由你确认才会开赛。"
        if self.model is None:
            return "我已经结合这轮对话整理了你的音乐方向。你可以继续追问；如果想把偏好变成更具体的选择，也可以进入歌曲世界杯。"
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
回答控制在 500 字内。若用户已经明确表达音乐偏好，可以自然提示其进入歌曲世界杯，但不要声称已经创建。"""
        try:
            response = await self.model.ainvoke(prompt)
            return str(response.content).strip()[:2000]
        except Exception:
            return "这次音乐问题已经有了明确方向。你可以继续补充偏好，或者将它整理成一场歌曲世界杯。"

    async def exploration_report(self, state: ConversationState) -> dict:
        request = state["request"]
        fallback = {
            "summary": "这份探索报告基于当前对话整理；继续补充喜欢或跳过的作品，结论会更具体。",
            "dimensions": [{"label": "探索起点", "summary": request.user_message[:120]}],
            "directions": ["从当前提到的艺人或场景继续展开", "也可以开启歌曲世界杯，用连续选择强化偏好信号"],
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


def _fallback_decision(state: ConversationState) -> ConversationDecision:
    text = state["request"].user_message.strip()
    previous = {item["action"] for item in state.get("action_history", [])}
    report_request = bool(re.search(r"(?:生成|给我|看看).{0,8}(?:报告|偏好分析|探索报告)|根据.{0,8}(?:对话|聊天).{0,8}(?:报告|分析)", text, re.I))
    if report_request and ("search_web" in previous or "search_knowledge" in previous):
        return ConversationDecision(action="generate_exploration_report", public_summary="整理当前对话中的偏好线索并生成探索报告")
    if "search_web" in previous or "search_knowledge" in previous:
        return ConversationDecision(action="respond", public_summary="资料已经准备好，开始整理回答")
    preference_signal = bool(re.search(r"喜欢|常听|反复听|世界杯|偏好|想听|歌手|乐队|专辑|华语|摇滚|民谣|爵士|说唱|电子|流行|独立", text, re.I))
    immediate_pool = _explicit_pool_request(text)
    question_signal = bool(re.search(r"为什么|是什么|介绍|背景|历史|最近|最新|谁是|有哪些", text))
    if immediate_pool:
        return ConversationDecision(action="build_candidate_pool", public_summary="开始构建并核验歌曲世界杯候选池")
    if report_request:
        return ConversationDecision(action="generate_exploration_report", public_summary="整理当前对话中的偏好线索并生成探索报告")
    if preference_signal and not question_signal:
        return ConversationDecision(action="propose_tournament", public_summary="偏好方向已经明确，准备歌曲世界杯入口")
    if question_signal:
        return ConversationDecision(action="search_web", public_summary="需要核对公开音乐资料", query=text)
    if len(text) < 5:
        return ConversationDecision(action="clarify", public_summary="还需要一个更明确的音乐起点")
    return ConversationDecision(action="respond", public_summary="结合当前对话整理音乐回应")


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
    return bool(re.search(r"直接开赛|现在开赛|生成.{0,8}候选|构建.{0,8}候选|开始.{0,8}世界杯|(?:16|32).{0,5}(?:首|歌)", text, re.I))


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
