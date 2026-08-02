import json
import logging
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from .graph import build_candidate_pool_graph
from .llm import DeepSeekCandidateSelector
from .schemas import CandidatePoolRequest
from .settings import settings
from .tools import KnowledgeSearchTool, MusicCatalogTool, WebSearchTool

app = FastAPI(title="IndieSoundQuest Agent Service", version="0.1.0")
logger = logging.getLogger(__name__)
graph = build_candidate_pool_graph(MusicCatalogTool(settings.java_internal_base_url, settings.agent_internal_service_token), WebSearchTool(settings.tavily_api_key), KnowledgeSearchTool(settings.milvus_uri), DeepSeekCandidateSelector())

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
            result = (await graph.ainvoke({"request": request}))["result"]
            yield f"event: result\ndata: {result.model_dump_json(by_alias=True)}\n\n"
        except Exception:
            logger.exception("candidate pool workflow failed", extra={"request_id": str(request.request_id)})
            yield f"event: error\ndata: {json.dumps({'requestId': str(request.request_id), 'code': 'CATALOG_UNAVAILABLE', 'message': '暂时无法整理候选曲目，请稍后重试'}, ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")
