#!/usr/bin/env python3
"""Exercise the live candidate-pool ReAct endpoint and assert knowledge use.

Run inside the agent Docker network. The script prints only the returned public
trace summary and never prints the internal service credential.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://agent-service:8000")
    parser.add_argument("--size", type=int, choices=(16, 32), default=16)
    parser.add_argument(
        "--preference",
        default="我想探索城市迷惘、深夜独处与温柔失恋交织的中文独立音乐；请充分利用已有主题知识进行跨艺人探索。",
    )
    args = parser.parse_args()
    token = os.environ.get("AGENT_INTERNAL_SERVICE_TOKEN")
    if not token:
        raise SystemExit("AGENT_INTERNAL_SERVICE_TOKEN is required")
    request_id = uuid.uuid4()
    body = json.dumps({
        "requestId": str(request_id), "guestId": "knowledge-integration-demo",
        "size": args.size, "candidateCount": args.size * 2,
        "preferenceText": args.preference, "seedArtistIds": [], "excludeRecordingIds": [],
    }, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{args.base_url.rstrip('/')}/internal/v1/workflows/candidate-pool:stream",
        data=body,
        headers={
            "Authorization": f"Bearer {token}", "X-Request-Id": str(request_id),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=150) as response:
        payload = response.read().decode("utf-8")
    result_line = next((line[6:] for line in payload.splitlines() if line.startswith("data: {") and '"status"' in line), None)
    if result_line is None:
        raise SystemExit("candidate-pool endpoint returned no result event")
    result = json.loads(result_line)
    trace = result.get("traceSummary", {})
    verified = "search_knowledge" in trace.get("actions", [])
    print(json.dumps({
        "requestId": str(request_id), "status": result.get("status"),
        "candidateCount": len(result.get("recordingIds", [])),
        "knowledgeSearched": verified, "actions": trace.get("actions", []),
        "terminationReason": result.get("terminationReason"),
    }, ensure_ascii=False))
    if not verified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
