from __future__ import annotations

import json
import re
from typing import Literal, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from .schemas import ConversationAgentRequest, ConversationAgentResult, ConversationCardIntent
from .settings import settings
from .tools import KnowledgeSearchTool, WebSearchTool


class ConversationDecision(BaseModel):
    action: Literal["search_web", "search_knowledge", "clarify", "propose_tournament", "respond"]
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
    """Technical conversation router; the two business agents remain candidate and report agents."""

    def __init__(self, web: WebSearchTool, knowledge: KnowledgeSearchTool):
        self.web = web
        self.knowledge = knowledge
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
        if self.model is None:
            return _fallback_decision(state)
        observation_summary = {
            "actions": [item["action"] for item in state.get("action_history", [])],
            "webSourceCount": len(state.get("web_sources", [])),
            "knowledgeCount": len(state.get("knowledge", [])),
            "iteration": state.get("iteration", 0),
        }
        prompt = f"""你是 IndieSoundQuest 对话运行时的 ReAct 决策器，不输出思维链。
产品核心是：多轮理解偏好 -> 候选池确认 -> 歌曲世界杯 -> 赛后报告 -> 继续对话。
你只能选择一个动作：
- search_web：问题需要当前公开音乐资料或外部事实；
- search_knowledge：本地主题卡可以补充歌词主题或文化语境，但它不是歌曲发现主来源；
- clarify：偏好过于含糊或存在必须由用户选择的信息；
- propose_tournament：用户已经表达了足以用于候选搜索的音乐偏好，应展示世界杯启动卡；
- respond：普通音乐问答，或已有资料足以回答。
不得声称已经搜索、创建候选池或赛事，除非观察中确有对应结果。艺人身份歧义由候选池 Agent 在真正生成时核验。
会话摘要：{request.summary}
已确认长期偏好：{request.confirmed_memories}
最近消息：{request.recent_messages}
本轮用户消息：{request.user_message}
当前可观察状态：{json.dumps(observation_summary, ensure_ascii=False)}
返回 ConversationDecision。publicSummary 是可展示给用户的安全动作摘要。"""
        try:
            decision = await self.model.with_structured_output(ConversationDecision).ainvoke(prompt)
        except Exception:
            return _fallback_decision(state)
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
        if self.model is None:
            return "我已经结合这轮对话整理了你的音乐方向。你可以继续追问；如果想把偏好变成更具体的选择，也可以进入歌曲世界杯。"
        sources = [{"title": item.get("sourceTitle"), "summary": item.get("summary"), "url": item.get("sourceUrl")} for item in state.get("web_sources", [])[:5]]
        knowledge = state.get("knowledge", [])[:4]
        prompt = f"""你是 IndieSoundQuest 的音乐探索 Agent。用中文自然、克制地回答，不输出思维链。
不要虚构歌曲、艺人、歌词、链接或工具结果。外部资料为空时，不得声称已经查询。
用户问题：{request.user_message}
会话摘要：{request.summary}
最近对话：{request.recent_messages}
公开资料：{json.dumps(sources, ensure_ascii=False)}
本地补充主题卡：{json.dumps(knowledge, ensure_ascii=False)}
回答控制在 500 字内。若用户已经明确表达音乐偏好，可以自然提示其进入歌曲世界杯，但不要声称已经创建。"""
        try:
            response = await self.model.ainvoke(prompt)
            return str(response.content).strip()[:2000]
        except Exception:
            return "这次音乐问题已经有了明确方向。你可以继续补充偏好，或者将它整理成一场歌曲世界杯。"

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


def _fallback_decision(state: ConversationState) -> ConversationDecision:
    text = state["request"].user_message.strip()
    previous = {item["action"] for item in state.get("action_history", [])}
    if "search_web" in previous or "search_knowledge" in previous:
        return ConversationDecision(action="respond", public_summary="资料已经准备好，开始整理回答")
    preference_signal = bool(re.search(r"喜欢|常听|反复听|世界杯|偏好|想听|歌手|乐队|专辑|华语|摇滚|民谣|爵士|说唱|电子|流行|独立", text, re.I))
    question_signal = bool(re.search(r"为什么|是什么|介绍|背景|历史|最近|最新|谁是|有哪些", text))
    if preference_signal and not question_signal:
        return ConversationDecision(action="propose_tournament", public_summary="偏好方向已经明确，准备歌曲世界杯入口")
    if question_signal:
        return ConversationDecision(action="search_web", public_summary="需要核对公开音乐资料", query=text)
    if len(text) < 5:
        return ConversationDecision(action="clarify", public_summary="还需要一个更明确的音乐起点")
    return ConversationDecision(action="respond", public_summary="结合当前对话整理音乐回应")
