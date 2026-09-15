import pytest

from app.tools import LastFmResearchTool, WikimediaResearchTool, _lastfm_sources


def test_lastfm_similar_tracks_become_noncanonical_discovery_sources():
    sources = _lastfm_sources({
        "similartracks": {
            "track": [{
                "name": "另一首歌",
                "artist": {"name": "另一位艺人"},
                "match": "0.82",
                "url": "https://www.last.fm/music/example/_/track",
            }]
        }
    }, "track.getSimilar", "起点艺人", "起点歌曲")

    assert len(sources) == 1
    assert sources[0]["title"] == "另一首歌"
    assert sources[0]["artistName"] == "另一位艺人"
    assert sources[0]["evidenceKind"] == "LISTENER_SIMILARITY_SIGNAL"
    assert sources[0]["searchProvider"] == "lastfm"


@pytest.mark.asyncio
async def test_lastfm_without_api_key_degrades_without_network():
    tool = LastFmResearchTool()
    assert tool.enabled is False
    assert await tool.discover(artist_name="安溥") == []


class FakeWikimediaResearchTool(WikimediaResearchTool):
    async def _search_wikipedia(self, query: str, language: str, limit: int):
        return [{
            "sourceUrl": "https://zh.wikipedia.org/wiki/test",
            "sourceTitle": "测试作品",
            "summary": "作品与艺人背景",
            "searchProvider": f"wikipedia-{language}",
        }]

    async def _search_wikidata(self, query: str, limit: int):
        return [{
            "sourceUrl": "https://www.wikidata.org/wiki/Q1",
            "sourceTitle": "测试作品 · Wikidata",
            "summary": "结构化实体",
            "searchProvider": "wikidata",
        }]


@pytest.mark.asyncio
async def test_wikimedia_search_merges_and_deduplicates_sources():
    sources = await FakeWikimediaResearchTool().search("测试作品")
    assert [item["searchProvider"] for item in sources] == ["wikipedia-zh", "wikidata"]
    assert len({item["sourceUrl"] for item in sources}) == 2
