import httpx
import pytest

from app.tools import SpotifyCatalogTool


@pytest.mark.asyncio
async def test_spotify_tool_is_a_no_network_noop_without_credentials(monkeypatch):
    called = False

    class FailClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def post(self, *_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("disabled Spotify must not request a token")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FailClient())
    assert await SpotifyCatalogTool().search_tracks("Radiohead") == []
    assert not called


@pytest.mark.asyncio
async def test_spotify_tool_maps_track_hints_and_caches_client_credential_token(monkeypatch):
    calls = {"token": 0, "search": 0}

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None
        async def post(self, url, **_kwargs):
            assert url == SpotifyCatalogTool.TOKEN_URL
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600}, request=httpx.Request("POST", url))
        async def get(self, url, *, params, headers):
            assert url == SpotifyCatalogTool.SEARCH_URL
            assert headers == {"Authorization": "Bearer test-token"}
            assert params["q"] == "Radiohead"
            assert params["market"] == "US"
            calls["search"] += 1
            return httpx.Response(200, json={"tracks": {"items": [{
                "id": "spotify-track-id", "name": "Paranoid Android",
                "artists": [{"name": "Radiohead"}],
                "external_urls": {"spotify": "https://open.spotify.com/track/example"},
                "external_ids": {"isrc": "GBAYE9701375"},
                "album": {"name": "OK Computer", "images": [{"url": "https://i.scdn.co/image/cover"}]},
            }]}}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    tool = SpotifyCatalogTool("client-id", "client-secret", "us")
    first = await tool.search_tracks("Radiohead")
    second = await tool.search_tracks("Radiohead")

    assert calls == {"token": 1, "search": 2}
    assert first == second == [{
        "kind": "spotify_catalog_source", "sourceUrl": "https://open.spotify.com/track/example",
        "sourceTitle": "Paranoid Android · Radiohead", "summary": "Spotify 目录收录：Paranoid Android · Radiohead",
        "queryPurpose": "candidate_discovery", "searchQuery": "Radiohead", "catalogProvider": "SPOTIFY",
        "spotifyTrackId": "spotify-track-id", "isrc": "GBAYE9701375", "albumTitle": "OK Computer",
        "coverUrl": "https://i.scdn.co/image/cover", "title": "Paranoid Android", "artistName": "Radiohead",
    }]
