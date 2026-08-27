#!/usr/bin/env python3
"""Run the public, guest-scoped IndieSoundQuest journey end to end.

The script deliberately uses the browser-facing Java API instead of calling the
Python graphs directly:

  preference -> streamed candidate Agent -> agent-generated tournament -> votes
  -> streamed report Agent -> persisted report read-back.

It retains the guest cookie for the whole journey, so ownership checks are part
of the acceptance test as well. It defaults to a disposable 32-song tournament
(and accepts ``--size 16`` for a quicker smoke run), then performs every
deterministic vote needed to finish the bracket.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


@dataclass
class ApiClient:
    base_url: str

    def __post_init__(self) -> None:
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def request(self, method: str, path: str, body: dict | None = None, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[dict, str]:
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=payload, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw), raw
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} returned HTTP {error.code}: {details}") from error
        except URLError as error:
            raise RuntimeError(f"Cannot reach {self.base_url}: {error.reason}") from error

    def sse(self, path: str, body: dict, headers: dict[str, str], timeout: int = 900) -> list[tuple[str, dict]]:
        request_headers = {"Accept": "text/event-stream", "Content-Type": "application/json", **headers}
        request = Request(
            f"{self.base_url}{path}", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=request_headers, method="POST"
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"POST {path} returned HTTP {error.code}: {details}") from error
        except URLError as error:
            raise RuntimeError(f"Cannot reach {self.base_url}: {error.reason}") from error

        events: list[tuple[str, dict]] = []
        event_name: str | None = None
        for line in raw.splitlines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: ") and event_name:
                try:
                    events.append((event_name, json.loads(line[6:])))
                except json.JSONDecodeError as error:
                    raise RuntimeError(f"Malformed SSE payload for {event_name}: {line[6:300]}") from error
                event_name = None
        return events


def require_result(events: list[tuple[str, dict]], stage: str) -> dict:
    errors = [payload for event, payload in events if event == "error"]
    if errors:
        raise RuntimeError(f"{stage} streamed error: {errors[-1]}")
    results = [payload for event, payload in events if event == "result"]
    if not results:
        raise RuntimeError(f"{stage} returned no result event; events={[event for event, _ in events]}")
    return results[-1]


def verify(base_url: str, preference: str, size: int) -> None:
    client = ApiClient(base_url.rstrip("/"))
    request_id = uuid.uuid4()

    # The welcome conversation is an actual product artifact; the same guest
    # cookie must remain valid when its tournament/report are created.
    conversation, _ = client.request("POST", "/api/v1/conversations")
    assert conversation.get("id"), conversation

    candidate_events = client.sse(
        "/api/v1/agent-runs/candidate-pool:stream",
        {"size": size, "preferenceText": preference, "seedArtistIds": [], "confirmedArtists": []},
        {"X-Request-Id": str(request_id)},
    )
    pool = require_result(candidate_events, "candidate Agent")
    assert pool["status"] == "ready_for_confirmation", pool
    candidates = pool["candidatePool"]
    recording_ids = candidates["recordingIds"]
    assert len(recording_ids) >= size, candidates
    assert len(set(recording_ids)) == len(recording_ids), candidates
    assert any(event in {"progress", "plan_updated"} for event, _ in candidate_events), candidate_events

    tournament, _ = client.request(
        "POST", "/api/v1/tournaments",
        {
            "size": size,
            "candidateSource": "AGENT_GENERATED",
            "recordingIds": recording_ids[:size],
            "explorationBrief": candidates.get("candidateSummary", preference),
        },
        {"Idempotency-Key": str(uuid.uuid4())},
    )
    tournament_id = tournament["id"]
    assert tournament["status"] == "DRAFT", tournament
    assert tournament["candidateSource"] == "AGENT_GENERATED", tournament

    prepared, _ = client.request("PATCH", f"/api/v1/tournaments/{tournament_id}", {"status": "PREPARED"})
    assert prepared["status"] == "READY", prepared
    detail, _ = client.request("GET", f"/api/v1/tournaments/{tournament_id}")
    assert len(detail["entries"]) == size and len(detail["matches"]) == size - 1, detail

    # A deterministic side is enough to validate actual state transitions;
    # preference quality is intentionally not asserted by an automated run.
    while detail["currentMatch"] is not None:
        match = detail["currentMatch"]
        vote, _ = client.request(
            "POST", f"/api/v1/tournament-matches/{match['id']}/votes",
            {"selectedEntryId": match["leftEntryId"]},
            {"Idempotency-Key": str(uuid.uuid4())},
        )
        assert vote["tournamentId"] == tournament_id, vote
        detail, _ = client.request("GET", f"/api/v1/tournaments/{tournament_id}")

    assert detail["status"] == "COMPLETED", detail
    assert detail["completedVoteCount"] == size - 1, detail

    report_events = client.sse(
        f"/api/v1/tournaments/{tournament_id}/preference-report:stream",
        {"force": True},
        {},
    )
    report_view = require_result(report_events, "report Agent")
    assert report_view["status"] == "READY", report_view
    report = report_view.get("report") or {}
    assert report.get("songRecommendations"), report
    assert report.get("artistRecommendations"), report
    assert any(event in {"progress", "plan_updated"} for event, _ in report_events), report_events

    persisted, _ = client.request("GET", f"/api/v1/tournaments/{tournament_id}/preference-report")
    assert persisted["status"] == "READY", persisted
    assert persisted.get("report", {}).get("songRecommendations"), persisted
    messages, _ = client.request("GET", f"/api/v1/conversations/{conversation['id']}/messages")
    assert messages, "new conversation did not retain its welcome message"

    print(json.dumps({
        "status": "PASS",
        "conversationId": conversation["id"],
        "tournamentId": tournament_id,
        "candidateCount": len(recording_ids),
        "candidateProgressEvents": sum(1 for event, _ in candidate_events if event in {"progress", "plan_updated"}),
        "size": size,
        "completedVotes": detail["completedVoteCount"],
        "reportId": report_view["reportId"],
        "reportProgressEvents": sum(1 for event, _ in report_events if event in {"progress", "plan_updated"}),
        "recommendationCounts": {"songs": len(report["songRecommendations"]), "artists": len(report["artistRecommendations"])},
    }, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the complete IndieSoundQuest public user journey")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--size", type=int, choices=(16, 32), default=32)
    parser.add_argument("--preference", default="我喜欢徐佳莹、艾怡良与郑宜农，想从锋利的女性创作、都市情绪和真诚的抒情里开始一场歌曲世界杯。")
    args = parser.parse_args()
    try:
        verify(args.base_url, args.preference, args.size)
    except (AssertionError, RuntimeError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
