import pytest

from app.tools import WebSearchTool, _decode_document, _html_to_excerpt, _is_safe_public_url


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


def test_public_page_excerpt_removes_scripts_and_markup():
    excerpt = _html_to_excerpt(
        "<html><style>secret{}</style><script>token='noise'</script><main>创作人谈歌曲的录音与编曲。&nbsp;保留正文。</main></html>",
        200,
    )
    assert "创作人谈歌曲" in excerpt
    assert "secret" not in excerpt
    assert "token" not in excerpt
    assert "<main>" not in excerpt


def test_public_page_reader_decodes_declared_chinese_charset():
    assert _decode_document("创作背景".encode("gb18030"), "text/html; charset=gb18030") == "创作背景"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private",
    "http://localhost/internal",
    "http://169.254.169.254/latest/meta-data",
    "https://user:password@example.com/music",
    "file:///etc/passwd",
])
async def test_public_page_reader_rejects_private_or_credentialed_targets(url):
    assert await _is_safe_public_url(url) is False
