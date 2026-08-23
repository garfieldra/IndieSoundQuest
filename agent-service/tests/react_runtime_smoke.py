"""Offline smoke tests for the two Supervisor ReAct graphs; no external data or API calls."""

import asyncio
from uuid import UUID, uuid4

from app.graph import build_candidate_pool_graph
from app.report_graph import build_report_graph
from app.report_llm import ReportGenerator
from app.report_schemas import TournamentReportRequest
from app.schemas import CandidatePoolRequest


class FakeCatalog:
    def __init__(self, count: int):
        self.items = [
            {"id": str(uuid4()), "title": f"Song {index}", "artistId": str(uuid4()),
             "artistName": "Artist", "albumTitle": "Album", "coverStatus": "AVAILABLE"}
            for index in range(count)
        ]

    async def search_recordings(self, *_):
        return self.items

    async def resolve_and_import(self, _):
        return []


class FakeWeb:
    async def search(self, _):
        return []


class FakeKnowledge:
    async def search_verified(self, *_):
        return []


class CandidateSupervisor:
    def __init__(self):
        self.calls = 0

    async def infer_intent(self, preference, seed_artist_ids):
        from app.llm import classify_intent_locally
        return classify_intent_locally(preference, seed_artist_ids)

    async def decide(self, _preference, _target, _count, summary):
        from app.llm import CandidateDecision
        self.calls += 1
        if not summary["intentPolicyReady"]:
            return CandidateDecision(action="understand_preference", reason_code="intent_policy_missing", decision_summary="offline intent decision")
        if not summary["catalogSearched"]:
            return CandidateDecision(action="search_catalog", reason_code="verified_candidates_below_active_size", decision_summary="offline catalog decision")
        if not summary["ranked"]:
            return CandidateDecision(action="rerank_candidates", reason_code="candidate_pool_requires_rerank", decision_summary="offline rank decision")
        return CandidateDecision(action="submit_candidates", reason_code="candidate_pool_ready_for_validation", decision_summary="offline submit decision")

    async def select(self, *_):
        return None

    async def extract_discovery_hints(self, *_):
        return []


class FakeFacts:
    async def get(self, tournament_id, _guest_id):
        entries = []
        for index in range(16):
            entry_id = uuid4()
            entries.append({"entryId": str(entry_id), "recordingId": str(uuid4()), "artistId": str(uuid4()),
                            "title": f"Song {index}", "artistName": "Artist", "albumTitle": "Album"})
        matches = [
            {"matchId": str(uuid4()), "roundNumber": 1, "leftEntryId": entries[index]["entryId"],
             "rightEntryId": entries[index + 1]["entryId"], "winnerEntryId": entries[index]["entryId"]}
            for index in range(0, 16, 2)
        ]
        return {"tournamentId": str(tournament_id), "size": 16, "entries": entries, "matches": matches}


async def main():
    selector = CandidateSupervisor()
    candidate_graph = build_candidate_pool_graph(FakeCatalog(32), FakeWeb(), FakeKnowledge(), selector)
    candidate_request = CandidatePoolRequest(
        requestId=uuid4(), guestId="offline", size=16, preferenceText="offline test preference"
    )
    candidate_state = await candidate_graph.ainvoke({"request": candidate_request})
    assert candidate_state["result"].status == "ready_for_confirmation"
    assert len(candidate_state["result"].recording_ids) == 32
    assert [item["action"] for item in candidate_state["action_history"]] == [
        "understand_preference", "search_catalog", "rerank_candidates", "submit_candidates"
    ]

    generator = ReportGenerator()
    generator.model = None
    report_graph = build_report_graph(FakeFacts(), generator, FakeWeb(), FakeKnowledge())
    report_request = TournamentReportRequest(
        requestId=uuid4(), reportId=uuid4(), tournamentId=uuid4(), guestId="offline",
        tournamentVersion=1, includePersonalityEasterEgg=True,
    )
    report_state = await report_graph.ainvoke({"request": report_request})
    assert "error_code" not in report_state
    assert report_state["result"].schema_version == "1.0"
    actions = [item["action"] for item in report_state["action_history"]]
    assert actions[0] == "analyze_tournament"
    assert "draft_report" in actions and "critique_report" in actions and actions[-1] == "submit_report"
    print({"candidateActions": candidate_state["action_history"], "reportActions": report_state["action_history"]})


if __name__ == "__main__":
    asyncio.run(main())
