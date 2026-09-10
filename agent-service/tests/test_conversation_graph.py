from uuid import uuid4

import pytest

from app.conversation_graph import (
    ConversationDecision,
    ConversationReActRuntime,
    MusicRecommendationDraft,
    RecommendedArtistDraft,
    RecommendedSongDraft,
    _unique_artist_drafts,
    _unique_song_drafts,
)
from app.schemas import CandidateItem, CandidatePoolResult, ConversationAgentRequest


class FakeWeb:
    async def search(self, query: str, purpose: str = "general"):
        return [{"sourceTitle": "公开资料", "sourceUrl": "https://example.com/music", "summary": "可验证的音乐资料"}]


class FakeKnowledge:
    async def search_verified(self, query: str, recording_ids: list[str]):
        return []


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
