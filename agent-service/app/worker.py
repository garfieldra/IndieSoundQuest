import asyncio
import json
import os
import socket

import aio_pika
import httpx

from .main import conversation_runtime, graph, report_graph, _plan_event
from .report_schemas import TournamentReportRequest
from .schemas import ConversationAgentRequest, CandidatePoolRequest
from .settings import settings

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"
JAVA = settings.java_internal_base_url.rstrip("/")
HEADERS = {"Authorization": f"Bearer {settings.agent_internal_service_token}"}

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
                if run_type == "CONVERSATION":
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
                result = None; last_action = None; state = {}
                async for state in runtime_graph.astream({"request": request}, config, stream_mode="values"):
                    action = state.get("decision").action if state.get("decision") else None
                    if action and action != last_action:
                        last_action = action
                        payload = {"runId": run_id, "phase": action, "status": "started", "message": "Agent 正在继续处理"}
                        await callback(client, run_id, "events", lease, {"type":"progress","payloadJson":json.dumps(payload,ensure_ascii=False)})
                    if state.get("action_history"):
                        raw = _plan_event(request.request_id, state, plan_kind).split("data: ",1)[1].strip()
                        await callback(client, run_id, "events", lease, {"type":"plan_updated","payloadJson":raw})
                    if state.get("result"): result = state["result"]
                if state.get("error_code"):
                    raise RuntimeError(str(state["error_code"]))
                if result is None: raise RuntimeError(f"{run_type.lower()} result missing")
                # Agent results contain UUID values. Use Pydantic's JSON mode
                # so every worker callback receives wire-safe data.
                dumped=result.model_dump(by_alias=True, mode="json")
                if run_type=="CONVERSATION":
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
