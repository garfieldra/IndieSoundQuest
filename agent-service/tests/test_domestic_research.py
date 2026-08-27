import httpx
import pytest

from app.research import ResearchSource, deduplicate_research_sources
from app.tools import DomesticContentResearchTool


def source(**changes):
    base = {
        "provider": "ZHIHU", "sourceType": "ANSWER", "sourceUrl": "https://www.zhihu.com/question/123/answer/456",
        "title": "一段音乐讨论", "snippet": "这是一段长度足够的、来自公开知乎内容的音乐讨论摘录。",
        "queryPurpose": "cultural_context",
    }
    return ResearchSource.model_validate(base | changes)


def test_research_source_enforces_provider_url_and_bounded_safe_metadata():
    parsed = source(title="  一段\x00  音乐讨论  ", platformMetadata={"answerId": "456", "cookie": "must-drop"})
    assert parsed.title == "一段 音乐讨论"
    assert parsed.platform_metadata == {"answerId": "456"}
    with pytest.raises(ValueError, match="belonging to its provider"):
        source(sourceUrl="https://www.douban.com/music/1")
    with pytest.raises(ValueError, match="sourceType"):
        source(sourceType="VIDEO")


def test_research_source_deduplicates_by_provider_and_url():
    assert len(deduplicate_research_sources([source(), source(title="重复来源")])) == 1


@pytest.mark.asyncio
async def test_disabled_provider_is_a_no_network_noop():
    tool = DomesticContentResearchTool(zhihu_enabled=False)
    assert await tool.search("ZHIHU", "华语独立音乐推荐", "candidate_discovery") == []


@pytest.mark.asyncio
async def test_enabled_provider_projects_only_validated_sources(monkeypatch):
    response = {"items": [source().model_dump(mode="json", by_alias=True)]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, *_args, **_kwargs):
            return httpx.Response(200, json=response, request=httpx.Request("POST", "http://sidecar/v1/research/search"))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    tool = DomesticContentResearchTool(zhihu_enabled=True, zhihu_base_url="http://sidecar")
    results = await tool.search("ZHIHU", "华语独立音乐推荐", "candidate_discovery")
    assert results == [{
        "kind": "domestic_research_source", "sourceUrl": "https://www.zhihu.com/question/123/answer/456",
        "sourceTitle": "一段音乐讨论", "summary": "这是一段长度足够的、来自公开知乎内容的音乐讨论摘录。",
        "queryPurpose": "cultural_context", "researchProvider": "ZHIHU", "researchSourceType": "ANSWER", "contentConfidence": "LOW",
    }]
