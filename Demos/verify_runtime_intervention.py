#!/usr/bin/env python3
"""Real E2E guard for durable in-run steering and ReAct replanning."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx


INITIAL_PROMPT = (
    "请深入分析张悬《宝贝》的歌词、编曲和创作背景，"
    "查阅公开资料后给出有证据边界的回答。"
)
DIRECTION = "重点核对制作人与录音版本；不要给歌曲推荐，最终回答请明确回应这项调整。"
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}


def _snapshot(client: httpx.Client, run_id: str) -> dict:
    return client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()


def _queue(client: httpx.Client, run_id: str, key: str, content: str) -> dict:
    return client.post(
        f"/api/v1/agent-runs/{run_id}/next-message",
        headers={"Idempotency-Key": key},
        json={"content": content},
    ).raise_for_status().json()


def main() -> None:
    base_url = os.getenv("ISQ_API_BASE_URL", "http://127.0.0.1:8080")
    with httpx.Client(base_url=base_url, timeout=45) as client:
        conversation_id = client.post("/api/v1/conversations").raise_for_status().json()["id"]
        queued = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())},
            json={"content": INITIAL_PROMPT},
        ).raise_for_status().json()
        run_id = queued["runId"]

        deadline = time.monotonic() + 90
        snapshot = {}
        while time.monotonic() < deadline:
            snapshot = _snapshot(client, run_id)
            if snapshot["runStatus"] == "RUNNING":
                break
            if snapshot["runStatus"] in TERMINAL:
                raise RuntimeError(f"Run ended before intervention: {snapshot['runStatus']}")
            time.sleep(0.2)
        else:
            raise TimeoutError("Agent Run was not claimed in time")

        queued_key = str(uuid4())
        queued = _queue(client, run_id, queued_key, DIRECTION)
        replay = _queue(client, run_id, queued_key, DIRECTION)
        if queued["id"] != replay["id"]:
            raise AssertionError("Follow-up idempotency replay created a different record")
        second = client.post(
            f"/api/v1/agent-runs/{run_id}/next-message",
            headers={"Idempotency-Key": str(uuid4())},
            json={"content": "这条消息不应覆盖等待区。"},
        )
        if second.status_code != 409:
            raise AssertionError(f"Run accepted a second waiting message: {second.status_code}")
        intervention = client.post(f"/api/v1/agent-runs/{run_id}/next-message:intervene").raise_for_status().json()
        replay_intervention = client.post(f"/api/v1/agent-runs/{run_id}/next-message:intervene").raise_for_status().json()
        if intervention["id"] != replay_intervention["id"]:
            raise AssertionError("Intervention conversion is not idempotent")

        deadline = time.monotonic() + 12 * 60
        while time.monotonic() < deadline:
            snapshot = _snapshot(client, run_id)
            if snapshot["runStatus"] in TERMINAL:
                break
            time.sleep(1)
        if snapshot.get("runStatus") != "COMPLETED":
            raise RuntimeError(f"Steered Agent Run did not complete: {snapshot.get('runStatus')}")

        events = snapshot.get("events") or []
        event_types = [event.get("type") for event in events]
        for required in ("INTERVENTION_ACCEPTED", "INTERVENTION_APPLIED", "PLAN_UPDATED", "RESULT"):
            if required not in event_types:
                raise AssertionError(f"Missing public run event: {required}; got {event_types}")
        for required in ("COMMENTARY", "RESPONSE_STARTED", "RESPONSE_DELTA"):
            if required not in event_types:
                raise AssertionError(f"Missing Codex-style public streaming event: {required}")

        result_event = next(event for event in reversed(events) if event.get("type") == "RESULT")
        artifact = json.loads(result_event.get("payloadJson") or "{}")
        trace = artifact.get("traceSummary") or {}
        if int(trace.get("appliedInterventionSequence") or 0) != int(intervention["sequenceNumber"]):
            raise AssertionError(f"Final trace did not acknowledge the intervention: {trace}")

        messages = client.get(
            f"/api/v1/conversations/{conversation_id}/messages"
        ).raise_for_status().json()
        contents = [item.get("content") for item in messages if item.get("type") == "USER_TEXT"]
        if contents.count(DIRECTION) != 1:
            raise AssertionError(f"Durable intervention messages are missing or duplicated: {contents}")
        assistant_text = next(
            (item.get("content") or "" for item in reversed(messages) if item.get("type") == "AGENT_TEXT"),
            "",
        )
        if not any(term in assistant_text for term in ("制作人", "录音", "版本")):
            raise AssertionError("Final answer did not reflect the latest direction")
        streamed_text = "".join(
            str(json.loads(event.get("payloadJson") or "{}").get("delta") or "")
            for event in events if event.get("type") == "RESPONSE_DELTA"
        )
        if streamed_text != assistant_text:
            raise AssertionError("Durable response deltas do not reconstruct the persisted final answer")

        applied_event = next(event for event in events if event.get("type") == "INTERVENTION_APPLIED")
        applied_sequence = int(json.loads(applied_event.get("payloadJson") or "{}").get("interventionSequence") or 0)
        if applied_sequence != int(intervention["sequenceNumber"]):
            raise AssertionError("Worker did not apply the converted intervention")

        late = client.post(
            f"/api/v1/agent-runs/{run_id}/interventions",
            headers={"Idempotency-Key": str(uuid4())},
            json={"content": "这条指令到得太晚，不应改写已经完成的结果。"},
        )
        if late.status_code != 409:
            raise AssertionError(f"Terminal run accepted a late intervention: {late.status_code}")

        print(json.dumps({
            "status": snapshot["runStatus"],
            "conversationId": conversation_id,
            "runId": run_id,
            "interventionSequence": intervention["sequenceNumber"],
            "secondWaitingMessageStatus": second.status_code,
            "eventTypes": event_types,
            "planRevisionCount": event_types.count("PLAN_UPDATED"),
            "lateInterventionStatus": late.status_code,
            "finalTrace": trace,
            "assistantLength": len(assistant_text),
            "responseDeltaCount": event_types.count("RESPONSE_DELTA"),
            "commentaryCount": event_types.count("COMMENTARY"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
