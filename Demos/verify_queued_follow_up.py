#!/usr/bin/env python3
"""Real E2E guard for a durable, single-slot next-turn queue."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx


TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}


def snapshot(client: httpx.Client, run_id: str) -> dict:
    return client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()


def wait_for(client: httpx.Client, run_id: str, wanted: set[str], timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = snapshot(client, run_id)
        if state["runStatus"] in wanted:
            return state
        time.sleep(0.5)
    raise TimeoutError(f"Run {run_id} did not reach {wanted}; last state={state.get('runStatus')}")


def main() -> None:
    base_url = os.getenv("ISQ_API_BASE_URL", "http://127.0.0.1:8080")
    with httpx.Client(base_url=base_url, timeout=45) as client:
        conversation_id = client.post("/api/v1/conversations").raise_for_status().json()["id"]
        first = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())},
            json={"content": "请查阅公开资料，深入分析安溥《最好的时光》的创作背景与编曲表达。"},
        ).raise_for_status().json()
        first_run_id = first["runId"]
        wait_for(client, first_run_id, {"RUNNING"}, 90)

        follow_key = str(uuid4())
        follow_content = "接下来请比较这首歌的录音室版本与常见现场演绎，不必重复上一轮结论。"
        queued = client.post(
            f"/api/v1/agent-runs/{first_run_id}/next-message",
            headers={"Idempotency-Key": follow_key},
            json={"content": follow_content},
        ).raise_for_status().json()
        replay = client.post(
            f"/api/v1/agent-runs/{first_run_id}/next-message",
            headers={"Idempotency-Key": follow_key},
            json={"content": follow_content},
        ).raise_for_status().json()
        if queued["id"] != replay["id"] or queued["status"] != "WAITING":
            raise AssertionError("Waiting-message idempotency failed")

        rejected = client.post(
            f"/api/v1/agent-runs/{first_run_id}/next-message",
            headers={"Idempotency-Key": str(uuid4())},
            json={"content": "第二条并发等待消息必须被拒绝。"},
        )
        if rejected.status_code != 409:
            raise AssertionError(f"Second waiting message was not rejected: {rejected.status_code}")

        first_result = wait_for(client, first_run_id, TERMINAL, 12 * 60)
        if first_result["runStatus"] != "COMPLETED":
            raise RuntimeError(f"First run did not complete: {first_result['runStatus']}")

        deadline = time.monotonic() + 30
        dispatched: dict = {}
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/agent-runs/{first_run_id}/next-message")
            response.raise_for_status()
            dispatched = response.json()
            if dispatched.get("status") == "DISPATCHED" and dispatched.get("nextRunId"):
                break
            time.sleep(0.25)
        else:
            raise TimeoutError(f"Waiting message was not dispatched: {dispatched}")

        second_run_id = dispatched["nextRunId"]
        second_result = wait_for(client, second_run_id, TERMINAL, 12 * 60)
        if second_result["runStatus"] != "COMPLETED":
            raise RuntimeError(f"Automatically dispatched run did not complete: {second_result['runStatus']}")

        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
        follow_messages = [item for item in messages if item.get("type") == "USER_TEXT" and item.get("content") == follow_content]
        next_placeholders = [item for item in messages if item.get("type") == "AGENT_RUN" and item.get("agentRunId") == second_run_id]
        if len(follow_messages) != 1 or len(next_placeholders) != 1:
            raise AssertionError("Queued follow-up was lost or duplicated in the conversation timeline")

        first_types = [item["type"] for item in first_result.get("events") or []]
        if "FOLLOW_UP_QUEUED" not in first_types or "FOLLOW_UP_DISPATCHED" not in first_types:
            raise AssertionError(f"Durable follow-up events are incomplete: {first_types}")

        print(json.dumps({
            "conversationId": conversation_id,
            "firstRunId": first_run_id,
            "nextRunId": second_run_id,
            "firstStatus": first_result["runStatus"],
            "nextStatus": second_result["runStatus"],
            "secondWaitingMessageStatus": rejected.status_code,
            "followUpStatus": dispatched["status"],
            "timelineMessageCount": len(messages),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
