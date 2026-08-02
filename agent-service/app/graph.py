from typing import TypedDict
from uuid import UUID
from langgraph.graph import END, StateGraph
from .schemas import CandidateItem, CandidatePoolRequest, CandidatePoolResult
from .tools import KnowledgeSearchTool, MusicCatalogTool, WebSearchTool
from .llm import DeepSeekCandidateSelector


class State(TypedDict, total=False):
    request: CandidatePoolRequest
    recordings: list[dict]
    web_sources: list[dict]
    result: CandidatePoolResult


def build_candidate_pool_graph(catalog: MusicCatalogTool, web: WebSearchTool, knowledge: KnowledgeSearchTool, selector: DeepSeekCandidateSelector):
    async def collect(state: State):
        request = state["request"]
        return {"recordings": await catalog.search_recordings(request.preference_text, request.seed_artist_ids)}
    async def research(state: State):
        await knowledge.search_verified(state["request"].preference_text, [item["id"] for item in state["recordings"]])
        return {"web_sources": await web.search(state["request"].preference_text)}
    async def generate_candidates(state: State):
        request, sources = state["request"], state["web_sources"]
        excluded = set(request.exclude_recording_ids)
        available = [item for item in state["recordings"] if UUID(item["id"]) not in excluded]
        model_selection = await selector.select(request.preference_text, request.size, available)
        available_by_id = {UUID(item["id"]): item for item in available}
        if model_selection and len(model_selection.selected) == request.size and len({item.recording_id for item in model_selection.selected}) == request.size and all(item.recording_id in available_by_id for item in model_selection.selected):
            choices = [available_by_id[item.recording_id] | {"reason": item.reason} for item in model_selection.selected]
            summary = model_selection.candidate_summary
        else:
            choices, summary = available[:request.size], "这组候选基于你的偏好与可验证的音乐目录生成；确认后才会创建赛事。"
        if len(choices) != request.size:
            return {"result": CandidatePoolResult(request_id=request.request_id, status="insufficient_candidates", size=request.size, candidate_summary="可验证曲目不足，无法可靠组成这场赛事。", warnings=[{"code":"INSUFFICIENT_CANDIDATES","message":"请缩小范围或改用自选候选。"}])}
        return {"result": CandidatePoolResult(request_id=request.request_id, status="ready_for_confirmation", size=request.size, recording_ids=[UUID(item["id"]) for item in choices], items=[CandidateItem(recording_id=item["id"], reason=item.get("reason", "来自与你输入偏好匹配的规范音乐目录。"), evidence=sources[:1]) for item in choices], candidate_summary=summary, warnings=[] if sources else [{"code":"WEB_SEARCH_UNAVAILABLE","message":"未使用网页补充资料，理由仅基于目录数据。"}])}
    graph = StateGraph(State)
    graph.add_node("collect", collect); graph.add_node("research", research); graph.add_node("generate_candidates", generate_candidates)
    graph.set_entry_point("collect"); graph.add_edge("collect", "research"); graph.add_edge("research", "generate_candidates"); graph.add_edge("generate_candidates", END)
    return graph.compile()
