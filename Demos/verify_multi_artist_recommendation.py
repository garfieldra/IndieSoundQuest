#!/usr/bin/env python3
"""Real local E2E guard for multi-artist recommendation coverage."""

from __future__ import annotations

import json
import time
from collections import Counter
from uuid import uuid4

import httpx


PROMPT = "我最喜欢安溥、张悬、徐佳莹、郑宜农、艾怡良、TizzyBac等歌手，根据我的偏好进行分析，推荐一些我可能喜欢的音乐。"


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
        represented = list(dict.fromkeys(str(item.get("artistName") or "") for item in songs if item.get("artistName")))
        if len(represented) < 5:
            raise AssertionError(f"Multi-artist result collapsed: {represented}")
        counts = Counter(str(item.get("artistName") or "") for item in songs if item.get("artistName"))
        # 安溥/张悬 are two user-supplied names for one actual artist, so up to
        # three of seven cards may legitimately represent that shared identity.
        if max(counts.values(), default=0) > 3:
            raise AssertionError(f"One artist still dominates the seven-card result: {dict(counts)}")
        if any(str(item.get("title") or "").strip().casefold() == "hell" for item in songs):
            raise AssertionError("Catalogue browse-order fallback leaked the low-confidence title 'Hell'")
        print(json.dumps({
            "status": status,
            "conversationId": conversation_id,
            "songCount": len(songs),
            "representedArtists": represented,
            "summary": payload.get("summary"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
