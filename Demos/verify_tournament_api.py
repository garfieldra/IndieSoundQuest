#!/usr/bin/env python3
"""端到端验证本地歌曲世界杯 API。

用法：
    python3 Demos/verify_tournament_api.py

服务需已运行在 http://127.0.0.1:8080。脚本会创建一场新的 16 首赛事，
自动完成 15 场投票，并验证封面、赛程、幂等投票和冠军结算。
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

    def request(self, method: str, path: str, body: dict | None = None, headers: dict[str, str] | None = None) -> dict:
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=payload, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} returned HTTP {error.code}: {details}") from error
        except URLError as error:
            raise RuntimeError(f"Cannot reach {self.base_url}: {error.reason}") from error


def verify(base_url: str) -> None:
    client = ApiClient(base_url.rstrip("/"))
    artists = client.request("GET", "/api/v1/artists")
    assert artists, "Expected at least one seed artist"

    tournament = client.request(
        "POST",
        "/api/v1/tournaments",
        {"artistId": artists[0]["id"], "size": 16},
    )
    tournament_id = tournament["id"]
    assert tournament["status"] == "DRAFT", tournament

    prepared = client.request("PATCH", f"/api/v1/tournaments/{tournament_id}", {"status": "PREPARED"})
    assert prepared["status"] == "READY", prepared
    detail = client.request("GET", f"/api/v1/tournaments/{tournament_id}")
    assert len(detail["entries"]) == 16, detail
    assert len(detail["matches"]) == 15, detail
    assert all(entry["coverStatus"] == "AVAILABLE" and entry["coverUrl"] for entry in detail["entries"]), detail["entries"]

    first_vote: tuple[str, str, str] | None = None
    while detail["currentMatch"] is not None:
        match = detail["currentMatch"]
        selected_entry_id = match["leftEntryId"]
        idempotency_key = f"api-smoke-{uuid.uuid4()}"
        result = client.request(
            "POST",
            f"/api/v1/tournament-matches/{match['id']}/votes",
            {"selectedEntryId": selected_entry_id},
            {"Idempotency-Key": idempotency_key},
        )
        assert result["tournamentId"] == tournament_id, result
        if first_vote is None:
            first_vote = (match["id"], selected_entry_id, idempotency_key)
        detail = client.request("GET", f"/api/v1/tournaments/{tournament_id}")

    assert detail["status"] == "COMPLETED", detail
    assert detail["completedVoteCount"] == 15, detail
    assert sum(match["status"] == "COMPLETED" for match in detail["matches"]) == 15, detail

    assert first_vote is not None
    replay_match_id, replay_entry_id, replay_key = first_vote
    replay = client.request(
        "POST",
        f"/api/v1/tournament-matches/{replay_match_id}/votes",
        {"selectedEntryId": replay_entry_id},
        {"Idempotency-Key": replay_key},
    )
    assert replay["status"] == "COMPLETED", replay
    after_replay = client.request("GET", f"/api/v1/tournaments/{tournament_id}")
    assert after_replay["completedVoteCount"] == 15, after_replay
    print(f"PASS: tournament {tournament_id} completed with 16 entries, 15 matches, covers and idempotent voting.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify IndieSoundQuest tournament API end to end")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    try:
        verify(args.base_url)
    except (AssertionError, RuntimeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
