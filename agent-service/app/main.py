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
        yield f"event: stage_started\ndata: {json.dumps({'requestId': str(request.request_id), 'stage': 'collect_recordings', 'message': '正在整理可用曲目'}, ensure_ascii=False)}\n\n"
        try:
            # A ReAct turn traverses both Supervisor and executor nodes.  The
            # framework default (25) can interrupt a legitimate guarded run before
            # the graph's own tool/deadline/stagnation guards have a chance to stop.
            state = await graph.ainvoke({"request": request}, {"recursion_limit": 128})
            result = state["result"]
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
        yield f"event: stage_started\ndata: {json.dumps({'requestId': str(request.request_id), 'stage': 'read_tournament_facts', 'message': '正在读取本场赛事选择记录'}, ensure_ascii=False)}\n\n"
        try:
            state = await report_graph.ainvoke({"request": request}, {"recursion_limit": 80})
            logger.info(
                "report ReAct completed request_id=%s actions=%s",
                request.request_id,
                [item["action"] for item in state.get("action_history", [])],
            )
            if state.get("error_code"):
                yield f"event: error\ndata: {json.dumps({'requestId': str(request.request_id), 'code': state['error_code'], 'message': '报告审查未通过，请稍后重试'}, ensure_ascii=False)}\n\n"
                return
            yield f"event: stage_started\ndata: {json.dumps({'requestId': str(request.request_id), 'stage': 'generate_report', 'message': '正在分析你的音乐选择'}, ensure_ascii=False)}\n\n"
            yield f"event: result\ndata: {state['result'].model_dump_json(by_alias=True)}\n\n"
        except Exception:
            logger.exception("tournament report workflow failed", extra={"request_id": str(request.request_id)})
            yield f"event: error\ndata: {json.dumps({'requestId': str(request.request_id), 'code': 'REPORT_WORKFLOW_FAILED', 'message': '暂时无法生成报告，请稍后重试'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
