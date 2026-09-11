#!/usr/bin/env python3
"""Real E2E guard for scene/mood preference reasoning without a named artist."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx


PROMPT = "推荐一些适合深夜写代码、克制但有节奏感的华语音乐。"


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8080", timeout=30) as client:
        conversation_id = client.post("/api/v1/conversations").raise_for_status().json()["id"]
        queued = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())}, json={"content": PROMPT},
        ).raise_for_status().json()
        run_id = queued["runId"]
        deadline = time.monotonic() + 12 * 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()["runStatus"]
            if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}:
                break
            time.sleep(1)
        if status != "COMPLETED":
            raise RuntimeError(f"Agent run did not complete successfully: {status}")
        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
        cards = [item for item in messages if item.get("cardType") == "MUSIC_RECOMMENDATIONS"]
        if not cards:
            raise AssertionError("Scene request did not persist a recommendation card")
        payload = json.loads(cards[-1]["cardPayloadJson"])
        songs = payload.get("songs") or []
        profile = payload.get("preferenceProfile") or {}
        if len(songs) < 3:
            raise AssertionError(f"Scene request returned too few verified songs: {songs}")
        scene_text = " ".join(profile.get("scenes") or [])
        if "深夜" not in scene_text or "写代码" not in scene_text:
            raise AssertionError(f"Listening scene was lost from preference profile: {profile}")
        inferred_text = " ".join([*(profile.get("moods") or []), *(profile.get("sonic_traits") or [])])
        if "克制" not in inferred_text:
            raise AssertionError(f"Restrained sound preference was lost from profile: {profile}")
        print(json.dumps({
            "status": status, "conversationId": conversation_id,
            "songs": [f"{item.get('artistName')} — {item.get('title')}" for item in songs],
            "preferenceProfile": profile, "summary": payload.get("summary"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
