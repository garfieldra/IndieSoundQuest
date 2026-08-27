import asyncio
import html
import ipaddress
import re
import socket
import time
from urllib.parse import urlparse
from uuid import UUID
import httpx
from .research import ResearchPurpose, ResearchSource, deduplicate_research_sources


class MusicCatalogTool:
    """只调用 Java 内部目录 API；不直连 MySQL 或自行创造歌曲实体。"""
    def __init__(self, base_url: str, token: str): self.base_url, self.token = base_url.rstrip("/"), token

    async def search_recordings(self, preference_text: str, seed_artist_ids: list[UUID]) -> list[dict]:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(f"{self.base_url}/internal/v1/music-catalog/search", json={"query": preference_text, "artistIds": [str(x) for x in seed_artist_ids], "limit": 80}, headers={"Authorization": f"Bearer {self.token}"})
            response.raise_for_status()
            return response.json()["items"]

    async def resolve_and_import(self, hints: list[dict]) -> list[dict]:
        if not hints:
            return []
        # A batch can contain 16 independent MusicBrainz resolutions.  The
        # authority asks clients to be polite about request rate, so the Java
        # service may correctly take longer than the old 50-second client
        # ceiling even though it is still making forward progress.
        async with httpx.AsyncClient(timeout=150) as client:
            response = await client.post(
                f"{self.base_url}/internal/v1/music-catalog/musicbrainz/resolve-and-import",
                json={"hints": hints[:16]},
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
            return response.json()["items"]

    async def resolve_artist_candidates(self, names: list[str]) -> list[dict]:
        if not names:
            return []
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.post(
                f"{self.base_url}/internal/v1/music-catalog/musicbrainz/resolve-artists",
                json={"names": names[:8]}, headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
            return response.json()["items"]

    async def discover_artist_recordings(self, artists: list[dict], per_artist_limit: int = 32) -> list[dict]:
        if not artists:
            return []
        async with httpx.AsyncClient(timeout=150) as client:
            response = await client.post(
                f"{self.base_url}/internal/v1/music-catalog/musicbrainz/discover-artists-and-import",
                json={"artists": artists[:8], "perArtistLimit": per_artist_limit},
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
            return response.json()["items"]


class WebSearchTool:
    MAX_EXCERPT_CHARS = 2_000
    MAX_PAGE_BYTES = 256_000

    def __init__(self, tavily_api_key: str | None = None, bocha_api_key: str | None = None):
        self.tavily_api_key = tavily_api_key
        self.bocha_api_key = bocha_api_key

    async def search(self, query: str, purpose: str = "general") -> list[dict]:
        # Chinese music discovery benefits from Bocha's domestic index.  The
        # international Tavily index remains available for English/global input
        # and as a fallback when the preferred provider is unavailable.
        if self.bocha_api_key and _contains_cjk(query):
            try:
                return await self._search_bocha(query, purpose)
            except httpx.HTTPError:
                pass
        if self.tavily_api_key:
            try:
                return await self._search_tavily(query, purpose)
            except httpx.HTTPError:
                pass
        if self.bocha_api_key:
            try:
                return await self._search_bocha(query, purpose)
            except httpx.HTTPError:
                pass
        return []

    async def _search_tavily(self, query: str, purpose: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.tavily_api_key}"},
                json={"query": query, "max_results": 5, "search_depth": "advanced"},
            )
            response.raise_for_status()
            return [{
                "kind": "web_source", "sourceUrl": item["url"], "sourceTitle": item["title"],
                "summary": item.get("content", "")[:1_200], "publishedDate": item.get("published_date"),
                "queryPurpose": purpose, "searchQuery": query,
            } for item in response.json().get("results", [])]

    async def _search_bocha(self, query: str, purpose: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.bochaai.com/v1/web-search",
                headers={"Authorization": f"Bearer {self.bocha_api_key}", "Content-Type": "application/json"},
                json={"query": query, "count": 10, "summary": True},
            )
            response.raise_for_status()
            # Bocha wraps the documented SearchResponse under `data`.
            payload = response.json()
            values = payload.get("data", payload).get("webPages", {}).get("value", [])
            return [{
                "kind": "web_source", "sourceUrl": item["url"], "sourceTitle": item.get("name", ""),
                "summary": (item.get("summary") or item.get("snippet") or "")[:1_200],
                "publishedDate": item.get("datePublished"), "queryPurpose": purpose, "searchQuery": query,
                "searchProvider": "bocha",
            } for item in values if item.get("url")]

    async def search_many(self, queries: list[dict]) -> list[dict]:
        unique: list[dict] = []
        seen: set[str] = set()
        for item in queries[:5]:
            query = str(item.get("query", "")).strip()
            if query and query not in seen:
                seen.add(query)
                unique.append({"query": query, "purpose": str(item.get("purpose") or "general")})
        groups = await asyncio.gather(
            *(self.search(item["query"], item["purpose"]) for item in unique),
            return_exceptions=True,
        )
        results: list[dict] = []
        for group in groups:
            if isinstance(group, list): results.extend(group)
        return _deduplicate_sources(results)

    async def enrich_public_sources(self, sources: list[dict], limit: int = 2) -> list[dict]:
        """Fetch a small, safe public excerpt only when Tavily snippets are insufficient."""
        enriched: list[dict] = []
        for source in sources[:limit]:
            url = source.get("sourceUrl", "")
            if not await _is_safe_public_url(url):
                continue
            try:
                async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                    async with client.stream("GET", url, headers={"User-Agent": "IndieSoundQuest/0.1 public-evidence-fetch"}) as response:
                        content_type = response.headers.get("content-type", "")
                        content_length = int(response.headers.get("content-length", "0") or 0)
                        if response.status_code != 200 or "html" not in content_type.lower() or content_length > self.MAX_PAGE_BYTES:
                            continue
                        chunks, total = [], 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > self.MAX_PAGE_BYTES:
                                chunks = []
                                break
                            chunks.append(chunk)
                if not chunks:
                    continue
                excerpt = _html_to_excerpt(b"".join(chunks).decode("utf-8", errors="replace"), self.MAX_EXCERPT_CHARS)
                if len(excerpt) >= 80:
                    enriched.append(source | {"pageExcerpt": excerpt})
            except (httpx.HTTPError, ValueError):
                continue
        return enriched


class SpotifyCatalogTool:
    """Official Spotify metadata search, used only as a discovery signal.

    The access token and client secret remain inside the Agent process. Returned
    tracks are deliberately shaped as unverified hints; downstream MusicBrainz
    resolution is still required before Java can create a tournament entry.
    """

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    SEARCH_URL = "https://api.spotify.com/v1/search"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, market: str | None = None):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.market = (market or "").strip().upper() or None
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        if not self.enabled or len(query.strip()) < 2:
            return []
        token = await self._access_token()
        params: dict[str, str | int] = {
            "q": query.strip()[:300], "type": "track,artist,album", "limit": max(1, min(limit, 10)),
        }
        if self.market:
            params["market"] = self.market
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(self.SEARCH_URL, params=params, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
        results: list[dict] = []
        for item in response.json().get("tracks", {}).get("items", []):
            title = str(item.get("name") or "").strip()
            artists = [str(artist.get("name") or "").strip() for artist in item.get("artists", [])]
            artist_name = ", ".join(value for value in artists if value)
            external_url = str((item.get("external_urls") or {}).get("spotify") or "").strip()
            if not title or not artist_name or not external_url.startswith("https://open.spotify.com/"):
                continue
            album = item.get("album") or {}
            image = next((entry.get("url") for entry in album.get("images", []) if isinstance(entry, dict) and entry.get("url")), None)
            results.append({
                "kind": "spotify_catalog_source", "sourceUrl": external_url,
                "sourceTitle": f"{title} · {artist_name}",
                "summary": f"Spotify 目录收录：{title} · {artist_name}"[:1_200],
                "queryPurpose": "candidate_discovery", "searchQuery": params["q"],
                "catalogProvider": "SPOTIFY", "spotifyTrackId": str(item.get("id") or ""),
                "isrc": str((item.get("external_ids") or {}).get("isrc") or ""),
                "albumTitle": str(album.get("name") or ""), "coverUrl": image,
                "title": title, "artistName": artist_name,
            })
        return _deduplicate_sources(results)

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expiry:
                return self._token
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(self.client_id, self.client_secret),
                )
                response.raise_for_status()
            payload = response.json()
            token = str(payload.get("access_token") or "")
            if not token:
                raise ValueError("Spotify token response has no access token")
            # Refresh early to avoid passing an almost-expired token to a search.
            self._token, self._token_expiry = token, time.monotonic() + max(30, int(payload.get("expires_in") or 3600) - 60)
            return token


class DomesticContentResearchTool:
    """Optional, adapter-only gateway for community research sidecars.

    The Agent cannot execute a platform CLI or forward arbitrary commands. A
    disabled provider performs no network call and simply contributes no
    evidence, allowing the normal online-first chain to continue.
    """
    def __init__(
        self, *, zhihu_enabled: bool = False, bilibili_enabled: bool = False,
        douban_enabled: bool = False, zhihu_base_url: str = "http://zhihu-research:8091",
        bilibili_base_url: str = "http://bilibili-research:8092", douban_base_url: str = "http://douban-research:8093",
        timeout_seconds: int = 12, max_sources: int = 8,
    ):
        self.providers = {
            "ZHIHU": {"enabled": zhihu_enabled, "baseUrl": zhihu_base_url.rstrip("/")},
            "BILIBILI": {"enabled": bilibili_enabled, "baseUrl": bilibili_base_url.rstrip("/")},
            # The provider stays disabled until its lookup-only sidecar has
            # passed a source/terms review; no direct scraping fallback exists.
            "DOUBAN": {"enabled": douban_enabled, "baseUrl": douban_base_url.rstrip("/")},
        }
        self.timeout_seconds = timeout_seconds
        self.max_sources = max(1, min(max_sources, 8))

    async def search(self, provider: str, query: str, purpose: ResearchPurpose, limit: int = 5) -> list[dict]:
        config = self.providers.get(provider)
        if not config or not config["enabled"] or not config["baseUrl"]:
            return []
        payload = {"query": query.strip()[:240], "purpose": purpose, "limit": min(max(1, limit), self.max_sources)}
        if len(payload["query"]) < 2:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{config['baseUrl']}/v1/research/search", json=payload)
                response.raise_for_status()
            parsed = [ResearchSource.model_validate(item) for item in response.json().get("items", [])]
            return [item.as_web_source() for item in deduplicate_research_sources(parsed)[:self.max_sources]]
        except (httpx.HTTPError, ValueError):
            return []

    def enabled_providers(self) -> list[str]:
        return [name for name, config in self.providers.items() if config["enabled"] and config["baseUrl"]]


def _deduplicate_sources(sources: list[dict]) -> list[dict]:
    unique: dict[str, dict] = {}
    for source in sources:
        url = source.get("sourceUrl")
        if url and url not in unique: unique[url] = source
    return list(unique.values())


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


async def _is_safe_public_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.port not in {None, 80, 443}:
        return False
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, None, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


def _html_to_excerpt(document: str, limit: int) -> str:
    without_noncontent = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", document)
    text = re.sub(r"(?s)<[^>]+>", " ", without_noncontent)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:limit]


class KnowledgeSearchTool:
    """Reads only the reviewed Milvus collection; experimental cards are never queried."""
    def __init__(self, uri: str, embedding_model: str = "BAAI/bge-small-zh-v1.5", collection_name: str = "isq_song_theme_cards_v1"):
        self.uri, self.embedding_model, self.collection_name = uri, embedding_model, collection_name
        self._store = None
    async def search_verified(self, query: str, recording_ids: list[str]) -> list[dict]:
        from .knowledge_store import ThemeCardKnowledgeStore
        if self._store is None: self._store = ThemeCardKnowledgeStore(self.uri, self.embedding_model, self.collection_name)
        try: return await asyncio.wait_for(asyncio.to_thread(self._store.search, query), timeout=12)
        except Exception: return []


class TournamentFactsTool:
    """读取 Java 已完成赛事事实；Agent 不直连数据库。"""
    def __init__(self, base_url: str, token: str):
        self.base_url, self.token = base_url.rstrip("/"), token

    async def get(self, tournament_id: UUID, guest_id: str) -> dict:
        async with httpx.AsyncClient(timeout=12, http1=True) as client:
            response = await client.get(
                f"{self.base_url}/internal/v1/tournaments/{tournament_id}/report-facts",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Guest-Session-Id": guest_id,
                },
            )
            response.raise_for_status()
            return response.json()
