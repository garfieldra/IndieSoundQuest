#!/usr/bin/env python3
"""Run the live report ReAct graph and print a safe, compact verification trace."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent-service"))

from app.main import report_graph
from app.report_schemas import TournamentReportRequest


async def run(tournament_id: str, guest_id: str) -> None:
    request = TournamentReportRequest(
        request_id=uuid4(), report_id=uuid4(), tournament_id=tournament_id,
        guest_id=guest_id, tournament_version=1,
    )
    state = await report_graph.ainvoke({"request": request})
    report = state.get("result")
    if report is None or state.get("error_code"):
        raise SystemExit(f"report graph failed: {state.get('error_code', 'NO_RESULT')}")
    board = state["board"]
    output = {
        "actions": [item["action"] for item in state.get("action_history", [])],
        "networkSourceCount": len(board.get("network_sources", [])),
        "knowledgeContextCount": len(board.get("knowledge_context", [])),
        "explorationTags": report.exploration_tags,
        "songRecommendationSources": [item.source_status for item in report.song_recommendations],
        "artistRecommendationSources": [item.source_status for item in report.artist_recommendations],
        "networkSources": [
            {"title": item.get("sourceTitle", ""), "summary": item.get("summary", "")[:240]}
            for item in board.get("network_sources", [])
        ],
    }
    print(json.dumps(output, ensure_ascii=False))
    if "search_web" not in output["actions"]:
        raise SystemExit("network discovery was not selected for this cross-artist report")
    # Knowledge retrieval is an optional enhancement: a report remains valid
    # when the ReAct supervisor finds the tournament facts and web evidence
    # sufficient. The trace above makes its per-run decision observable.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tournament-id", required=True)
    parser.add_argument("--guest-id", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.tournament_id, args.guest_id))


if __name__ == "__main__":
    main()
