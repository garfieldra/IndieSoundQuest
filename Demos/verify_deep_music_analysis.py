#!/usr/bin/env python3
"""Real end-to-end acceptance test for the deep music analysis Skill."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx


PROMPT = "为什么张悬《宝贝》听起来很亲密？请从演唱距离感、歌词称谓和编曲留白进行具体分析，并明确哪些是事实、哪些是解读。"
ALLOWED_CLAIM_TYPES = {"FACT", "OBSERVATION", "INTERPRETATION", "LISTENER_INFERENCE"}


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
        status = "QUEUED"
        deadline = time.monotonic() + 12 * 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()["runStatus"]
            if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}:
                break
            time.sleep(1)
        if status != "COMPLETED":
            raise RuntimeError(f"Deep analysis run did not complete successfully: {status}")

        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
        cards = [item for item in messages if item.get("cardType") == "MUSIC_ANALYSIS"]
        if not cards:
            raise AssertionError("Deep analysis card was not persisted")
        forbidden = [item.get("cardType") for item in messages if item.get("cardType") in {"WORLD_CUP_LAUNCH", "CANDIDATE_POOL"}]
        if forbidden:
            raise AssertionError(f"Deep analysis unexpectedly entered the World Cup flow: {forbidden}")

        payload = json.loads(cards[-1]["cardPayloadJson"])
        claims = payload.get("claims") or []
        sources = payload.get("sources") or []
        dimensions = payload.get("selectedDimensions") or []
        if not claims:
            raise AssertionError("Analysis did not produce any evidence claims")
        if not sources:
            raise AssertionError("Analysis did not retain any public sources")
        invalid_claims = [item for item in claims if item.get("claimType") not in ALLOWED_CLAIM_TYPES]
        if invalid_claims:
            raise AssertionError(f"Analysis contains invalid claim types: {invalid_claims}")
        unsupported_facts = [
            item for item in claims
            if item.get("claimType") == "FACT"
            and (item.get("status") == "supported" or item.get("confidence") in {"high", "medium"})
            and not item.get("evidenceRefs")
        ]
        if unsupported_facts:
            raise AssertionError(f"Fact claims were presented without evidence references: {unsupported_facts}")
        if not any(item.get("claimType") in {"OBSERVATION", "INTERPRETATION"} for item in claims):
            raise AssertionError("Analysis lacks an observable or interpretive argument")
        if not {"演唱表达", "歌词与叙事", "编曲与音色"}.intersection(dimensions):
            raise AssertionError(f"Question-relevant analysis dimensions were lost: {dimensions}")

        assistant_texts = [
            item.get("content") for item in messages
            if item.get("role") == "ASSISTANT" and isinstance(item.get("content"), str)
        ]
        analysis_text = max(assistant_texts, key=len, default="")
        if len(analysis_text) < 500:
            raise AssertionError(f"Deep analysis is still too shallow ({len(analysis_text)} characters)")

        print(json.dumps({
            "status": status,
            "conversationId": conversation_id,
            "runId": run_id,
            "analysisLength": len(analysis_text),
            "dimensions": dimensions,
            "claimCount": len(claims),
            "sourceCount": len(sources),
            "claimTypes": sorted({item.get("claimType") for item in claims}),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
