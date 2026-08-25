#!/usr/bin/env python3
"""Exercise the live Candidate Pool Agent's public rerank contract.

Run from Docker's internal network.  The script prints only aggregate counts;
it never prints the service token, prompts, model output, or raw search pages.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from urllib.request import Request, urlopen


def main() -> None:
    request_id = uuid.uuid4()
    body = {
        "requestId": str(request_id),
        "guestId": "rerank-contract-demo",
        "size": 32,
        "candidateCount": 64,
        "preferenceText": "我喜欢徐佳莹、艾怡良和郑宜农，想探索适合深夜聆听的华语创作女声，也允许扩展相近艺人。",
        "seedArtistIds": [],
        "excludeRecordingIds": [],
    }
    token = os.environ.get("AGENT_INTERNAL_SERVICE_TOKEN")
    if not token:
        raise SystemExit("AGENT_INTERNAL_SERVICE_TOKEN is required")
    request = Request(
        "http://agent-service:8000/internal/v1/workflows/candidate-pool:stream",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "X-Request-Id": str(request_id),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=900) as response:
        payload = response.read().decode("utf-8")
    lines = payload.splitlines()
    result_line = next((lines[index + 1][6:] for index, line in enumerate(lines[:-1]) if line == "event: result" and lines[index + 1].startswith("data: ")), None)
    if not result_line:
        raise SystemExit("candidate-pool endpoint returned no result event")
    result = json.loads(result_line)
    items = result.get("items", [])
    main_items = [item for item in items if item.get("poolRole") == "MAIN"]
    reserve_items = [item for item in items if item.get("poolRole") == "RESERVE"]
    statuses = {item.get("explanationStatus") for item in items}
    valid = (
        result.get("status") == "ready_for_confirmation"
        and len(items) == 64 and len(main_items) == 32 and len(reserve_items) == 32
        and all(item.get("rankingReason") and item.get("selectionFactors") for item in items)
        and statuses <= {"MODEL_GENERATED", "CATALOG_FALLBACK"}
    )
    print(json.dumps({
        "status": result.get("status"), "candidateCount": len(items),
        "mainCount": len(main_items), "reserveCount": len(reserve_items),
        "explanationStatuses": sorted(status for status in statuses if status),
        "rerankObservations": [entry for entry in result.get("traceSummary", {}).get("actions", []) if entry == "rerank_candidates"],
        "passed": valid,
    }, ensure_ascii=False))
    if not valid:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
