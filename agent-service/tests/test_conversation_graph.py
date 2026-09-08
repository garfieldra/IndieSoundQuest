from uuid import uuid4

import pytest

from app.conversation_graph import ConversationReActRuntime
from app.schemas import CandidateItem, CandidatePoolResult, ConversationAgentRequest


class FakeWeb:
    async def search(self, query: str, purpose: str = "general"):
        return [{"sourceTitle": "公开资料", "sourceUrl": "https://example.com/music", "summary": "可验证的音乐资料"}]


class FakeKnowledge:
    async def search_verified(self, query: str, recording_ids: list[str]):
        return []


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
async def test_music_question_uses_research_without_forcing_world_cup_card():
    result = await run("请介绍一下周杰伦最近的音乐动态")
    assert result is not None
    assert result.action == "respond"
    assert result.card_intent is None
    assert result.trace_summary["webSourceCount"] == 1


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
