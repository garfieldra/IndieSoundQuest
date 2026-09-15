#!/usr/bin/env python3
"""Real E2E guard for autonomous source reading and creative-context research."""

from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx


PROMPT = "请深入分析张悬《宝贝》的创作背景与制作语境，以及这些信息如何帮助理解它的演唱和编曲表达。请区分事实与解读。"


def main() -> None:
    base_url = os.getenv("ISQ_API_BASE_URL", "http://127.0.0.1:8080")
    with httpx.Client(base_url=base_url, timeout=30) as client:
        conversation_id = client.post("/api/v1/conversations").raise_for_status().json()["id"]
        queued = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers={"Idempotency-Key": str(uuid4())}, json={"content": PROMPT},
        ).raise_for_status().json()
        run_id = queued["runId"]
        status = "QUEUED"
        events: list[dict] = []
        deadline = time.monotonic() + 12 * 60
        while time.monotonic() < deadline:
            snapshot = client.get(f"/api/v1/agent-runs/{run_id}/events").raise_for_status().json()
            status = snapshot["runStatus"]
            events = snapshot.get("events") or []
            if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "WAITING_FOR_USER"}:
                break
            time.sleep(1)
        if status != "COMPLETED":
            raise RuntimeError(f"Deep research run did not complete successfully: {status}")

        messages = client.get(f"/api/v1/conversations/{conversation_id}/messages").raise_for_status().json()
        cards = [item for item in messages if item.get("cardType") == "MUSIC_ANALYSIS"]
        if not cards:
            raise AssertionError("Creative-context research did not persist a MUSIC_ANALYSIS card")
        payload = json.loads(cards[-1]["cardPayloadJson"])
        coverage = payload.get("evidenceCoverage") or {}
        if coverage.get("requires_full_text") is not True:
            raise AssertionError(f"Creative-context request was not recognized as fact-heavy: {coverage}")
        if int(coverage.get("independent_domain_count") or 0) < 2:
            raise AssertionError(f"Research did not establish independent-source coverage: {coverage}")
        if int(coverage.get("full_text_source_count") or 0) < 1:
            raise AssertionError(f"Agent did not obtain a readable full-page excerpt: {coverage}")
        claims = payload.get("claims") or []
        claim_review = payload.get("claimReview") or {}
        if claim_review.get("semanticReviewApplied") is not True:
            raise AssertionError(f"Fact-heavy analysis skipped semantic claim review: {claim_review}")
        unsupported_facts = [
            item for item in claims
            if item.get("claimType") == "FACT" and item.get("status") != "unsupported" and not item.get("evidenceRefs")
        ]
        if unsupported_facts:
            raise AssertionError(f"Fact claims lack evidence references: {unsupported_facts}")
        assistant_text = max(
            (item.get("content") for item in messages if item.get("type") == "AGENT_TEXT" and isinstance(item.get("content"), str)),
            key=len, default="",
        )
        if len(assistant_text) < 700:
            raise AssertionError(f"Creative-context analysis remains too shallow: {len(assistant_text)} characters")
        event_text = json.dumps(events, ensure_ascii=False)
        if "read_source" not in event_text and "精读" not in event_text:
            raise AssertionError("Public run trace did not expose the autonomous source-reading action")
        print(json.dumps({
            "status": status, "conversationId": conversation_id, "runId": run_id,
            "analysisLength": len(assistant_text), "claimCount": len(claims),
            "sourceCount": len(payload.get("sources") or []),
            "independentDomains": coverage.get("independent_domain_count"),
            "fullTextSources": coverage.get("full_text_source_count"),
            "reviewedClaims": claim_review.get("reviewedClaimCount"),
            "verdictCounts": claim_review.get("verdictCounts"),
            "coveredDimensions": coverage.get("covered_dimensions"),
            "remainingDimensions": coverage.get("missing_dimensions"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
