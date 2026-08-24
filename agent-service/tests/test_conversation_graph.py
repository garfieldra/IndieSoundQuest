from uuid import uuid4

import pytest

from app.conversation_graph import ConversationReActRuntime
from app.schemas import ConversationAgentRequest


class FakeWeb:
    async def search(self, query: str, purpose: str = "general"):
        return [{"sourceTitle": "公开资料", "sourceUrl": "https://example.com/music", "summary": "可验证的音乐资料"}]


class FakeKnowledge:
    async def search_verified(self, query: str, recording_ids: list[str]):
        return []


async def run(text: str):
    runtime = ConversationReActRuntime(FakeWeb(), FakeKnowledge())
    runtime.model = None
    request_id = uuid4()
    request = ConversationAgentRequest(
        requestId=request_id,
        agentRunId=request_id,
        conversationId=uuid4(),
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
