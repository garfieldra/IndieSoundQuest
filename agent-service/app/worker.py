import asyncio
import json
import os
import socket

import aio_pika
import httpx

from .main import conversation_runtime, graph, report_graph, _plan_event, _ACTION_PROGRESS
from .report_schemas import TournamentReportRequest
from .schemas import ConversationAgentRequest, CandidatePoolRequest
from .settings import settings

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
JAVA = settings.java_internal_base_url.rstrip("/")
HEADERS = {"Authorization": f"Bearer {settings.agent_internal_service_token}"}

_PUBLIC_TOOLS = {
    "understand_preference": ("偏好理解子任务", "subagent"),
    "resolve_named_entities": ("艺人身份解析", "tool"),
    "search_catalog": ("规范歌曲目录", "tool"),
    "expand_artist_catalog": ("艺人作品扩展", "tool"),
    "search_web": ("网络音乐搜索", "tool"),
    "search_domestic_content": ("中文内容检索", "tool"),
    "search_spotify": ("流媒体目录检索", "tool"),
    "search_knowledge": ("Milvus 主题知识库", "tool"),
    "resolve_musicbrainz": ("MusicBrainz 身份核验", "tool"),
    "rerank_candidates": ("候选语义重排", "subagent"),
    "analyze_tournament": ("赛事偏好分析", "subagent"),
    "draft_report": ("报告撰写", "subagent"),
    "critique_report": ("报告证据审查", "subagent"),
    "generate_exploration_report": ("对话探索报告", "subagent"),
    "recommend_music": ("普通音乐推荐", "subagent"),
}

def _tool_records(state: dict) -> list:
    board = state.get("board") or {}
    return list(board.get("tool_call_history", state.get("tool_history", [])) or [])

def _observations(state: dict) -> list[dict]:
    return list(state.get("observations", []) or [])

def _record_value(record, key: str, default=None):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)

def _tool_payload(run_id: str, action: str, status: str, record=None, observation: dict | None = None) -> dict:
    name, kind = _PUBLIC_TOOLS.get(action, (action.replace("_", " "), "tool"))
    metrics = {}
    for key in ("inputCount", "outputCount", "verifiedCount", "sourceCount", "hintCount", "count"):
        value = (observation or {}).get(key)
        if isinstance(value, (int, float)): metrics[key] = value
    duration = int(_record_value(record, "duration_ms", 0) or 0)
    messages = {
        "started": f"正在调用{name}",
        "completed": f"{name}已完成",
        "degraded": f"{name}暂不可用，Agent 正在调整计划",
    }
    return {"runId": run_id, "toolKey": action, "toolName": name, "kind": kind, "status": status, "message": messages[status], "durationMs": duration, "metrics": metrics}

async def callback(client, run_id, path, lease, payload=None):
    headers = {**HEADERS, "X-Lease-Token": lease}
    response = await client.post(f"{JAVA}/internal/v1/agent-runs/{run_id}/{path}", headers=headers, json=payload or {})
    response.raise_for_status()

async def execute(message: aio_pika.IncomingMessage):
    lease = None
    attempt_no = 0
    try:
        task = json.loads(message.body)
        run_id = task["runId"]
        async with httpx.AsyncClient(timeout=30) as client:
            claim = await client.post(f"{JAVA}/internal/v1/agent-runs/{run_id}/claim", headers=HEADERS, json={"workerId": WORKER_ID})
            if claim.status_code == 409:
                await message.ack(); return
            claim.raise_for_status(); claim_body=claim.json(); lease=claim_body["leaseToken"]; attempt_no=claim_body["attemptNo"]
            stop = asyncio.Event()
            async def heartbeat():
                while not stop.is_set():
                    await asyncio.sleep(10)
                    if not stop.is_set(): await callback(client, run_id, "heartbeat", lease)
            pulse = asyncio.create_task(heartbeat())
            try:
                run_type=task.get("runType","CONVERSATION")
                if run_type in {"CONVERSATION", "EXPLORATION_REPORT"}:
                    request = ConversationAgentRequest.model_validate(task["input"])
                    runtime_graph = conversation_runtime.graph
                    config = {"configurable": {"thread_id": str(request.agent_run_id)}, "recursion_limit": 24}
                    plan_kind = "conversation"
                elif run_type == "CANDIDATE_POOL":
                    request = CandidatePoolRequest.model_validate(task["input"])
                    runtime_graph = graph
                    config = {"recursion_limit": 128}
                    plan_kind = "candidate"
                elif run_type == "TOURNAMENT_REPORT":
                    request = TournamentReportRequest.model_validate(task["input"])
                    runtime_graph = report_graph
                    config = {"recursion_limit": 80}
                    plan_kind = "report"
                    await callback(client, run_id, "report-started", lease, {"reportId": str(request.report_id)})
                else:
                    raise ValueError(f"unsupported runType: {run_type}")
                result = None; last_action = None; last_plan = None; state = {}; observation_count = 0; tool_record_count = 0
                async for state in runtime_graph.astream({"request": request}, config, stream_mode="values"):
                    action = state.get("decision").action if state.get("decision") else None
                    if action and action != last_action:
                        last_action = action
                        phase, message_text = _ACTION_PROGRESS.get(action, (action, "Agent 正在继续处理"))
                        payload = {"runId": run_id, "phase": phase, "status": "started", "message": message_text}
                        await callback(client, run_id, "events", lease, {"type":"progress","payloadJson":json.dumps(payload,ensure_ascii=False)})
                        if action in _PUBLIC_TOOLS:
                            tool = _tool_payload(run_id, action, "started")
                            await callback(client, run_id, "events", lease, {"type":"tool_started","payloadJson":json.dumps(tool,ensure_ascii=False)})
                    records = _tool_records(state); observations = _observations(state)
                    completed_now = False
                    if len(records) > tool_record_count:
                        new_records = records[tool_record_count:]
                        tool_status = "completed" if all(str(_record_value(record, "status", "success")) == "success" for record in new_records) else "degraded"
                        duration_ms = sum(int(_record_value(record, "duration_ms", 0) or 0) for record in new_records)
                        observation = next((item for item in reversed(observations[observation_count:]) if item.get("action") == action), None)
                        tool = _tool_payload(run_id, action or str(_record_value(new_records[-1], "name", "tool")), tool_status, record={"duration_ms": duration_ms}, observation=observation)
                        await callback(client, run_id, "events", lease, {"type":f"tool_{tool_status}","payloadJson":json.dumps(tool,ensure_ascii=False)})
                        tool_record_count = len(records); completed_now = True
                    elif len(observations) > observation_count:
                        for observation in observations[observation_count:]:
                            observed_action = str(observation.get("action") or action or "tool")
                            if observed_action not in _PUBLIC_TOOLS: continue
                            tool_status = "completed" if observation.get("status") == "success" else "degraded"
                            tool = _tool_payload(run_id, observed_action, tool_status, observation=observation)
                            await callback(client, run_id, "events", lease, {"type":f"tool_{tool_status}","payloadJson":json.dumps(tool,ensure_ascii=False)})
                        completed_now = True
                    observation_count = len(observations)
                    if state.get("result"): result = state["result"]
                    if state.get("action_history"):
                        raw = _plan_event(request.request_id, state, plan_kind, current_completed=completed_now or state.get("result") is not None).split("data: ",1)[1].strip()
                        if raw != last_plan:
                            last_plan = raw
                            await callback(client, run_id, "events", lease, {"type":"plan_updated","payloadJson":raw})
                if state.get("error_code"):
                    raise RuntimeError(str(state["error_code"]))
                if result is None: raise RuntimeError(f"{run_type.lower()} result missing")
                # Agent results contain UUID values. Use Pydantic's JSON mode
                # so every worker callback receives wire-safe data.
                dumped=result.model_dump(by_alias=True, mode="json")
                if run_type in {"CONVERSATION", "EXPLORATION_REPORT"}:
                    card=dumped.get("cardIntent"); await callback(client,run_id,"complete",lease,{"conversationId":str(request.conversation_id),"text":dumped["text"],"card":card})
                elif run_type=="CANDIDATE_POOL":
                    await callback(client,run_id,"complete-candidate",lease,{"size":request.size,"seedArtistIds":[str(x) for x in request.seed_artist_ids],"resultJson":json.dumps(dumped,ensure_ascii=False)})
                else:
                    await callback(client,run_id,"complete-report",lease,{"reportId":str(request.report_id),"resultJson":json.dumps(dumped,ensure_ascii=False)})
                await message.ack()
            except Exception as exc:
                if attempt_no < 3:
                    await callback(client,run_id,"retry",lease,{"code":"AGENT_WORKER_RETRY","message":str(exc)[:300]})
                    await message.nack(requeue=True)
                else:
                    if task.get("runType") == "TOURNAMENT_REPORT" and task.get("input",{}).get("reportId"):
                        await callback(client,run_id,"fail-report",lease,{"reportId":task["input"]["reportId"],"code":"AGENT_WORKER_FAILED","message":str(exc)[:300]})
                    else:
                        await callback(client,run_id,"fail",lease,{"code":"AGENT_WORKER_FAILED","message":str(exc)[:300]})
                    await message.reject(requeue=False)
            finally:
                stop.set(); pulse.cancel()
    except Exception:
        if not message.processed:
            await message.nack(requeue=True)

async def main():
    url=os.getenv("RABBITMQ_URL","amqp://indiesoundquest:indiesoundquest@rabbitmq/")
    connection=await aio_pika.connect_robust(url)
    channel=await connection.channel(); await channel.set_qos(prefetch_count=int(os.getenv("AGENT_WORKER_PREFETCH","2")))
    queue=await channel.declare_queue("isq.agent-runs.v1",durable=True,arguments={"x-dead-letter-exchange":"isq.dlx","x-dead-letter-routing-key":"agent-run.failed"})
    await queue.consume(execute)
    await asyncio.Future()

if __name__ == "__main__": asyncio.run(main())
