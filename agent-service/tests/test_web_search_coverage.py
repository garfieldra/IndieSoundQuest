import pytest

from app.tools import WebSearchTool


@pytest.mark.asyncio
async def test_search_many_round_robins_results_across_artist_queries():
    tool = WebSearchTool()

    async def fake_search(query: str, purpose: str = "general"):
        artist = query.split()[0]
        return [
            {"sourceUrl": f"https://example.com/{artist}/{index}", "sourceTitle": f"{artist}-{index}", "searchQuery": query}
            for index in range(3)
        ]

    tool.search = fake_search
    results = await tool.search_many([
        {"query": "安溥 歌曲", "purpose": "explicit_artist_coverage"},
        {"query": "徐佳莹 歌曲", "purpose": "explicit_artist_coverage"},
        {"query": "艾怡良 歌曲", "purpose": "explicit_artist_coverage"},
    ])

    assert [item["sourceTitle"] for item in results[:6]] == [
        "安溥-0", "徐佳莹-0", "艾怡良-0", "安溥-1", "徐佳莹-1", "艾怡良-1",
    ]
