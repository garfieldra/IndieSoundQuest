import json
import logging
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from .graph import build_candidate_pool_graph
from .report_graph import build_report_graph
from .report_llm import ReportGenerator
from .report_schemas import TournamentReportRequest
from .llm import DeepSeekCandidateSelector
from .schemas import CandidatePoolRequest
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
    "critique_report": ("review_report", "正在核验报告事实与推荐来源"),
    "rerank_candidates": ("organize_candidates", "正在整理候选歌曲与候补队列"),
}

def _progress(request_id, action: str, elapsed_ms: int, metrics: dict | None = None) -> str:
    phase, message = _ACTION_PROGRESS.get(action, ("working", "正在继续整理本次音乐探索"))
    payload = {"runId": str(request_id), "phase": phase, "status": "started", "message": message, "elapsedMs": elapsed_ms, "metrics": metrics or {}}
    return f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

async def verify_caller(authorization: str = Header(default="")):
    if authorization != f"Bearer {settings.agent_internal_service_token}": raise HTTPException(401, "invalid internal credential")

@app.get("/health/live")
async def live(): return {"status":"UP"}

@app.get("/health/ready")
async def ready(): return {"status":"UP", "catalog":"configured", "modelProvider": settings.llm_provider, "webSearch": bool(settings.tavily_api_key)}

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
            last_action = None
            result = None
            async for state in graph.astream({"request": request}, {"recursion_limit": 128}, stream_mode="values"):
                action = state.get("decision").action if state.get("decision") else None
                if action and action != last_action:
                    last_action = action
                    yield _progress(request.request_id, action, int((__import__("time").monotonic() - started) * 1000))
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
            started = __import__("time").monotonic(); last_action = None; result = None; state = {}
            async for state in report_graph.astream({"request": request}, {"recursion_limit": 80}, stream_mode="values"):
                action = state.get("decision").action if state.get("decision") else None
                if action and action != last_action:
                    last_action = action
                    yield _progress(request.request_id, action, int((__import__("time").monotonic() - started) * 1000))
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
