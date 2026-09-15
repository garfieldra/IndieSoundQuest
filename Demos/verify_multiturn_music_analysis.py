#!/usr/bin/env python3
"""End-to-end guard for analysis inheritance and autonomous gap research."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx


def wait_for_run(client: httpx.Client, run_id: str) -> tuple[str, list[dict]]:
    deadline = time.monotonic() + 12 * 60
    status = "QUEUED"
    events: list[dict] = []
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()
        status = snapshot["runStatus"]
        events = snapshot.get("events") or []
        if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}:
            return status, events
        time.sleep(1)
    return status, events


def send(client: httpx.Client, conversation_id: str, content: str) -> tuple[str, list[dict]]:
    queued = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": str(uuid4())}, json={"content": content},
    ).raise_for_status().json()
    run_id = queued["runId"]
    status, events = wait_for_run(client, run_id)
    if status != "COMPLETED":
        raise RuntimeError(f"Agent run {run_id} did not complete: {status}")
    return run_id, events


def analysis_cards(client: httpx.Client, conversation_id: str) -> list[dict]:
    messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
    return [item for item in messages if item.get("cardType") == "MUSIC_ANALYSIS"]


def main() -> None:
    base_url = os.getenv("ISQ_API_BASE_URL", "http://127.0.0.1:8080")
    with httpx.Client(base_url=base_url, timeout=30) as client:
        conversation_id = client.post("/api/v1/conversations").raise_for_status().json()["id"]
        first_run, _ = send(
            client, conversation_id,
            "为什么张悬《宝贝》听起来很亲密？请结合演唱距离感和编曲留白深入分析。",
        )
        first_cards = analysis_cards(client, conversation_id)
        if len(first_cards) != 1:
            raise AssertionError("First turn did not persist exactly one analysis card")
        first = first_cards[0]
        first_payload = json.loads(first["cardPayloadJson"])

        second_run, second_events = send(
            client, conversation_id,
            "继续刚才这首歌：重点比较录音室版本与现场表达的距离感；如果现有证据不足，请换检索角度补充公开资料。",
        )
        cards = analysis_cards(client, conversation_id)
        if len(cards) < 2:
            raise AssertionError("Follow-up did not persist a revised analysis card")
        second_payload = json.loads(cards[-1]["cardPayloadJson"])
        if second_payload.get("contextMode") != "REFINED":
            raise AssertionError("Follow-up card is not marked as a refinement")
        if second_payload.get("basedOnCardId") != first.get("id"):
            raise AssertionError("Follow-up card lost its persisted parent card")
        if second_payload.get("subject") != first_payload.get("subject"):
            raise AssertionError("Terse follow-up lost or changed the analysis subject")
        if int(second_payload.get("workspaceRevision") or 0) <= int(first_payload.get("workspaceRevision") or 0):
            raise AssertionError("Analysis workspace revision did not advance")
        first_urls = {item.get("url") for item in first_payload.get("sources") or [] if item.get("url")}
        second_urls = {item.get("url") for item in second_payload.get("sources") or [] if item.get("url")}
        if first_urls and not first_urls.intersection(second_urls):
            raise AssertionError("Follow-up discarded every reusable source from the previous turn")
        if "横向比较" not in (second_payload.get("selectedDimensions") or []):
            raise AssertionError(
                "Follow-up did not revise analysis dimensions for version comparison: "
                + json.dumps({
                    "subject": second_payload.get("subject"),
                    "question": second_payload.get("question"),
                    "dimensions": second_payload.get("selectedDimensions"),
                    "events": second_events[-8:],
                }, ensure_ascii=False)
            )
        event_text = json.dumps(second_events, ensure_ascii=False)
        if "search_web" not in event_text and "read_source" not in event_text and "补充" not in event_text:
            raise AssertionError("Evidence-gap follow-up showed no autonomous research activity")

        print(json.dumps({
            "status": "COMPLETED", "conversationId": conversation_id,
            "firstRunId": first_run, "secondRunId": second_run,
            "subject": second_payload.get("subject"),
            "firstWorkspaceRevision": first_payload.get("workspaceRevision"),
            "secondWorkspaceRevision": second_payload.get("workspaceRevision"),
            "reusedSourceCount": len(first_urls.intersection(second_urls)),
            "sourceCount": len(second_urls),
            "selectedDimensions": second_payload.get("selectedDimensions"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
