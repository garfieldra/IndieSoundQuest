from uuid import UUID
import httpx


class MusicCatalogTool:
    """只调用 Java 内部目录 API；不直连 MySQL 或自行创造歌曲实体。"""
    def __init__(self, base_url: str, token: str): self.base_url, self.token = base_url.rstrip("/"), token

    async def search_recordings(self, preference_text: str, seed_artist_ids: list[UUID]) -> list[dict]:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(f"{self.base_url}/internal/v1/music-catalog/search", json={"query": preference_text, "artistIds": [str(x) for x in seed_artist_ids], "limit": 80}, headers={"Authorization": f"Bearer {self.token}"})
            response.raise_for_status()
            return response.json()["items"]


class WebSearchTool:
    def __init__(self, api_key: str | None): self.api_key = api_key
    async def search(self, query: str) -> list[dict]:
        if not self.api_key: return []
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://api.tavily.com/search", json={"api_key": self.api_key, "query": query, "max_results": 3, "search_depth": "basic"})
            response.raise_for_status()
            return [{"kind": "web_source", "sourceUrl": item["url"], "sourceTitle": item["title"]} for item in response.json().get("results", [])]


class KnowledgeSearchTool:
    """Milvus 适配边界。知识库未初始化时返回空结果，由 Graph 安全降级。"""
    def __init__(self, uri: str): self.uri = uri
    async def search_verified(self, query: str, recording_ids: list[str]) -> list[dict]:
        # MVP 尚未导入 reviewed Claim 与 embedding；保留显式降级而非伪造检索结果。
        return []
