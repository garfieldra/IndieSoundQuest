from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.conversation_graph import (
    ConversationDecision,
    ConversationReActRuntime,
    MusicRecommendationDraft,
    RecommendedArtistDraft,
    RecommendedSongDraft,
    _assess_research,
    _diverse_song_order,
    _draft_named_artist_coverage,
    _explicit_artist_mentions,
    _fallback_preference_profile,
    _next_analysis_query,
    _recommendation_search_queries,
    _select_evidence_backed_recording,
    _unique_artist_drafts,
    _unique_song_drafts,
)
from app.schemas import CandidateItem, CandidatePoolResult, ConversationAgentRequest
from app.schemas import ConversationResumeRequest
from app.music_analysis import EvidenceClaim, EvidenceClaimReview, MusicAnalysisDraft, MusicAnalysisPlan, MusicAnalysisReview, apply_claim_reviews, assess_analysis_evidence, classify_source_quality, deep_analysis_intent, inherited_analysis_sources, initial_analysis_workspace, sanitize_claims


class FakeWeb:
    async def search(self, query: str, purpose: str = "general"):
        return [{"sourceTitle": "公开资料", "sourceUrl": "https://example.com/music", "summary": "可验证的音乐资料"}]


class FakeKnowledge:
    async def search_verified(self, query: str, recording_ids: list[str]):
        return []


class _FailingStructuredCall:
    async def ainvoke(self, _prompt):
        raise ValueError("provider rejected function calling")


class FakePlainJsonFallbackModel:
    def with_structured_output(self, _schema, **_kwargs):
        return _FailingStructuredCall()

    async def ainvoke(self, _prompt):
        return SimpleNamespace(content='''```json
        {"core_judgment":"这是一个足够具体、边界清晰并且已经通过本地结构校验的核心判断。",
         "answer":"这是经过本地结构校验的深度分析正文。它不会因为 Provider 的函数调用兼容问题而丢失。" ,
         "claims":[{"statement":"公开资料只支持当前这项有限结论，不能外推更多幕后事实。","claim_type":"INTERPRETATION","dimension":"证据边界","evidence_refs":[],"confidence":"low","boundary":"仅用于验证 JSON 回退。","status":"partial"}],
         "uncertainties":[],"related_works":[]}
        ```'''.replace("深度分析正文。", "深度分析正文。" * 30))


def test_multi_artist_recommendation_builds_one_parallel_search_per_explicit_artist():
    text = "我最喜欢安溥、张悬、徐佳莹、郑宜农、艾怡良、TizzyBac等歌手，根据我的偏好推荐音乐。"
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage=text,
    )
    assert _explicit_artist_mentions(text) == ["安溥", "张悬", "徐佳莹", "郑宜农", "艾怡良", "TizzyBac"]
    queries = _recommendation_search_queries(request, text)
    assert len(queries) == 7
    assert [item["query"].split('"')[1] for item in queries[:6]] == ["安溥", "张悬", "徐佳莹", "郑宜农", "艾怡良", "TizzyBac"]


def test_single_artist_song_request_is_extracted_and_gets_anchored_search():
    text = "给我推荐几首asen的歌曲。"
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage=text,
    )
    assert _explicit_artist_mentions(text) == ["asen"]
    queries = _recommendation_search_queries(request, "alt-J 相似音乐")
    assert queries[0]["query"] == '"asen" 代表作 歌曲 专辑 曲目'
    assert queries[1]["query"] == "alt-J 相似音乐"


def test_scene_request_builds_structured_fallback_profile_and_research_gate():
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="推荐一些适合深夜写代码、克制但有节奏感的华语音乐。",
    )
    profile = _fallback_preference_profile(request, [])
    assert profile.scenes == ["深夜", "写代码"]
    assert profile.moods == ["克制"]
    assert profile.languages_regions == ["华语"]
    assessment = _assess_research(request, profile.model_dump(), [{"sourceUrl": f"https://example.com/{i}"} for i in range(5)])
    assert assessment["ready"] is False
    assessment = _assess_research(request, profile.model_dump(), [{"sourceUrl": f"https://example.com/{i}"} for i in range(6)])
    assert assessment["ready"] is True


def test_verified_song_order_preserves_artist_diversity_before_second_tracks():
    songs = [
        {"recordingId": "1", "artistName": "Tizzy Bac", "title": "A"},
        {"recordingId": "2", "artistName": "Tizzy Bac", "title": "B"},
        {"recordingId": "3", "artistName": "安溥", "title": "C"},
        {"recordingId": "4", "artistName": "徐佳莹", "title": "D"},
    ]
    ordered = _diverse_song_order(songs)
    assert [item["recordingId"] for item in ordered] == ["1", "3", "4", "2"]


def test_named_artist_coverage_requires_concrete_song_candidates():
    draft = MusicRecommendationDraft(
        summary="这是一段足够长的多艺人推荐摘要，用于确认艺人卡不能替代歌曲候选覆盖。",
        songs=[RecommendedSongDraft(title="测试歌曲", artist_name="安溥", reason="一段足够具体且长度合规的推荐理由", source_url="https://example.com/song")],
        artists=[RecommendedArtistDraft(artist_name="徐佳莹", reason="一段足够具体且长度合规的推荐理由", source_url="https://example.com/artist")],
    )
    assert _draft_named_artist_coverage(draft, ["安溥", "徐佳莹"]) == 1


def test_catalog_supplement_prefers_title_supported_by_artist_search_evidence():
    seed = {"mention": "徐佳莹", "name": "徐佳瑩"}
    candidates = [
        {"recordingId": "1", "title": "Hell", "artistName": "徐佳瑩"},
        {"recordingId": "2", "title": "失落沙洲", "artistName": "徐佳瑩", "albumTitle": "LaLa首张创作专辑"},
    ]
    sources = [{
        "searchQuery": '"徐佳莹" 代表作 歌曲 专辑 曲目',
        "sourceTitle": "徐佳莹代表作",
        "summary": "代表作品包括失落沙洲、身骑白马等歌曲。",
        "sourceUrl": "https://example.com/lala",
    }]
    selected, source_url = _select_evidence_backed_recording(seed, candidates, sources)
    assert selected["title"] == "失落沙洲"
    assert source_url == "https://example.com/lala"


def test_recommendation_deduplication_normalizes_song_and_artist_names():
    source = "https://example.com/music"
    songs = [RecommendedSongDraft(title="灰色", artist_name="徐佳瑩", reason="一段足够长的推荐理由", source_url=source), RecommendedSongDraft(title="灰 色", artist_name="徐 佳 瑩", reason="另一段足够长的推荐理由", source_url=source)]
    artists = [RecommendedArtistDraft(artist_name="魏如萱", reason="一段足够长的推荐理由", source_url=source), RecommendedArtistDraft(artist_name="魏 如 萱", reason="另一段足够长的推荐理由", source_url=source)]
    assert len(_unique_song_drafts(songs)) == 1
    assert len(_unique_artist_drafts(artists)) == 1


class FakeCandidateGraph:
    async def ainvoke(self, values, config):
        request = values["request"]
        recording_id = uuid4()
        return {
            "recordings": [{"id": str(recording_id), "title": "测试歌曲", "artistName": "测试艺人"}],
            "result": CandidatePoolResult(
                requestId=request.request_id, status="ready_for_confirmation", size=request.size,
                reserve_size=1, recording_ids=[recording_id], candidate_summary="测试候选池",
                items=[CandidateItem(recordingId=recording_id, reason="这是一条可展示的候选理由")],
                terminationReason="TARGET_REACHED_AND_VALIDATED",
            ),
        }


class FakeRecommendationModel:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return MusicRecommendationDraft.model_validate({
            "summary": "这些推荐沿着克制、夜行感与华语创作女声的方向展开，并保留公开资料边界。",
            "songs": [{
                "title": "测试歌曲", "artist_name": "测试艺人",
                "reason": "编曲留白和叙事语气适合夜晚步行时安静聆听",
                "source_url": "https://example.com/music",
            }],
            "artists": [{
                "artist_name": "测试艺人", "reason": "声音表达克制，并具有清晰的创作主体性",
                "source_url": "https://example.com/music",
            }],
        })


class FakeRecommendationCatalog:
    async def resolve_and_import(self, hints):
        assert hints[0]["title"] == "测试歌曲"
        return [{
            "status": "RESOLVED", "recordingId": str(uuid4()), "artistId": str(uuid4()),
            "title": "测试歌曲", "artistName": "测试艺人", "albumTitle": "测试专辑",
            "coverUrl": "https://example.com/cover.jpg",
        }]


class FakeRespondingRouter:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return ConversationDecision(action="respond", public_summary="直接整理文字回答")


class FakeTournamentRouter:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return ConversationDecision(action="propose_tournament", public_summary="错误地建议世界杯")


class FakeUnavailableLastFmRouter:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return ConversationDecision(
            action="search_lastfm", public_summary="查找相似艺人",
            artist_name="安溥", lastfm_mode="similar_artists",
        )


class FakeRepeatedMusicBrainzRouter:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return ConversationDecision(
            action="research_musicbrainz", public_summary="再次核对相同目录实体",
            artist_name="张悬", track_title="宝贝", entity_type="recording",
        )


class FakePrematureAnalysisRouter:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return ConversationDecision(action="analyze_music", public_summary="过早形成分析")


class FakeUnderSpecifiedAnalysisPlanner:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return MusicAnalysisPlan(
            subject="错误的新主题", resolved_question="继续分析上一首作品",
            selected_dimensions=["演唱表达"], open_questions=["还需要哪些证据？"],
        )


class FakeRevisionModel:
    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _prompt):
        return MusicRecommendationDraft.model_validate({
            "summary": "这一轮沿着更冷、更疏离的声音继续，并按要求替换上一轮已经出现的作品。",
            "songs": [
                {"title": "上一轮歌曲", "artist_name": "上一轮艺人", "reason": "用于验证重复歌曲会被结果护栏排除", "source_url": "https://example.com/previous"},
                {"title": "新的歌曲", "artist_name": "新的艺人", "reason": "更克制的编曲与疏离感符合这轮增量要求", "source_url": "https://example.com/music"},
            ],
            "artists": [{"artist_name": "新的艺人", "reason": "创作气质更冷静并保留足够旋律线索", "source_url": "https://example.com/music"}],
        })


class FakeRevisionCatalog:
    async def resolve_and_import(self, hints):
        assert [item["title"] for item in hints] == ["新的歌曲"]
        return [{
            "status": "RESOLVED", "recordingId": str(uuid4()), "artistId": str(uuid4()),
            "title": "新的歌曲", "artistName": "新的艺人", "albumTitle": "新专辑", "coverUrl": "",
        }]


class FakeDeepAnalysisAndReviewModel:
    def __init__(self):
        self.schema = None

    def with_structured_output(self, schema, **_kwargs):
        self.schema = schema
        return self

    async def ainvoke(self, _prompt):
        if self.schema is MusicAnalysisDraft:
            return MusicAnalysisDraft(
                core_judgment="这首作品的亲密感来自克制的人声距离与编曲留白共同作用。",
                answer="初稿通过公开访谈和作品资料区分事实与解释。" * 20,
                claims=[EvidenceClaim(
                    statement="公开资料记录了作品所属专辑与发行语境",
                    claim_type="FACT", dimension="创作与专辑语境",
                    evidence_refs=["S1"], confidence="high", status="supported",
                )],
            )
        if self.schema is MusicAnalysisReview:
            return MusicAnalysisReview(
                revised_answer="审校后仅保留来源能够支持的发行语境，并将声音效果明确标为分析解释。" * 16,
                items=[EvidenceClaimReview(
                    claim_index=0, verdict="partial", supported_refs=["S1"],
                    rationale="来源支持专辑归属，但没有完整覆盖所有创作语境",
                    revised_statement="公开资料能够支持作品的专辑归属，但更具体的创作语境仍需补充",
                    boundary="目前只核对到专辑归属。",
                )],
                review_summary="事实表述已按来源覆盖范围收窄。",
            )
        raise AssertionError(f"unexpected schema: {self.schema}")


async def run(text: str, candidate_graph=None):
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge(), candidate_graph=candidate_graph)
    runtime.model = None
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id,
        agentRunId=request_id,
        conversationId=uuid4(),
        guestId="test-guest",
        userMessage=text,
    )
    result = None
    async for state in runtime.graph.astream(
        {"request": request},
        {"configurable": {"thread_id": str(request_id)}, "recursion_limit": 24},
        stream_mode="values",
    ):
        result = state.get("result") or result
    return result


@pytest.mark.asyncio
async def test_clear_preference_produces_persistable_world_cup_intent():
    result = await run("我喜欢徐佳莹、艾怡良和郑宜农，想做一场适合深夜的歌曲世界杯")
    assert result is not None
    assert result.action == "propose_tournament"
    assert result.card_intent is not None
    assert result.card_intent.card_type == "WORLD_CUP_LAUNCH"
    assert result.card_intent.payload["defaultSize"] == 32


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "我喜欢徐佳莹、艾怡良和郑宜农，想了解她们的共同点和相近音乐。",
    "最近很喜欢华语独立女声，给我一些新的探索方向。",
    "想找适合夜晚散步、克制但不阴郁的独立流行。",
    "推荐16首适合通勤听的中文歌。",
    "我喜欢华语创作女声，但先不要开赛，只想聊聊她们的声音。",
    "我不想玩歌曲世界杯，只想正常聊音乐。",
])
async def test_ordinary_preference_and_recommendation_never_auto_propose_world_cup(text):
    result = await run(text)
    assert result is not None
    assert result.action not in {"propose_tournament", "build_candidate_pool"}
    assert result.card_intent is None or result.card_intent.card_type == "PUBLIC_MUSIC_SOURCES"


@pytest.mark.asyncio
async def test_ambiguous_short_request_becomes_resumable_general_clarification():
    result = await run("随便")
    assert result is not None
    assert result.action == "clarify"
    assert result.card_intent is not None
    assert result.card_intent.card_type == "GENERAL_CLARIFICATION"
    assert result.card_intent.payload["allowFreeText"] is True


@pytest.mark.asyncio
async def test_general_clarification_answer_resumes_same_run():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = None
    run_id = uuid4()
    original = ConversationAgentRequest(
        requestId=run_id, agentRunId=run_id, conversationId=uuid4(), guestId="guest",
        userMessage="随便", summary="用户正在寻找新的音乐起点。",
        recentMessages=[{"role": "USER", "content": "随便"}],
    )
    request = ConversationResumeRequest(
        requestId=run_id, agentRunId=run_id, conversationId=original.conversation_id,
        guestId="guest", resumeKind="GENERAL_CLARIFICATION",
        answer="我想听适合雨夜散步、克制但不阴郁的华语歌",
        originalRequest=original.model_dump(by_alias=True, mode="json"),
    )
    result = await runtime.resume(request)
    assert result.action == "recommend_music"
    assert result.memory_summary is None
    assert result.memory_summary_through_sequence is None


@pytest.mark.asyncio
async def test_medium_term_memory_incrementally_compresses_only_supplied_old_segment():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = None
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="不要太悲伤，保留一点律动", summary="用户喜欢夜晚散步时听的华语独立音乐。",
        recentMessages=[{"role": "USER", "content": "想要更冷一点"}],
        summaryThroughSequence=8,
        memoryCompressionMessages=[
            {"role": "USER", "content": "不喜欢过度煽情的编曲", "sequenceNumber": 9},
            {"role": "ASSISTANT", "content": "后续会保留克制感", "sequenceNumber": 10},
        ],
        memoryCompressionThroughSequence=10,
    )
    result = await run("普通音乐回答测试")
    summary = await runtime.build_memory_summary(request, result)
    assert "华语独立音乐" in summary
    assert "过度煽情" in summary
    assert "不要太悲伤" not in summary
    assert result.memory_summary_through_sequence == 10


@pytest.mark.asyncio
async def test_medium_term_memory_does_not_change_without_compression_segment():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = None
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="再推荐一些", summary="已有摘要", recentMessages=[],
        summaryThroughSequence=6,
    )
    result = await run("普通音乐回答测试")
    assert await runtime.build_memory_summary(request, result) is None
    assert result.memory_summary_through_sequence is None


@pytest.mark.asyncio
async def test_explicit_world_cup_interest_still_returns_optional_launch_card():
    result = await run("我想玩一场华语创作女声的歌曲世界杯，先聊聊这个形式。")
    assert result is not None
    assert result.action == "propose_tournament"
    assert result.card_intent is not None
    assert result.card_intent.card_type == "WORLD_CUP_LAUNCH"


@pytest.mark.asyncio
async def test_music_question_uses_research_without_forcing_world_cup_card():
    result = await run("请介绍一下周杰伦最近的音乐动态")
    assert result is not None
    assert result.action == "respond"
    assert result.card_intent is not None
    assert result.card_intent.message_type == "RECOMMENDATION_CARD"
    assert result.card_intent.card_type == "PUBLIC_MUSIC_SOURCES"
    assert result.card_intent.payload["items"][0]["url"] == "https://example.com/music"
    assert result.trace_summary["webSourceCount"] == 1


@pytest.mark.asyncio
async def test_ordinary_recommendation_searches_online_and_returns_source_card():
    result = await run("推荐一些适合夜晚散步、克制但不阴郁的华语歌曲")
    assert result is not None
    assert result.action == "recommend_music"
    assert result.card_intent is not None
    assert result.card_intent.card_type == "PUBLIC_MUSIC_SOURCES"
    assert "公开资料" in result.text


@pytest.mark.asyncio
async def test_recommendation_tool_verifies_songs_and_builds_persistable_card():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge(), catalog=FakeRecommendationCatalog())
    runtime.model = FakeRecommendationModel()
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="推荐适合夜晚散步的华语歌曲",
    )
    result = await runtime.recommend_music(request, await FakeWeb().search("test"))
    assert result.action == "recommend_music"
    assert result.card_intent is not None
    assert result.card_intent.card_type == "MUSIC_RECOMMENDATIONS"
    assert result.card_intent.payload["songs"][0]["verificationStatus"] == "MUSICBRAINZ_VERIFIED"
    assert result.card_intent.payload["songs"][0]["searchUrl"].startswith("https://music.163.com/")


@pytest.mark.asyncio
async def test_explicit_recommendation_routes_to_card_tool_after_web_evidence():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakeRespondingRouter()
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="请推荐5首适合深夜听的华语歌曲",
    )
    decision = await runtime.decide({
        "request": request, "action_history": [{"action": "search_web"}],
        "web_sources": await FakeWeb().search("test"), "knowledge": [], "iteration": 1,
    })
    assert decision.action == "recommend_music"


@pytest.mark.asyncio
async def test_recommendation_retries_empty_research_when_model_wobbles_to_tournament():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakeTournamentRouter()
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="推荐一些适合深夜写代码、克制但有节奏感的华语音乐。",
    )
    decision = await runtime.decide({
        "request": request, "action_history": [{"action": "search_web"}],
        "web_sources": [], "knowledge": [], "iteration": 1, "search_attempts": 1,
        "research_assessment": {"ready": False, "sourceCount": 0, "nextStep": "继续补充公开资料"},
    })
    assert decision.action == "search_web"


@pytest.mark.asyncio
async def test_unconfigured_lastfm_is_replanned_to_public_web_search():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakeUnavailableLastFmRouter()
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="找一些和安溥相似的艺人",
    )
    decision = await runtime.decide({
        "request": request, "action_history": [], "web_sources": [], "knowledge": [], "iteration": 0,
    })
    assert decision.action == "search_web"
    assert "未启用" in decision.public_summary


@pytest.mark.asyncio
async def test_musicbrainz_no_gain_loop_is_replanned_instead_of_repeated():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge(), catalog=FakeRecommendationCatalog())
    runtime.model = FakeRepeatedMusicBrainzRouter()
    request = _analysis_request("请分析张悬《宝贝》的制作人和发行背景。")
    workspace = initial_analysis_workspace(request)
    decision = await runtime.decide({
        "request": request,
        "action_history": [{"action": "research_musicbrainz"}],
        "observations": [{"action": "research_musicbrainz", "newOutputCount": 0}],
        "web_sources": [{"sourceUrl": "https://musicbrainz.org/recording/1"}],
        "knowledge": [], "iteration": 2, "search_attempts": 1,
        "analysis_workspace": workspace.model_dump(),
        "evidence_coverage": {
            "blocking_gaps": ["仍缺少创作背景来源"],
            "suggested_queries": ["宝贝 创作背景 采访"],
        },
    })
    assert decision.action == "search_web"
    assert decision.query == "宝贝 创作背景 采访"


@pytest.mark.asyncio
async def test_short_followup_uses_persisted_recommendation_context_and_researches_again():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakeRespondingRouter()
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="更冷一点，换一批，不要重复",
        recentCards=[{
            "messageId": str(uuid4()), "messageType": "RECOMMENDATION_CARD", "cardType": "MUSIC_RECOMMENDATIONS",
            "payload": {"songs": [{"title": "测试歌曲", "artistName": "测试艺人", "sourceUrl": "https://example.com/music"}], "artists": []},
        }],
    )
    first = await runtime.decide({"request": request, "action_history": [], "web_sources": [], "knowledge": [], "iteration": 0})
    assert first.action == "search_web"
    second = await runtime.decide({
        "request": request, "action_history": [{"action": "search_web"}],
        "web_sources": await FakeWeb().search("test"), "knowledge": [], "iteration": 1,
    })
    assert second.action == "recommend_music"


@pytest.mark.asyncio
async def test_fresh_set_followup_excludes_previous_song_and_links_revision_card():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge(), catalog=FakeRevisionCatalog())
    runtime.model = FakeRevisionModel()
    previous_id = str(uuid4())
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="更冷一点，换一批，不要重复",
        recentCards=[{
            "messageId": previous_id, "messageType": "RECOMMENDATION_CARD", "cardType": "MUSIC_RECOMMENDATIONS",
            "payload": {"songs": [{"title": "上一轮歌曲", "artistName": "上一轮艺人", "sourceUrl": "https://example.com/previous"}], "artists": []},
        }],
    )
    result = await runtime.recommend_music(request, await FakeWeb().search("test"))
    assert result.card_intent is not None
    payload = result.card_intent.payload
    assert [item["title"] for item in payload["songs"]] == ["新的歌曲"]
    assert payload["contextMode"] == "REFINED"
    assert payload["basedOnCardId"] == previous_id


@pytest.mark.asyncio
async def test_explicit_start_invokes_candidate_pool_composite_tool_and_returns_persistable_card():
    result = await run("我喜欢徐佳莹和艾怡良，现在直接开赛，生成32首候选歌曲。", FakeCandidateGraph())
    assert result is not None
    assert result.action == "build_candidate_pool"
    assert result.card_intent is not None
    assert result.card_intent.message_type == "CANDIDATE_POOL_CARD"
    assert result.card_intent.card_type == "CANDIDATE_POOL"
    assert result.card_intent.payload["items"][0]["title"] == "测试歌曲"


@pytest.mark.asyncio
async def test_direct_report_request_returns_persistable_exploration_report_card():
    result = await run("请根据我们刚才的对话，给我一份音乐偏好探索报告。")
    assert result is not None
    assert result.action == "generate_exploration_report"
    assert result.card_intent is not None
    assert result.card_intent.message_type == "REPORT_CARD"
    assert result.card_intent.card_type == "EXPLORATION_REPORT"
    assert result.card_intent.payload["summary"]
    assert result.card_intent.payload.get("claims")


@pytest.mark.asyncio
async def test_explicit_start_honors_16_song_request_size():
    result = await run("我喜欢独立流行，现在直接开赛，生成16首候选歌曲。", FakeCandidateGraph())
    assert result is not None
    assert result.action == "build_candidate_pool"
    assert result.card_intent is not None
    assert result.card_intent.payload["size"] == 16


def _analysis_request(text: str, recent_cards: list[dict] | None = None) -> ConversationAgentRequest:
    request_id = uuid4()
    return ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage=text, recentCards=recent_cards or [],
    )


def test_deep_analysis_selects_only_question_relevant_dimensions():
    request = _analysis_request("为什么张悬《宝贝》听起来很亲密？请从演唱和编曲留白具体分析。")
    workspace = initial_analysis_workspace(request)
    assert deep_analysis_intent(request) is True
    assert workspace.subject == "宝贝"
    assert "演唱表达" in workspace.selected_dimensions
    assert "编曲与音色" in workspace.selected_dimensions
    assert "节奏与律动" not in workspace.selected_dimensions


def test_preference_report_request_does_not_load_deep_analysis_skill():
    request = _analysis_request("请分析一下我的音乐偏好，并生成一份探索报告。")
    assert deep_analysis_intent(request) is False


def test_terse_followup_continues_persisted_music_analysis_context():
    request = _analysis_request("具体体现在哪里？", [{
        "messageId": str(uuid4()), "messageType": "RECOMMENDATION_CARD", "cardType": "MUSIC_ANALYSIS",
        "payload": {"coreJudgment": "人声距离感是亲密感的重要来源", "selectedDimensions": ["演唱表达"]},
    }])
    assert deep_analysis_intent(request) is True


def test_followup_inherits_subject_claims_sources_and_revision_from_analysis_card():
    previous_id = str(uuid4())
    request = _analysis_request("重点展开演唱距离感，依据是什么？", [{
        "messageId": previous_id, "messageType": "RECOMMENDATION_CARD", "cardType": "MUSIC_ANALYSIS",
        "payload": {
            "subject": "张悬《宝贝》", "question": "为什么《宝贝》听起来亲密？",
            "coreJudgment": "亲密感来自克制的人声距离与编曲留白。",
            "selectedDimensions": ["编曲与音色"], "workspaceRevision": 4,
            "claims": [{
                "statement": "人声距离与留白共同构成较私密的听觉关系。",
                "claimType": "INTERPRETATION", "dimension": "编曲与音色",
                "evidenceRefs": ["S1"], "confidence": "medium", "status": "partial",
            }],
            "sources": [{
                "ref": "S1", "title": "公开访谈", "url": "https://example.com/interview",
                "summary": "访谈讨论了这首作品的录音与表达。", "provider": "bocha",
            }],
        },
    }])
    workspace = initial_analysis_workspace(request)
    assert workspace.subject == "张悬《宝贝》"
    assert workspace.revision == 5
    assert workspace.working_thesis.startswith("亲密感")
    assert workspace.claims[0].evidence_refs == ["S1"]
    assert "本轮追问" in workspace.question
    assert workspace.selected_dimensions == ["演唱表达"]
    sources = inherited_analysis_sources(request)
    assert sources[0]["sourceUrl"] == "https://example.com/interview"
    assert sources[0]["inheritedFromAnalysisCard"] is True


def test_analysis_gap_query_skips_queries_already_attempted():
    request = _analysis_request("创作背景还有哪些可靠依据？")
    state = {
        "analysis_workspace": {"subject": "张悬《宝贝》", "open_questions": ["制作人如何描述录音过程？"]},
        "evidence_coverage": {"suggested_queries": ["宝贝 创作背景 采访", "宝贝 制作人 credits"]},
        "analysis_queries": ["宝贝 创作背景 采访"],
    }
    assert _next_analysis_query(state, request) == "宝贝 制作人 credits"


@pytest.mark.asyncio
async def test_model_workspace_revision_cannot_drop_explicit_followup_dimensions_or_subject():
    request = _analysis_request("继续比较录音室版和现场版。", [{
        "messageId": str(uuid4()), "messageType": "RECOMMENDATION_CARD", "cardType": "MUSIC_ANALYSIS",
        "payload": {"subject": "张悬《宝贝》", "selectedDimensions": ["编曲与音色"], "workspaceRevision": 2},
    }])
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakeUnderSpecifiedAnalysisPlanner()
    workspace = await runtime.plan_analysis_workspace(request)
    assert workspace.subject == "张悬《宝贝》"
    assert "横向比较" in workspace.selected_dimensions
    assert "版本与身份" in workspace.selected_dimensions


def test_fact_claim_without_known_source_is_demoted_instead_of_presented_as_fact():
    claim = EvidenceClaim(
        statement="这首歌在某年由某位制作人完成录音",
        claim_type="FACT", dimension="创作与专辑语境", evidence_refs=["S99"], confidence="high",
        status="supported",
    )
    sanitized = sanitize_claims([claim], {"S1"})[0]
    assert sanitized.evidence_refs == []
    assert sanitized.status == "unsupported"
    assert sanitized.confidence == "low"
    assert sanitized.boundary


def test_source_quality_distinguishes_catalog_full_page_and_search_snippet():
    catalog = classify_source_quality({"sourceUrl": "https://musicbrainz.org/recording/1"})
    full_page = classify_source_quality({
        "sourceUrl": "https://example.com/interview", "sourceTitle": "制作人专访",
        "pageExcerpt": "这是一段足够长的制作与录音访谈正文。" * 12,
    })
    snippet = classify_source_quality({"sourceUrl": "https://example.net/result", "summary": "简短摘要"})
    assert catalog["tier"] == "A"
    assert full_page["tier"] == "B"
    assert snippet["tier"] == "D"


def test_semantic_claim_review_rejects_unknown_refs_and_caps_weak_source_confidence():
    claims = [EvidenceClaim(
        statement="公开采访明确说明了这首歌的录音安排",
        claim_type="FACT", dimension="制作与空间", evidence_refs=["S1"], confidence="high", status="supported",
    )]
    unsupported, meta = apply_claim_reviews(
        claims,
        [EvidenceClaimReview(
            claim_index=0, verdict="supported", supported_refs=["S99"],
            rationale="给出的引用不存在，不能支持事实",
        )],
        {"S1"}, {"S1": {"score": 0.38}},
    )
    assert unsupported[0].status == "unsupported"
    assert unsupported[0].confidence == "low"
    assert unsupported[0].evidence_refs == []
    assert meta["semanticReviewApplied"] is True

    supported, _ = apply_claim_reviews(
        claims,
        [EvidenceClaimReview(
            claim_index=0, verdict="supported", supported_refs=["S1"],
            rationale="摘要可以部分核对录音安排",
        )],
        {"S1"}, {"S1": {"score": 0.38}},
    )
    assert supported[0].status == "supported"
    assert supported[0].confidence == "medium"


def test_creation_background_requires_full_text_and_independent_source_coverage():
    request = _analysis_request("请深入分析张悬《宝贝》的创作背景、制作过程和编曲表达。")
    workspace = initial_analysis_workspace(request)
    shallow = assess_analysis_evidence(workspace, [{
        "sourceUrl": "https://example.com/a", "sourceTitle": "宝贝创作背景",
        "summary": "这是一篇关于专辑与创作背景的简短搜索摘要。",
    }])
    assert shallow.requires_full_text is True
    assert shallow.ready_for_fact_heavy_answer is False
    assert shallow.full_text_source_count == 0
    deep = assess_analysis_evidence(workspace, [
        {"sourceUrl": "https://example.com/a", "sourceTitle": "制作访谈", "summary": "创作与制作",
         "pageExcerpt": "制作人和歌手在采访中讨论了歌曲的创作、录音、编曲与人声表达。" * 8},
        {"sourceUrl": "https://artist.example.org/b", "sourceTitle": "专辑资料", "summary": "专辑发行与词曲资料"},
    ])
    assert deep.full_text_source_count == 1
    assert deep.independent_domain_count == 2
    assert deep.ready_for_fact_heavy_answer is True


@pytest.mark.asyncio
async def test_fact_heavy_analysis_cannot_skip_available_source_reading():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakePrematureAnalysisRouter()
    request = _analysis_request("请深入分析张悬《宝贝》的创作背景和制作过程。")
    decision = await runtime.decide({
        "request": request,
        "action_history": [{"action": "search_web"}],
        "web_sources": [{"sourceUrl": "https://example.com/a", "sourceTitle": "创作背景", "summary": "简短摘要"}],
        "knowledge": [], "iteration": 1, "search_attempts": 1,
        "analysis_workspace": initial_analysis_workspace(request).model_dump(),
        "evidence_coverage": {
            "blocking_gaps": ["尚未精读能够支持创作或制作事实的原始页面"],
            "suggested_queries": ["宝贝 创作背景 采访"],
        },
        "read_source_refs": [],
    })
    assert decision.action == "read_source"
    assert decision.source_ref == "S1"


@pytest.mark.asyncio
async def test_deep_music_question_researches_then_returns_persistable_analysis_card():
    result = await run("为什么张悬《宝贝》听起来很亲密？请具体分析演唱距离感和编曲留白。")
    assert result is not None
    assert result.action == "analyze_music"
    assert result.card_intent is not None
    assert result.card_intent.card_type == "MUSIC_ANALYSIS"
    assert result.card_intent.payload["sources"][0]["url"] == "https://example.com/music"
    assert "演唱表达" in result.card_intent.payload["selectedDimensions"]
    assert result.trace_summary["skill"] == "deep_music_analysis"
    assert result.trace_summary["webSourceCount"] == 1


@pytest.mark.asyncio
async def test_analysis_uses_same_model_for_conditional_semantic_claim_review():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakeDeepAnalysisAndReviewModel()
    request = _analysis_request("请深入分析《测试歌曲》的创作背景和演唱表达。")
    workspace = initial_analysis_workspace(request)
    result = await runtime.analyze_music({
        "request": request, "analysis_workspace": workspace.model_dump(),
        "web_sources": [{
            "sourceUrl": "https://musicbrainz.org/recording/test", "sourceTitle": "作品资料",
            "summary": "作品所属专辑与发行资料",
            "pageExcerpt": "公开资料记录了作品所属专辑与发行语境。" * 10,
        }],
        "knowledge": [], "action_history": [{"action": "search_web"}, {"action": "read_source"}],
        "evidence_coverage": {"requires_full_text": True, "full_text_source_count": 1},
    })
    assert result.trace_summary["semanticReviewApplied"] is True
    assert result.card_intent is not None
    assert result.card_intent.payload["claimReview"]["reviewedClaimCount"] == 1
    assert result.card_intent.payload["claims"][0]["status"] == "partial"
    assert "审校后" in result.text


@pytest.mark.asyncio
async def test_validated_invocation_falls_back_to_plain_json_for_provider_compatibility():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = FakePlainJsonFallbackModel()
    draft = await runtime._invoke_validated(MusicAnalysisDraft, "生成分析")
    assert isinstance(draft, MusicAnalysisDraft)
    assert draft.claims[0].claim_type == "INTERPRETATION"


@pytest.mark.asyncio
async def test_runtime_intervention_is_applied_before_next_supervisor_decision():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = None
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="请介绍一下安溥的音乐。",
    )
    calls = []

    async def provider(after_sequence: int):
        calls.append(after_sequence)
        return [{"sequenceNumber": 1, "type": "ADJUST_DIRECTION", "content": "重点谈创作背景，不要推荐歌曲。"}] if after_sequence < 1 else []

    final_state = {}
    async for value in runtime.graph.astream(
        {"request": request},
        {"configurable": {"thread_id": str(request_id), "intervention_provider": provider}, "recursion_limit": 24},
        stream_mode="values",
    ):
        final_state = value
    assert final_state["request"].active_interventions == ["重点谈创作背景，不要推荐歌曲。"]
    assert final_state["applied_intervention_sequence"] == 1
    assert any(item["action"] == "adjust_direction" for item in final_state["action_history"])
    assert final_state["result"].trace_summary["appliedInterventionSequence"] == 1
    assert calls[0] == 0


@pytest.mark.asyncio
async def test_runtime_intervention_mailbox_failure_is_not_silently_ignored():
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = None
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id, agentRunId=request_id, conversationId=uuid4(), guestId="guest",
        userMessage="请介绍一下安溥的音乐。",
    )

    async def unavailable_provider(_after_sequence: int):
        raise RuntimeError("mailbox unavailable")

    with pytest.raises(RuntimeError, match="mailbox unavailable"):
        async for _ in runtime.graph.astream(
            {"request": request},
            {"configurable": {"thread_id": str(request_id), "intervention_provider": unavailable_provider}},
            stream_mode="values",
        ):
            pass
