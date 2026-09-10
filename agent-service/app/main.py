import json
import logging
import os
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from .graph import build_candidate_pool_graph
from .conversation_graph import ConversationReActRuntime
from .report_graph import build_report_graph
from .report_llm import ReportGenerator
from .report_schemas import TournamentReportRequest
from .llm import DeepSeekCandidateSelector
from .schemas import CandidatePoolRequest, ConversationAgentRequest, ConversationResumeRequest, MemoryCompressionRequest, MemoryCompressionResponse
from .settings import settings
from .tools import DomesticContentResearchTool, KnowledgeSearchTool, MusicCatalogTool, SpotifyCatalogTool, TournamentFactsTool, WebSearchTool
from .registry import skill_registry, tool_registry

app = FastAPI(title="IndieSoundQuest Agent Service", version="0.1.0")
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    provider = TracerProvider(resource=Resource.create({"service.name": "indiesoundquest-agent"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
FastAPIInstrumentor.instrument_app(app)
# Reuse Uvicorn's configured handler so structured workflow summaries are emitted
# reliably in containers without adding a second global logging configuration.
logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)
web_search = WebSearchTool(tavily_api_key=settings.tavily_api_key, bocha_api_key=settings.bocha_api_key)
music_catalog = MusicCatalogTool(settings.java_internal_base_url, settings.agent_internal_service_token)
domestic_research = DomesticContentResearchTool(
    zhihu_enabled=settings.zhihu_research_enabled,
    bilibili_enabled=settings.bilibili_research_enabled,
    douban_enabled=settings.douban_research_enabled,
    zhihu_base_url=settings.zhihu_research_base_url,
    bilibili_base_url=settings.bilibili_research_base_url,
    douban_base_url=settings.douban_research_base_url,
    timeout_seconds=settings.domestic_research_timeout_seconds,
    max_sources=settings.domestic_research_max_sources,
)
spotify_catalog = SpotifyCatalogTool(settings.spotify_client_id, settings.spotify_client_secret, settings.spotify_market)
graph = build_candidate_pool_graph(music_catalog, web_search, KnowledgeSearchTool(settings.milvus_uri, settings.embedding_model, settings.knowledge_collection), DeepSeekCandidateSelector(), domestic_research, spotify_catalog if settings.spotify_discovery_enabled else None)
report_graph = build_report_graph(TournamentFactsTool(settings.java_internal_base_url, settings.agent_internal_service_token), ReportGenerator(), web_search, KnowledgeSearchTool(settings.milvus_uri, settings.embedding_model, settings.knowledge_collection), domestic_research)
conversation_runtime = ConversationReActRuntime(web_search, KnowledgeSearchTool(settings.milvus_uri, settings.embedding_model, settings.knowledge_collection), tool_registry, skill_registry, graph, music_catalog)

_ACTION_PROGRESS = {
    "understand_preference": ("understand_preference", "正在理解你的音乐偏好"),
    "resolve_named_entities": ("resolve_artist", "正在核验你提到的艺人"),
    "search_web": ("discover_web", "正在从公开音乐资料中寻找线索"),
    "search_spotify": ("discover_spotify", "正在从 Spotify 目录补充国际音乐线索"),
    "search_domestic_content": ("discover_domestic", "正在补充中文社区音乐资料"),
    "resolve_musicbrainz": ("verify_musicbrainz", "正在通过 MusicBrainz 核验歌曲身份"),
    "search_catalog": ("search_catalog", "正在整理已核验的本地目录"),
    "expand_artist_catalog": ("expand_artist_catalog", "正在批量核验已提及艺人的作品"),
    "search_knowledge": ("knowledge_context", "正在补充歌曲主题与文化语境"),
    "analyze_tournament": ("analyze_matches", "正在归纳本场的关键选择轨迹"),
    "draft_report": ("draft_report", "正在生成你的音乐偏好报告"),
    "draft_response": ("draft_response", "正在整理这次音乐探索的回应"),
    "clarify": ("clarify", "正在确认这次探索还需要哪些信息"),
    "propose_tournament": ("propose_tournament", "正在准备歌曲世界杯入口"),
    "build_candidate_pool": ("build_candidate_pool", "正在自主构建并核验歌曲世界杯候选池"),
    "generate_exploration_report": ("generate_exploration_report", "正在根据当前对话生成探索报告"),
    "recommend_music": ("recommend_music", "正在提取并核验歌曲与艺人推荐"),
    "respond": ("draft_response", "正在整理这次音乐探索的回应"),
    "critique_report": ("review_report", "正在核验报告事实与推荐来源"),
    "rerank_candidates": ("organize_candidates", "正在并行重排候选，并生成入选理由"),
}

def _progress(request_id, action: str, elapsed_ms: int, metrics: dict | None = None) -> str:
    phase, message = _ACTION_PROGRESS.get(action, ("working", "正在继续整理本次音乐探索"))
    payload = {"runId": str(request_id), "phase": phase, "status": "started", "message": message, "elapsedMs": elapsed_ms, "metrics": metrics or {}}
    return f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

_PLAN_ACTIONS = {
    "understand_preference": ("理解音乐偏好", "结合本轮输入与已有上下文确定探索边界"),
    "resolve_named_entities": ("核验艺人身份", "检查用户提到的艺人及可能歧义"),
    "request_clarification": ("等待用户确认", "存在会影响候选正确性的身份歧义"),
    "search_catalog": ("检查规范歌曲目录", "读取已核验曲目作为可用补充"),
    "expand_artist_catalog": ("扩展明确艺人的作品", "从公开音乐目录批量发现并核验作品"),
    "search_web": ("搜索公开音乐资料", "从网络寻找歌曲、艺人及相关音乐语境"),
    "search_domestic_content": ("检索中文内容平台", "补充国内社区中的音乐资料"),
    "search_spotify": ("检索流媒体目录", "补充国际音乐目录线索"),
    "search_knowledge": ("检索歌曲主题卡", "以本地知识库补充主题与文化语境"),
    "resolve_musicbrainz": ("核验新发现歌曲", "把网络线索解析为规范歌曲身份"),
    "rerank_candidates": ("重排候选歌曲", "按偏好相关性和证据质量生成入选理由"),
    "submit_candidates": ("检查并提交候选池", "确认主池与补位池满足开赛要求"),
    "finish_insufficient": ("说明候选不足", "整理已完成的搜索与仍缺少的数量"),
    "analyze_tournament": ("分析关键对局", "归纳胜出、淘汰和稳定偏好信号"),
    "draft_report": ("撰写偏好报告", "基于赛事事实组织结论和推荐"),
    "critique_report": ("审查报告证据", "核对推荐来源、事实边界与表达风险"),
    "submit_report": ("提交赛后报告", "完成结构化结果并交由 Java 校验"),
    "finish_degraded": ("生成保守报告", "在证据有限时明确边界并完成报告"),
    "clarify": ("澄清音乐方向", "只询问会实质影响下一步的信息"),
    "propose_tournament": ("准备世界杯入口", "把当前偏好整理为可进入的歌曲世界杯"),
    "build_candidate_pool": ("构建世界杯候选池", "调用候选池能力完成在线发现与身份核验"),
    "generate_exploration_report": ("生成对话探索报告", "总结当前对话中的偏好和探索方向"),
    "recommend_music": ("生成音乐推荐", "从公开资料提取具体推荐并核验歌曲身份"),
    "respond": ("整理音乐回应", "基于现有信息生成可继续追问的回答"),
}

def _plan_event(request_id, state: dict, workflow: str, current_completed: bool = False) -> str:
    """Project actual ReAct decisions into a revisable public todo list."""
    history = [item for item in state.get("action_history", []) if item.get("action") in _PLAN_ACTIONS]
    occurrences: dict[str, int] = {}
    items = []
    for index, entry in enumerate(history):
        action = entry["action"]
        occurrences[action] = occurrences.get(action, 0) + 1
        title, fallback_detail = _PLAN_ACTIONS[action]
        is_current = index == len(history) - 1
        items.append({
            "id": f"{action}-{occurrences[action]}",
            "title": title,
            "status": "completed" if not is_current or current_completed or state.get("result") else "running",
            "detail": str(entry.get("summary") or fallback_detail)[:160],
        })
    items = items[-5:]
    count = len(state.get("recordings", []))
    if workflow == "candidate":
        target = state.get("request").size * 2 if state.get("request") else 0
        goal, summary = f"为 {target // 2} 首赛事准备 {target} 首可核验候选", f"已核验 {count} / {target} 首；计划会随搜索结果继续调整。"
    elif workflow == "report":
        goal, summary = "基于本场歌曲世界杯生成偏好报告", "计划正依据赛事事实、外部证据和审查结果滚动调整。"
    else:
        goal, summary = "推进这次音乐探索对话", "计划正根据对话内容和工具结果滚动调整。"
    if not items:
        items = [{"id":"prepare-1","title":"准备本次音乐探索","status":"running","detail":"正在读取本轮请求与可用上下文"}]
    # Starting and completing an action are distinct public snapshots, so they
    # need distinct revisions even when action_history itself is unchanged.
    revision = max(1, len(history) * 2 - (0 if current_completed else 1))
    payload = {"runId": str(request_id), "revision": revision, "goal": goal, "summary": summary, "changeSummary": "已根据最新 ReAct 决策重排公开计划", "items": items}
    return f"event: plan_updated\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

async def verify_caller(authorization: str = Header(default="")):
    if authorization != f"Bearer {settings.agent_internal_service_token}": raise HTTPException(401, "invalid internal credential")

@app.get("/health/live")
async def live(): return {"status":"UP"}

@app.get("/health/ready")
async def ready(): return {"status":"UP", "catalog":"configured", "modelProvider": settings.llm_provider, "webSearch": bool(settings.tavily_api_key), "spotifyDiscovery": settings.spotify_discovery_enabled and spotify_catalog.enabled, "toolRegistry": tool_registry.summaries(), "skills": skill_registry.summaries(), "domesticResearch": {"zhihu": settings.zhihu_research_enabled, "bilibili": settings.bilibili_research_enabled, "douban": settings.douban_research_enabled}}

@app.post("/internal/v1/memory:compress", response_model=MemoryCompressionResponse, dependencies=[Depends(verify_caller)])
async def compress_memory(request: MemoryCompressionRequest):
    summary = await conversation_runtime.compress_memory(request.previous_summary, request.messages)
    return MemoryCompressionResponse(summary=summary, through_sequence=request.through_sequence)

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
            if not (result.card_intent and result.card_intent.message_type == "CLARIFICATION_CARD"):
                result.memory_summary = await conversation_runtime.build_memory_summary(request, result)
            logger.info("conversation ReAct completed request_id=%s trace=%s", request.request_id, result.trace_summary)
            yield f"event: result\ndata: {result.model_dump_json(by_alias=True)}\n\n"
        except Exception:
            logger.exception("conversation workflow failed")
            yield f"event: error\ndata: {json.dumps({'code':'CONVERSATION_UNAVAILABLE','message':'这次音乐对话暂时无法完成，请稍后重试'},ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")

@app.post("/internal/v1/workflows/conversation:resume", dependencies=[Depends(verify_caller)])
async def conversation_resume(request: ConversationResumeRequest, x_request_id: str = Header()):
    if str(request.request_id) != x_request_id: raise HTTPException(400, "X-Request-Id must match requestId")
    async def events():
        initial_action = "build_candidate_pool" if request.resume_kind == "ARTIST_IDENTITY" else "clarify"
        yield _progress(request.request_id, initial_action, 0)
        try:
            result = await conversation_runtime.resume(request)
            phase = "build_candidate_pool" if request.resume_kind == "ARTIST_IDENTITY" else "resume_conversation"
            message = "正在根据确认结果继续构建候选池" if request.resume_kind == "ARTIST_IDENTITY" else "已收到补充信息，正在继续本轮探索"
            yield f"event: progress\ndata: {json.dumps({'runId': str(request.request_id), 'phase': phase, 'status': 'running', 'message': message, 'elapsedMs': 0}, ensure_ascii=False)}\n\n"
            logger.info("conversation resume completed request_id=%s trace=%s", request.request_id, result.trace_summary)
            yield f"event: result\ndata: {result.model_dump_json(by_alias=True)}\n\n"
        except Exception:
            logger.exception("conversation resume failed")
            yield f"event: error\ndata: {json.dumps({'code':'CONVERSATION_RESUME_UNAVAILABLE','message':'澄清后的候选池暂时无法继续，请稍后重试'},ensure_ascii=False)}\n\n"
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
