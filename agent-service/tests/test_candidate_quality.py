from uuid import uuid4

import pytest

from app.graph import _candidate_allowed, _hint_evidence_is_observed, _normalize_decision_reason, _validate_candidate_quality, build_candidate_pool_graph
from app.evaluation import aggregate_metrics, evaluate_candidate_result
from app.llm import CandidateDecision, DeepSeekCandidateSelector, DiscoveryHint, RerankedCandidates, _literal_artist_mentions, classify_intent_locally
from app.schemas import CandidatePoolRequest
from app.tools import _html_to_excerpt, _is_safe_public_url


class FakeCatalog:
    def __init__(self, items):
        self.items = items
        self.artist_filters = []

    async def search_recordings(self, _preference, artist_ids):
        self.artist_filters.append(list(artist_ids))
        return self.items

    async def resolve_and_import(self, _hints):
        return []

    async def resolve_artist_candidates(self, names):
        return []

    async def discover_artist_recordings(self, _artists, _per_artist_limit=32):
        return []


class FakeWeb:
    def __init__(self):
        self.calls = 0

    async def search(self, _query):
        self.calls += 1
        return []

    async def search_many(self, queries):
        self.calls += len(queries)
        return []

    async def enrich_public_sources(self, _sources, limit=2):
        return []


class FakeKnowledge:
    async def search_verified(self, *_args):
        return []


def recordings(artist_id, artist_name, count):
    return [
        {
            "id": str(uuid4()), "title": f"{artist_name} Song {index}",
            "artistId": str(artist_id), "artistName": artist_name,
            "albumTitle": "Album", "coverStatus": "AVAILABLE",
        }
        for index in range(count)
    ]


@pytest.mark.parametrize(
    ("text", "has_seed", "expected"),
    [
        ("只玩张悬的歌曲世界杯，不要其他歌手", True, "ARTIST_LOCKED"),
        ("我喜欢张悬，想找相近的中文独立音乐", True, "ARTIST_SEEDED"),
        ("克制、温柔，适合夜晚散步的中文独立音乐", False, "OPEN_DISCOVERY"),
        ("从安溥开始探索一些相似作品", True, "ARTIST_SEEDED"),
    ],
)
def test_local_intent_policy(text, has_seed, expected):
    seeds = [uuid4()] if has_seed else []
    policy = classify_intent_locally(text, seeds)
    assert policy.intent_mode == expected
    assert policy.seed_artist_ids == seeds
    assert bool(policy.allowed_artist_ids == seeds and seeds) is (expected == "ARTIST_LOCKED")


def test_literal_artist_extraction_handles_unspaced_chinese_conjunctions():
    assert _literal_artist_mentions(
        "我喜欢徐佳莹、艾怡良和郑宜农，想探索适合深夜聆听的华语创作女声。"
    ) == ["徐佳莹", "艾怡良", "郑宜农"]


@pytest.mark.asyncio
async def test_locked_mode_filters_other_artists_and_keeps_short_reserve_safe():
    allowed, other = uuid4(), uuid4()
    catalog = FakeCatalog(recordings(allowed, "Allowed", 16) + recordings(other, "Other", 16))
    selector = DeepSeekCandidateSelector()
    selector.model = None
    graph = build_candidate_pool_graph(catalog, FakeWeb(), FakeKnowledge(), selector)
    request = CandidatePoolRequest(
        requestId=uuid4(), guestId="fixture", size=16,
        preferenceText="只玩这个歌手的个人歌曲世界杯，不要其他歌手",
        seedArtistIds=[allowed],
    )

    state = await graph.ainvoke({"request": request})

    assert state["intent_policy"].intent_mode == "ARTIST_LOCKED"
    assert catalog.artist_filters[0] == [allowed]
    assert state["result"].status == "insufficient_candidates"
    assert len(state["result"].recording_ids) == 16
    assert state["result"].termination_reason == "INSUFFICIENT_VERIFIED_CANDIDATES"
    assert all(item["artistId"] == str(allowed) for item in state["ranked"])


@pytest.mark.asyncio
async def test_ambiguous_named_artist_blocks_generation_until_confirmation():
    class AmbiguousCatalog(FakeCatalog):
        async def resolve_artist_candidates(self, names):
            assert names == ["Asen"]
            return [{"mention": "Asen", "reason": None, "candidates": [
                {"mbid": str(uuid4()), "name": "Asen", "score": 100, "country": "CN"},
                {"mbid": str(uuid4()), "name": "Asen", "score": 98, "country": "US"},
            ]}]

    class AmbiguousSelector(DeepSeekCandidateSelector):
        async def extract_named_artists(self, _preference):
            return ["Asen"]

    selector = AmbiguousSelector(); selector.model = None
    graph = build_candidate_pool_graph(AmbiguousCatalog([]), FakeWeb(), FakeKnowledge(), selector)
    state = await graph.ainvoke({"request": CandidatePoolRequest(
        requestId=uuid4(), guestId="fixture", size=16, preferenceText="我喜欢 Asen 的歌",
    )})

    assert state["result"].status == "needs_clarification"
    assert state["result"].clarifications[0]["mention"] == "Asen"
    assert "search_web" not in [item["action"] for item in state["action_history"]]


@pytest.mark.asyncio
async def test_seeded_mode_searches_open_catalog_instead_of_locking_seed():
    seed, other = uuid4(), uuid4()
    catalog = FakeCatalog(recordings(seed, "Seed", 16) + recordings(other, "Other", 16))
    selector = DeepSeekCandidateSelector()
    selector.model = None
    graph = build_candidate_pool_graph(catalog, FakeWeb(), FakeKnowledge(), selector)
    request = CandidatePoolRequest(
        requestId=uuid4(), guestId="fixture", size=16,
        preferenceText="我喜欢这个歌手，也想找一些相近的音乐",
        seedArtistIds=[seed],
    )

    state = await graph.ainvoke({"request": request})

    assert state["intent_policy"].intent_mode == "ARTIST_SEEDED"
    assert catalog.artist_filters[0] == []
    assert len(state["result"].recording_ids) == 32
    assert state["result"].termination_reason == "TARGET_REACHED_AND_VALIDATED"


def test_explicit_lock_is_not_overridden_by_model_enrichment():
    seed = uuid4()
    policy = classify_intent_locally("仅限这位艺人的作品", [seed])
    assert policy.intent_mode == "ARTIST_LOCKED"
    assert policy.allowed_artist_ids == [seed]


def test_resolved_artist_id_cannot_be_widened_by_model_artist_name():
    seed, other = uuid4(), uuid4()
    policy = classify_intent_locally("仅限这位艺人的作品", [seed])
    policy.allowed_artist_names = ["Other"]

    assert _candidate_allowed({"artistId": str(seed), "artistName": "Seed"}, policy)
    assert not _candidate_allowed({"artistId": str(other), "artistName": "Other"}, policy)


def test_deterministic_evaluator_rejects_unverified_hint_and_budget_overrun():
    verified, leaked = uuid4(), uuid4()
    case = {"caseId": "gate", "input": {"size": 16}, "expected": {}}
    result = {
        "status": "ready_for_confirmation",
        "recordingIds": [str(verified), str(leaked)] + [str(uuid4()) for _ in range(14)],
        "items": [
            {"recordingId": str(verified), "reason": "适合这次夜晚散步的克制氛围"},
            {"recordingId": str(leaked), "reason": "符合你的偏好", "trustState": "DISCOVERY_HINT"},
        ],
        "traceSummary": {"toolCalls": 11},
    }
    catalog = {str(verified): {"recordingId": str(verified), "artistId": str(uuid4())}}

    evaluated = evaluate_candidate_result(case, result, catalog)

    assert evaluated["unresolvedHintLeakCount"] > 0
    assert evaluated["toolBudgetViolationCount"] == 1
    assert not evaluated["hardGatePassed"]
    assert aggregate_metrics([evaluated])["hardGatePassedRate"] == 0


@pytest.mark.asyncio
async def test_open_discovery_attempts_expansion_when_catalog_is_single_artist():
    artist = uuid4()
    web = FakeWeb()
    selector = DeepSeekCandidateSelector()
    selector.model = None
    graph = build_candidate_pool_graph(FakeCatalog(recordings(artist, "中文独立音乐", 32)), web, FakeKnowledge(), selector)
    request = CandidatePoolRequest(
        requestId=uuid4(), guestId="fixture", size=16,
        preferenceText="克制温柔、适合夜晚散步的中文独立音乐",
    )

    state = await graph.ainvoke({"request": request})

    assert state["intent_policy"].intent_mode == "OPEN_DISCOVERY"
    # A ReAct search turn may fan out into several independently chosen
    # queries; the contract is that online expansion is attempted at least once.
    assert web.calls >= 1
    assert state["result"].status == "ready_for_confirmation"


@pytest.mark.asyncio
async def test_locked_mode_does_not_search_web_when_verified_scope_is_already_full():
    allowed, other = uuid4(), uuid4()
    web = FakeWeb()
    selector = DeepSeekCandidateSelector()
    selector.model = None
    graph = build_candidate_pool_graph(
        FakeCatalog(recordings(allowed, "Allowed", 32) + recordings(other, "Other", 8)), web, FakeKnowledge(), selector,
    )
    request = CandidatePoolRequest(
        requestId=uuid4(), guestId="fixture", size=16,
        preferenceText="仅限这个歌手的作品，不要其他艺人",
        seedArtistIds=[allowed],
    )

    state = await graph.ainvoke({"request": request})

    assert state["result"].status == "ready_for_confirmation"
    assert web.calls == 0


def test_action_reason_code_is_semantically_normalized():
    decision = CandidateDecision(
        action="search_catalog", reason_code="intent_policy_missing",
        decision_summary="读取目录",
    )
    assert _normalize_decision_reason(decision).reason_code == "verified_candidates_below_active_size"


@pytest.mark.asyncio
async def test_rerank_contract_preserves_pool_and_exposes_explanation_metadata():
    artist = uuid4()

    class RankingSelector(DeepSeekCandidateSelector):
        async def rerank_candidates(self, _preference, items, _policy):
            ranked = []
            for index, item in enumerate(reversed(items)):
                ranked.append(item | {
                    "rankScore": 100 - index,
                    "reason": f"《{item['title']}》与用户描述的深夜华语创作女声方向形成可比较的已核验作品。",
                    "rankingReason": f"《{item['title']}》与用户描述的深夜华语创作女声方向形成可比较的已核验作品。",
                    "selectionFactors": [{"kind": "mood", "text": "根据用户明确的深夜聆听场景进行排序。"}],
                    "explanationStatus": "MODEL_GENERATED",
                })
            return RerankedCandidates(items=ranked, candidate_summary="已完成语义重排", model_batch_count=2)

    selector = RankingSelector(); selector.model = None
    graph = build_candidate_pool_graph(FakeCatalog(recordings(artist, "华语创作女声", 32)), FakeWeb(), FakeKnowledge(), selector)
    state = await graph.ainvoke({"request": CandidatePoolRequest(
        requestId=uuid4(), guestId="fixture", size=16, preferenceText="深夜聆听的华语创作女声"
    )})

    result = state["result"]
    assert result.status == "ready_for_confirmation"
    assert len(result.items) == 32
    assert all(item.ranking_reason and item.selection_factors for item in result.items)
    assert all(item.explanation_status == "MODEL_GENERATED" for item in result.items)
    assert [item.pool_role for item in result.items[:16]] == ["MAIN"] * 16
    assert [item.pool_role for item in result.items[16:]] == ["RESERVE"] * 16


def test_quality_gate_blocks_short_active_pool_and_warns_short_reserve():
    artist = uuid4()
    request = CandidatePoolRequest(requestId=uuid4(), guestId="fixture", size=16, preferenceText="夜晚散步的中文独立音乐")
    state = {"request": request, "recordings": [item | {"trustState": "CATALOG_IMPORTED", "reason": "与夜晚散步的克制氛围相符"} for item in recordings(artist, "Artist", 18)]}
    gate = _validate_candidate_quality(state)
    assert gate["passed"]
    assert any(item["code"] == "RESERVE_CANDIDATES_INSUFFICIENT" for item in gate["issues"])
    state["recordings"] = state["recordings"][:15]
    gate = _validate_candidate_quality(state)
    assert not gate["passed"]
    assert any(item["code"] == "ACTIVE_COUNT_INSUFFICIENT" for item in gate["issues"])


def test_song_hint_requires_exact_source_and_observed_evidence():
    sources = [{
        "sourceUrl": "https://example.com/list", "sourceTitle": "夜晚散步歌单",
        "summary": "张悬的《宝贝》适合夜晚散步时聆听。", "queryPurpose": "find_curated_song_lists",
    }]
    valid = DiscoveryHint(
        title="宝贝", artist_name="张悬", source_url="https://example.com/list",
        evidence_snippet="张悬的《宝贝》适合夜晚散步时聆听。",
    )
    invented_url = valid.model_copy(update={"source_url": "https://unknown.example/song"})

    assert _hint_evidence_is_observed(valid, sources)
    assert not _hint_evidence_is_observed(invented_url, sources)


@pytest.mark.asyncio
async def test_public_excerpt_rejects_private_network_and_strips_html():
    assert not await _is_safe_public_url("http://127.0.0.1/private")
    excerpt = _html_to_excerpt("<html><script>secret()</script><p>张悬《宝贝》</p></html>", 200)
    assert "secret" not in excerpt
    assert "张悬《宝贝》" in excerpt
