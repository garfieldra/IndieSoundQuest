#!/usr/bin/env python3
"""E2E guard for natural song questions and lossless deep answers."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx


PROMPT = "五月天的《拥抱》是一首什么样的歌曲？"


def main() -> None:
    base_url = os.getenv("ISQ_API_BASE_URL", "http://127.0.0.1:8080")
    with httpx.Client(base_url=base_url, timeout=30) as client:
        conversation_id = client.post("/api/v1/conversations").raise_for_status().json()["id"]
        queued = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())},
            json={"content": PROMPT},
        ).raise_for_status().json()
        run_id = queued["runId"]
        deadline = time.monotonic() + 12 * 60
        snapshot: dict = {}
        while time.monotonic() < deadline:
            snapshot = client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()
            if snapshot["runStatus"] in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}:
                break
            time.sleep(1)
        if snapshot.get("runStatus") != "COMPLETED":
            raise RuntimeError(f"Natural analysis run did not complete: {snapshot.get('runStatus')}")

        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
        answers = [
            item.get("content") for item in messages
            if item.get("type") == "AGENT_TEXT" and isinstance(item.get("content"), str)
        ]
        answer = max(answers, key=len, default="")
        cards = [item for item in messages if item.get("cardType") == "MUSIC_ANALYSIS"]
        if not cards:
            raise AssertionError("Natural descriptive song question did not load MUSIC_ANALYSIS")
        payload = json.loads(cards[-1]["cardPayloadJson"])
        if len(answer) < 700:
            raise AssertionError(f"Researched answer is still too shallow: {len(answer)} characters")
        review = payload.get("claimReview") or {}
        if review.get("overcompressionDetected") is True:
            raise AssertionError(f"Semantic review still over-compressed the answer: {review}")
        event_text = json.dumps(snapshot.get("events") or [], ensure_ascii=False)
        if "analyze_music" not in event_text and "深入音乐分析" not in event_text:
            raise AssertionError("Public run events do not show deep analysis routing")

        print(json.dumps({
            "status": snapshot["runStatus"],
            "conversationId": conversation_id,
            "runId": run_id,
            "analysisLength": len(answer),
            "workspaceRevision": payload.get("workspaceRevision"),
            "selectedDimensions": payload.get("selectedDimensions"),
            "sourceCount": len(payload.get("sources") or []),
            "claimCount": len(payload.get("claims") or []),
            "semanticReviewApplied": review.get("semanticReviewApplied"),
            "overcompressionDetected": review.get("overcompressionDetected"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
