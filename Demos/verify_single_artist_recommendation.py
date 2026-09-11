#!/usr/bin/env python3
"""Real local E2E guard for an explicit single-artist song request."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx


PROMPT = "给我推荐几首asen的歌曲。"


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8080", timeout=30) as client:
        conversation = client.post("/api/v1/conversations")
        conversation.raise_for_status()
        conversation_id = conversation.json()["id"]
        queued = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())},
            json={"content": PROMPT},
        )
        queued.raise_for_status()
        run_id = queued.json()["runId"]
        deadline = time.monotonic() + 12 * 60
        status = "QUEUED"
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/agent-runs/{run_id}/events")
            response.raise_for_status()
            status = response.json()["runStatus"]
            if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}:
                break
            time.sleep(1)
        if status != "COMPLETED":
            raise RuntimeError(f"Agent run did not complete successfully: {status}")
        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages")
        messages.raise_for_status()
        cards = [item for item in messages.json() if item.get("cardType") == "MUSIC_RECOMMENDATIONS"]
        if not cards:
            raise AssertionError("No MUSIC_RECOMMENDATIONS card was persisted")
        payload = json.loads(cards[-1]["cardPayloadJson"])
        songs = payload.get("songs") or []
        if len(songs) < 3:
            raise AssertionError(f"Explicit single-artist request returned fewer than three songs: {songs}")
        artists = {str(item.get("artistName") or "") for item in songs}
        if not any("asen" in value.casefold() or "艾志恒" in value for value in artists):
            raise AssertionError(f"Songs do not belong to the requested Asen identity: {artists}")
        print(json.dumps({
            "status": status,
            "conversationId": conversation_id,
            "songs": [f"{item.get('artistName')} — {item.get('title')}" for item in songs],
            "summary": payload.get("summary"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
