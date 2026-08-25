import json
import logging
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from .graph import build_candidate_pool_graph
from .conversation_graph import ConversationReActRuntime
from .report_graph import build_report_graph
from .report_llm import ReportGenerator
from .report_schemas import TournamentReportRequest
from .llm import DeepSeekCandidateSelector
from .schemas import CandidatePoolRequest, ConversationAgentRequest
from .settings import settings
from .tools import KnowledgeSearchTool, MusicCatalogTool, TournamentFactsTool, WebSearchTool

app = FastAPI(title="IndieSoundQuest Agent Service", version="0.1.0")
# Reuse Uvicorn's configured handler so structured workflow summaries are emitted
# reliably in containers without adding a second global logging configuration.
logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)
web_search = WebSearchTool(tavily_api_key=settings.tavily_api_key, bocha_api_key=settings.bocha_api_key)
graph = build_candidate_pool_graph(MusicCatalogTool(settings.java_internal_base_url, settings.agent_internal_service_token), web_search, KnowledgeSearchTool(settings.milvus_uri, settings.embedding_model, settings.knowledge_collection), DeepSeekCandidateSelector())
report_graph = build_report_graph(TournamentFactsTool(settings.java_internal_base_url, settings.agent_internal_service_token), ReportGenerator(), web_search, KnowledgeSearchTool(settings.milvus_uri, settings.embedding_model, settings.knowledge_collection))
conversation_runtime = ConversationReActRuntime(web_search, KnowledgeSearchTool(settings.milvus_uri, settings.embedding_model, settings.knowledge_collection))

_ACTION_PROGRESS = {
    "understand_preference": ("understand_preference", "正在理解你的音乐偏好"),
    "resolve_named_entities": ("resolve_artist", "正在核验你提到的艺人"),
    "search_web": ("discover_web", "正在从公开音乐资料中寻找线索"),
    "resolve_musicbrainz": ("verify_musicbrainz", "正在通过 MusicBrainz 核验歌曲身份"),
    "search_catalog": ("search_catalog", "正在整理已核验的本地目录"),
    "expand_artist_catalog": ("expand_artist_catalog", "正在批量核验已提及艺人的作品"),
    "search_knowledge": ("knowledge_context", "正在补充歌曲主题与文化语境"),
    "analyze_tournament": ("analyze_matches", "正在归纳本场的关键选择轨迹"),
    "draft_report": ("draft_report", "正在生成你的音乐偏好报告"),
    "draft_response": ("draft_response", "正在整理这次音乐探索的回应"),
    "clarify": ("clarify", "正在确认这次探索还需要哪些信息"),
    "propose_tournament": ("propose_tournament", "正在准备歌曲世界杯入口"),
    "respond": ("draft_response", "正在整理这次音乐探索的回应"),
    "critique_report": ("review_report", "正在核验报告事实与推荐来源"),
    "rerank_candidates": ("organize_candidates", "正在并行重排候选，并生成入选理由"),
}

def _progress(request_id, action: str, elapsed_ms: int, metrics: dict | None = None) -> str:
    phase, message = _ACTION_PROGRESS.get(action, ("working", "正在继续整理本次音乐探索"))
    payload = {"runId": str(request_id), "phase": phase, "status": "started", "message": message, "elapsedMs": elapsed_ms, "metrics": metrics or {}}
    return f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

def _plan_event(request_id, state: dict, workflow: str) -> str:
    """Public plan projection: action facts only, never model reasoning or prompts."""
    action = state.get("decision").action if state.get("decision") else None
    history = [item.get("action") for item in state.get("action_history", [])]
    count = len(state.get("recordings", []))
    def status(key: str, actions: set[str]) -> str:
        if action in actions: return "running"
        if any(item in actions for item in history): return "completed"
        return "pending"
    if workflow == "conversation":
        items = [
            {"id":"understand","title":"理解这次音乐问题","status":status("understand", {"understand_preference"}),"detail":"结合本次提问与已有对话上下文"},
            {"id":"research","title":"按需查找音乐资料","status":status("research", {"search_web", "search_knowledge"}),"detail":"仅在回答或探索确有需要时调用工具"},
            {"id":"decide","title":"决定下一步探索方式","status":status("decide", {"clarify", "propose_tournament", "respond"}),"detail":"继续对话、澄清方向或进入歌曲世界杯"},
        ]
        goal, summary = "推进这次音乐探索对话", "Agent 正在根据对话状态自主决定下一步。"
    elif workflow == "candidate":
        target = state.get("request").size * 2 if state.get("request") else 0
        items = [
            {"id":"understand","title":"理解本次音乐偏好","status":status("understand", {"understand_preference", "resolve_named_entities"}),"detail":"已识别输入中的音乐方向"},
            {"id":"artist-catalog","title":"核验明确艺人的作品","status":status("artist", {"expand_artist_catalog", "search_catalog"}),"detail":f"已获得 {count} 首可核验歌曲"},
            {"id":"adjacent","title":"探索相近音乐方向","status":status("adjacent", {"search_web", "search_knowledge"}),"detail":"从公开音乐资料补充待核验线索"},
            {"id":"verify","title":"核验新发现歌曲","status":status("verify", {"resolve_musicbrainz"}),"detail":"将外部线索转为规范歌曲身份"},
            {"id":"review","title":"语义重排并检查候补数量","status":status("review", {"rerank_candidates", "submit_candidates"}),"detail":"并行生成入选理由，确保赛事与候补队列都满足数量要求"},
        ]
        goal, summary = f"为 {target // 2} 首赛事准备 {target} 首可核验候选", f"已核验 {count} / {target} 首；当前正由 Agent 决定下一步。"
    else:
        items = [
            {"id":"facts","title":"分析本场关键选择","status":status("facts", {"analyze_tournament"}),"detail":"归纳胜出、淘汰与关键对局"},
            {"id":"research","title":"补充探索资料","status":status("research", {"search_web", "search_knowledge"}),"detail":"按需查找公开音乐资料"},
            {"id":"draft","title":"生成偏好报告与推荐","status":status("draft", {"draft_report"}),"detail":"基于本场赛事事实组织结论"},
            {"id":"review","title":"审查推荐与事实边界","status":status("review", {"critique_report", "submit_report"}),"detail":"检查来源、对局依据与表达边界"},
        ]
        goal, summary = "基于本场歌曲世界杯生成偏好报告", "正在根据已完成的对局事实推进报告。"
    payload = {"runId": str(request_id), "revision": len(history), "goal": goal, "summary": summary, "items": items}
    return f"event: plan_updated\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

async def verify_caller(authorization: str = Header(default="")):
    if authorization != f"Bearer {settings.agent_internal_service_token}": raise HTTPException(401, "invalid internal credential")

@app.get("/health/live")
async def live(): return {"status":"UP"}

@app.get("/health/ready")
async def ready(): return {"status":"UP", "catalog":"configured", "modelProvider": settings.llm_provider, "webSearch": bool(settings.tavily_api_key)}

@app.post("/internal/v1/workflows/conversation:stream", dependencies=[Depends(verify_caller)])
async def conversation(request: ConversationAgentRequest, x_request_id: str = Header()):
    if str(request.request_id) != x_request_id: raise HTTPException(400, "X-Request-Id must match requestId")
    async def events():
        yield _progress(request.request_id, "understand_preference", 0)
        try:
            started = __import__("time").monotonic(); last_action = None; last_plan = None; result = None; state = {}
            config = {"configurable": {"thread_id": str(request.agent_run_id)}, "recursion_limit": 24}
            async for state in conversation_runtime.graph.astream({"request": request}, config, stream_mode="values"):
                action = state.get("decision").action if state.get("decision") else None
                if action and action != last_action:
                    last_action = action
                    yield _progress(request.request_id, action, int((__import__("time").monotonic() - started) * 1000))
                if state.get("action_history"):
                    plan = _plan_event(request.request_id, state, "conversation")
                    if plan != last_plan:
                        last_plan = plan; yield plan
                if state.get("result"): result = state["result"]
            if result is None: raise RuntimeError("conversation result missing")
            logger.info("conversation ReAct completed request_id=%s trace=%s", request.request_id, result.trace_summary)
            yield f"event: result\ndata: {result.model_dump_json(by_alias=True)}\n\n"
        except Exception:
            logger.exception("conversation workflow failed")
            yield f"event: error\ndata: {json.dumps({'code':'CONVERSATION_UNAVAILABLE','message':'这次音乐对话暂时无法完成，请稍后重试'},ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")

@app.post("/internal/v1/workflows/candidate-pool:stream", dependencies=[Depends(verify_caller)])
async def candidate_pool(request: CandidatePoolRequest, x_request_id: str = Header()):
    if str(request.request_id) != x_request_id: raise HTTPException(400, "X-Request-Id must match requestId")
    async def events():
        yield _progress(request.request_id, "understand_preference", 0)
        try:
            # A ReAct turn traverses both Supervisor and executor nodes.  The
            # framework default (25) can interrupt a legitimate guarded run before
            # the graph's own tool/deadline/stagnation guards have a chance to stop.
            started = __import__("time").monotonic()
            last_action = None; last_plan = None
            result = None
            async for state in graph.astream({"request": request}, {"recursion_limit": 128}, stream_mode="values"):
                action = state.get("decision").action if state.get("decision") else None
                if action and action != last_action:
                    last_action = action
                    yield _progress(request.request_id, action, int((__import__("time").monotonic() - started) * 1000))
                plan = _plan_event(request.request_id, state, "candidate")
                if plan != last_plan:
                    last_plan = plan; yield plan
                if state.get("result"):
                    result = state["result"]
            if result is None: raise RuntimeError("candidate result missing")
            logger.info(
                "candidate ReAct completed request_id=%s intent_mode=%s termination_reason=%s trace=%s observations=%s",
                request.request_id,
                state.get("intent_policy").intent_mode if state.get("intent_policy") else None,
                result.termination_reason,
                result.trace_summary,
                [{key: value for key, value in item.items() if key != "error"} for item in state.get("observations", [])],
            )
            yield f"event: result\ndata: {result.model_dump_json(by_alias=True)}\n\n"
        except Exception:
            logger.exception("candidate pool workflow failed", extra={"request_id": str(request.request_id)})
            yield f"event: error\ndata: {json.dumps({'requestId': str(request.request_id), 'code': 'CATALOG_UNAVAILABLE', 'message': '暂时无法整理候选曲目，请稍后重试'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/internal/v1/workflows/tournament-report:stream", dependencies=[Depends(verify_caller)])
async def tournament_report(request: TournamentReportRequest, x_request_id: str = Header()):
    if str(request.request_id) != x_request_id:
        raise HTTPException(400, "X-Request-Id must match requestId")

    async def events():
        yield _progress(request.request_id, "analyze_tournament", 0)
        try:
            started = __import__("time").monotonic(); last_action = None; last_plan = None; result = None; state = {}
            async for state in report_graph.astream({"request": request}, {"recursion_limit": 80}, stream_mode="values"):
                action = state.get("decision").action if state.get("decision") else None
                if action and action != last_action:
                    last_action = action
                    yield _progress(request.request_id, action, int((__import__("time").monotonic() - started) * 1000))
                plan = _plan_event(request.request_id, state, "report")
                if plan != last_plan:
                    last_plan = plan; yield plan
                if state.get("result"): result = state["result"]
            logger.info(
                "report ReAct completed request_id=%s actions=%s",
                request.request_id,
                [item["action"] for item in state.get("action_history", [])],
            )
            if state.get("error_code"):
                yield f"event: error\ndata: {json.dumps({'requestId': str(request.request_id), 'code': state['error_code'], 'message': '报告审查未通过，请稍后重试'}, ensure_ascii=False)}\n\n"
                return
            if result is None: raise RuntimeError("report result missing")
            yield f"event: result\ndata: {result.model_dump_json(by_alias=True)}\n\n"
        except Exception:
            logger.exception("tournament report workflow failed", extra={"request_id": str(request.request_id)})
            yield f"event: error\ndata: {json.dumps({'requestId': str(request.request_id), 'code': 'REPORT_WORKFLOW_FAILED', 'message': '暂时无法生成报告，请稍后重试'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
