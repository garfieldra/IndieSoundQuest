import asyncio
import html
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse
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

    async def research_musicbrainz(
        self, query: str, entity_type: str = "recording", mbid: str | None = None,
        artist_name: str | None = None, title: str | None = None,
    ) -> list[dict]:
        """Read credits, versions and relationships through the Java catalog boundary."""
        payload = {
            "query": query.strip()[:300],
            "entityType": entity_type,
            "mbid": (mbid or "").strip() or None,
            "artistName": (artist_name or "").strip()[:180] or None,
            "title": (title or "").strip()[:180] or None,
        }
        async with httpx.AsyncClient(timeout=35) as client:
            response = await client.post(
                f"{self.base_url}/internal/v1/music-catalog/musicbrainz/research",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
            return list(response.json().get("items", []))


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
        for item in queries[:8]:
            query = str(item.get("query", "")).strip()
            if query and query not in seen:
                seen.add(query)
                unique.append({"query": query, "purpose": str(item.get("purpose") or "general")})
        groups = await asyncio.gather(
            *(self.search(item["query"], item["purpose"]) for item in unique),
            return_exceptions=True,
        )
        # Preserve coverage across independent search angles.  Concatenating
        # provider results made the first artist consume the entire downstream
        # evidence window even when later searches had succeeded.
        successful = [group for group in groups if isinstance(group, list)]
        results: list[dict] = []
        for index in range(max((len(group) for group in successful), default=0)):
            for group in successful:
                if index < len(group):
                    results.append(group[index])
        return _deduplicate_sources(results)

    async def enrich_public_sources(self, sources: list[dict], limit: int = 2) -> list[dict]:
        """Fetch bounded public excerpts, validating every redirect against SSRF."""
        enriched: list[dict] = []
        for source in sources[:limit]:
            url = source.get("sourceUrl", "")
            try:
                async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                    current_url = url
                    chunks: list[bytes] = []
                    content_type = ""
                    for _ in range(4):
                        if not await _is_safe_public_url(current_url):
                            break
                        async with client.stream(
                            "GET", current_url,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; IndieSoundQuest/0.1; public evidence reader)"},
                        ) as response:
                            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                                current_url = urljoin(current_url, response.headers["location"])
                                continue
                            content_type = response.headers.get("content-type", "")
                            content_length = int(response.headers.get("content-length", "0") or 0)
                            if response.status_code != 200 or "html" not in content_type.lower() or content_length > self.MAX_PAGE_BYTES:
                                break
                            total = 0
                            async for chunk in response.aiter_bytes():
                                total += len(chunk)
                                if total > self.MAX_PAGE_BYTES:
                                    chunks = []
                                    break
                                chunks.append(chunk)
                            break
                if not chunks:
                    continue
                excerpt = _html_to_excerpt(_decode_document(b"".join(chunks), content_type), self.MAX_EXCERPT_CHARS)
                if len(excerpt) >= 80:
                    enriched.append(source | {"pageExcerpt": excerpt, "resolvedSourceUrl": current_url})
            except (httpx.HTTPError, ValueError):
                continue
        return enriched


class WikimediaResearchTool:
    """Read-only artist/work context from Wikimedia's public APIs."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def search(self, query: str, limit: int = 4) -> list[dict]:
        if not self.enabled or len(query.strip()) < 2:
            return []
        languages = ["zh"]
        if not _contains_cjk(query):
            languages.append("en")
        groups = await asyncio.gather(
            *(self._search_wikipedia(query, language, limit) for language in languages),
            self._search_wikidata(query, limit),
            return_exceptions=True,
        )
        merged: list[dict] = []
        for group in groups:
            if isinstance(group, list):
                merged.extend(group)
        return _deduplicate_sources(merged)[: max(1, min(limit * 2, 8))]

    async def _search_wikipedia(self, query: str, language: str, limit: int) -> list[dict]:
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "generator": "search", "gsrsearch": query.strip()[:240],
            "gsrlimit": max(1, min(limit, 5)), "prop": "extracts|info",
            "exintro": "1", "explaintext": "1", "inprop": "url",
        }
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(endpoint, params=params, headers={"User-Agent": _research_user_agent()})
            response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        return [{
            "kind": "wikimedia_source", "sourceUrl": item.get("fullurl"),
            "sourceTitle": item.get("title", ""),
            "summary": str(item.get("extract") or "")[:1_800],
            "queryPurpose": "music_context", "searchQuery": query,
            "searchProvider": f"wikipedia-{language}", "evidenceKind": "ENCYCLOPEDIA_CONTEXT",
        } for item in pages if item.get("fullurl") and item.get("title")]

    async def _search_wikidata(self, query: str, limit: int) -> list[dict]:
        params = {
            "action": "wbsearchentities", "format": "json", "language": "zh", "uselang": "zh",
            "type": "item", "limit": max(1, min(limit, 5)), "search": query.strip()[:240],
        }
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(
                "https://www.wikidata.org/w/api.php", params=params,
                headers={"User-Agent": _research_user_agent()},
            )
            response.raise_for_status()
        return [{
            "kind": "wikidata_entity", "sourceUrl": item.get("concepturi"),
            "sourceTitle": f"{item.get('label', '')} · Wikidata",
            "summary": str(item.get("description") or item.get("match", {}).get("text") or "")[:1_200],
            "queryPurpose": "entity_disambiguation", "searchQuery": query,
            "searchProvider": "wikidata", "wikidataId": item.get("id"),
            "evidenceKind": "STRUCTURED_ENTITY_CONTEXT",
        } for item in response.json().get("search", []) if item.get("concepturi")]


class LastFmResearchTool:
    """Optional Last.fm discovery signals; never treated as canonical identity."""

    API_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str | None = None):
        self.api_key = (api_key or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def discover(
        self, *, artist_name: str, track_title: str | None = None,
        mode: str = "similar_artists", limit: int = 10,
    ) -> list[dict]:
        if not self.enabled or len(artist_name.strip()) < 1:
            return []
        method = {
            "similar_artists": "artist.getSimilar",
            "top_tracks": "artist.getTopTracks",
            "artist_tags": "artist.getTopTags",
            "similar_tracks": "track.getSimilar",
            "track_tags": "track.getTopTags",
        }.get(mode, "artist.getSimilar")
        params: dict[str, str | int] = {
            "method": method, "api_key": self.api_key, "format": "json",
            "artist": artist_name.strip()[:180], "limit": max(1, min(limit, 20)),
            "autocorrect": "1",
        }
        if method.startswith("track."):
            if not track_title or len(track_title.strip()) < 1:
                return []
            params["track"] = track_title.strip()[:180]
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(self.API_URL, params=params, headers={"User-Agent": _research_user_agent()})
            response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            return []
        return _lastfm_sources(payload, method, artist_name, track_title)


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


def _research_user_agent() -> str:
    return "IndieSoundQuest/0.1 (https://github.com/garfieldra/IndieSoundQuest)"


def _lastfm_sources(payload: dict, method: str, artist_name: str, track_title: str | None) -> list[dict]:
    if method == "artist.getSimilar":
        items = payload.get("similarartists", {}).get("artist", [])
        kind = "similar_artist"
    elif method == "artist.getTopTracks":
        items = payload.get("toptracks", {}).get("track", [])
        kind = "top_track"
    elif method == "track.getSimilar":
        items = payload.get("similartracks", {}).get("track", [])
        kind = "similar_track"
    else:
        items = payload.get("toptags", {}).get("tag", [])
        kind = "listener_tag"
    sources: list[dict] = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        item_artist = item.get("artist")
        if isinstance(item_artist, dict):
            item_artist = item_artist.get("name")
        name = str(item.get("name") or "").strip()
        item_artist = str(item_artist or artist_name).strip()
        url = str(item.get("url") or "").strip()
        if not url.startswith(("https://www.last.fm/", "http://www.last.fm/")):
            continue
        match = item.get("match")
        playcount = item.get("playcount")
        count = item.get("count")
        if match not in (None, ""):
            detail = f"相似度 {match}"
        elif playcount not in (None, ""):
            detail = f"收听计数 {playcount}"
        elif count not in (None, ""):
            detail = f"标签计数 {count}"
        else:
            detail = "Last.fm 听众关系信号"
        title = f"{name} · {item_artist}" if kind in {"top_track", "similar_track"} else name
        sources.append({
            "kind": "lastfm_discovery_source", "sourceUrl": url,
            "sourceTitle": title, "summary": f"Last.fm {kind}：{title}；{detail}"[:1_200],
            "queryPurpose": "music_discovery", "searchQuery": " · ".join(
                value for value in [artist_name, track_title or ""] if value
            ),
            "searchProvider": "lastfm", "evidenceKind": "LISTENER_SIMILARITY_SIGNAL",
            "discoveryType": kind, "title": name if kind in {"top_track", "similar_track"} else None,
            "artistName": item_artist,
        })
    return _deduplicate_sources(sources)


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


def _decode_document(data: bytes, content_type: str) -> str:
    declared = re.search(r"charset=([\w-]+)", content_type, re.I)
    encodings = [declared.group(1)] if declared else []
    encodings.extend(["utf-8", "gb18030"])
    best = ""
    for encoding in dict.fromkeys(encodings):
        try:
            value = data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        if not best or value.count("�") < best.count("�"):
            best = value
        if "�" not in value:
            return value
    return best or data.decode("utf-8", errors="replace")


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
