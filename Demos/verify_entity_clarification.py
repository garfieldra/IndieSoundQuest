#!/usr/bin/env python3
"""Real E2E guard for necessary same-name artist clarification."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8080", timeout=30) as client:
        conversation_id = client.post("/api/v1/conversations").raise_for_status().json()["id"]
        queued = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())}, json={"content": "给我推荐几首 Alex 的歌。"},
        ).raise_for_status().json()
        run_id = queued["runId"]
        deadline = time.monotonic() + 5 * 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()["runStatus"]
            if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}:
                break
            time.sleep(1)
        if status != "WAITING_FOR_USER":
            raise AssertionError(f"Ambiguous artist did not pause for clarification: {status}")
        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
        cards = [item for item in messages if item.get("cardType") == "GENERAL_CLARIFICATION"]
        if not cards:
            raise AssertionError("Clarification card was not persisted")
        payload = json.loads(cards[-1]["cardPayloadJson"])
        if not payload.get("entityCandidates"):
            raise AssertionError(f"Clarification card lacks structured entity candidates: {payload}")
        candidates = payload["entityCandidates"][0]["candidates"]
        selected = next((item for item in candidates if item.get("name") == "Alex G"), candidates[0])
        resumed = client.post(
            f"/api/v1/agent-runs/{run_id}/answers",
            headers={"Idempotency-Key": str(uuid4())},
            json={"selections": [{
                "mention": payload["entityCandidates"][0]["mention"],
                "mbid": selected["mbid"], "name": selected["name"],
            }]}, timeout=720,
        )
        resumed.raise_for_status()
        if "message_completed" not in resumed.text:
            raise AssertionError(f"Clarified run did not return a completed message: {resumed.text[-1000:]}")
        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
        recommendations = [item for item in messages if item.get("cardType") == "MUSIC_RECOMMENDATIONS"]
        if not recommendations:
            raise AssertionError("Clarified artist selection did not continue into a recommendation card")
        recommendation = json.loads(recommendations[-1]["cardPayloadJson"])
        songs = recommendation.get("songs") or []
        if not songs or not all("alex g" in str(item.get("artistName") or "").casefold() for item in songs):
            raise AssertionError(f"Clarified recommendation escaped the selected identity: {songs}")
        print(json.dumps({
            "status": "COMPLETED_AFTER_CLARIFICATION", "conversationId": conversation_id,
            "selected": selected, "songs": [f"{item.get('artistName')} — {item.get('title')}" for item in songs],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
