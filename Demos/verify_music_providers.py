#!/usr/bin/env python3
"""验证 MusicBrainz、Cover Art Archive 和 Apple iTunes Search API 的最小数据闭环。

本脚本只读取 JSON 和 HTTP 头，不下载音频、封面或歌词，也不会在本地保存任何结果。
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


MUSICBRAINZ_BASE_URL = "https://musicbrainz.org/ws/2"
CAA_BASE_URL = "https://coverartarchive.org"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
USER_AGENT = "music-agent-demo/0.1 (contact: replace-with-project-email@example.com)"
MUSICBRAINZ_MIN_INTERVAL_SECONDS = 1.1


@dataclass
class CheckResult:
    name: str
    status: str
    details: dict[str, Any]


class HttpClient:
    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
        self.client = httpx.Client(follow_redirects=True, timeout=timeout)
        self._last_musicbrainz_request_at = 0.0

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        status, body, _ = self._request("GET", url, headers)
        return status, json.loads(body.decode("utf-8"))

    def head(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
        status, _, final_url = self._request("HEAD", url, headers)
        return status, final_url

    def musicbrainz_json(self, path: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        url = f"{MUSICBRAINZ_BASE_URL}/{path}?{urlencode({**params, 'fmt': 'json'})}"
        # 仅对网络/TLS 级瞬时故障重试一次；HTTP 429、503 等供应商响应不会被隐藏。
        for attempt in range(2):
            elapsed = time.monotonic() - self._last_musicbrainz_request_at
            if elapsed < MUSICBRAINZ_MIN_INTERVAL_SECONDS:
                time.sleep(MUSICBRAINZ_MIN_INTERVAL_SECONDS - elapsed)
            try:
                return self.get_json(url, {"User-Agent": USER_AGENT, "Accept": "application/json"})
            except RuntimeError:
                if attempt == 1:
                    raise
            finally:
                self._last_musicbrainz_request_at = time.monotonic()

        raise AssertionError("unreachable")

    def _request(self, method: str, url: str, headers: dict[str, str] | None) -> tuple[int, bytes, str]:
        try:
            response = self.client.request(method, url, headers=headers or {})
            # 不调用 raise_for_status：404、429、503 是需要报告给调用方的有效供应商响应。
            return response.status_code, response.content, str(response.url)
        except httpx.HTTPError as error:
            raise RuntimeError(f"network_error: {error}") from error


def result_from_exception(name: str, error: Exception) -> CheckResult:
    return CheckResult(name=name, status="error", details={"error": str(error)})


def check_musicbrainz_artist(client: HttpClient, artist: str) -> tuple[CheckResult, dict[str, Any] | None]:
    try:
        status_code, payload = client.musicbrainz_json("artist/", {"query": f'artist:"{artist}"', "limit": "5"})
        artists = payload.get("artists", [])
        first = artists[0] if artists else None
        status = "passed" if status_code == 200 and first else "failed"
        return (
            CheckResult(
                name="musicbrainz_artist_search",
                status=status,
                details={
                    "http_status": status_code,
                    "query": artist,
                    "result_count": len(artists),
                    "top_result": None if first is None else {
                        "mbid": first.get("id"),
                        "name": first.get("name"),
                        "score": first.get("score"),
                    },
                },
            ),
            first,
        )
    except Exception as error:  # noqa: BLE001 - 演示脚本需要把所有供应商异常写入报告。
        return result_from_exception("musicbrainz_artist_search", error), None


def check_musicbrainz_release_group(client: HttpClient, artist_mbid: str | None) -> tuple[CheckResult, dict[str, Any] | None]:
    if not artist_mbid:
        return CheckResult(
            name="musicbrainz_release_group_search",
            status="skipped",
            details={"reason": "no_artist_mbid"},
        ), None

    try:
        status_code, payload = client.musicbrainz_json("release-group/", {"artist": artist_mbid, "limit": "5"})
        release_groups = payload.get("release-groups", [])
        first = release_groups[0] if release_groups else None
        status = "passed" if status_code == 200 and first else "failed"
        return (
            CheckResult(
                name="musicbrainz_release_group_search",
                status=status,
                details={
                    "http_status": status_code,
                    "artist_mbid": artist_mbid,
                    "result_count": len(release_groups),
                    "top_result": None if first is None else {
                        "release_group_mbid": first.get("id"),
                        "title": first.get("title"),
                    },
                },
            ),
            first,
        )
    except Exception as error:  # noqa: BLE001
        return result_from_exception("musicbrainz_release_group_search", error), None


def check_cover_art(client: HttpClient, release_group: dict[str, Any] | None) -> CheckResult:
    release_group_mbid = (release_group or {}).get("id")
    if not release_group_mbid:
        return CheckResult(
            name="cover_art_archive_front",
            status="skipped",
            details={"reason": "no_release_group_mbid"},
        )

    url = f"{CAA_BASE_URL}/release-group/{release_group_mbid}/front-250"
    try:
        status_code, final_url = client.head(url, {"User-Agent": USER_AGENT})
        status = "passed" if status_code == 200 else "degraded" if status_code == 404 else "failed"
        return CheckResult(
            name="cover_art_archive_front",
            status=status,
            details={
                "http_status": status_code,
                "release_group_mbid": release_group_mbid,
                "requested_url": url,
                "final_url": final_url,
                "note": "404 means no cover is available for this release group; use a fallback provider.",
            },
        )
    except Exception as error:  # noqa: BLE001
        return result_from_exception("cover_art_archive_front", error)


def check_itunes(client: HttpClient, artist: str, song: str, country: str) -> CheckResult:
    query = f"{artist} {song}".strip()
    url = f"{ITUNES_SEARCH_URL}?{urlencode({'term': query, 'country': country, 'media': 'music', 'entity': 'song', 'limit': '5'})}"
    try:
        status_code, payload = client.get_json(url, {"User-Agent": USER_AGENT, "Accept": "application/json"})
        songs = payload.get("results", [])
        first = songs[0] if songs else None
        status = "passed" if status_code == 200 and first else "failed"
        return CheckResult(
            name="itunes_song_search",
            status=status,
            details={
                "http_status": status_code,
                "query": query,
                "country": country,
                "result_count": len(songs),
                "top_result": None if first is None else {
                    "track_name": first.get("trackName"),
                    "artist_name": first.get("artistName"),
                    "collection_name": first.get("collectionName"),
                    "artwork_url": first.get("artworkUrl100"),
                    "preview_url_present": bool(first.get("previewUrl")),
                    "track_view_url": first.get("trackViewUrl"),
                },
            },
        )
    except Exception as error:  # noqa: BLE001
        return result_from_exception("itunes_song_search", error)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artist", default="五月天", help="艺人名，默认：五月天")
    parser.add_argument("--song", default="温柔", help="歌曲名，默认：温柔")
    parser.add_argument("--country", default="US", help="Apple Store 双字母地区代码，默认：US")
    parser.add_argument("--timeout", type=int, default=15, help="单次请求超时秒数，默认：15")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = HttpClient(timeout=args.timeout)

    artist_result, artist = check_musicbrainz_artist(client, args.artist)
    release_group_result, release_group = check_musicbrainz_release_group(client, (artist or {}).get("id"))
    checks = [
        artist_result,
        release_group_result,
        check_cover_art(client, release_group),
        check_itunes(client, args.artist, args.song, args.country),
    ]

    report = {
        "input": {"artist": args.artist, "song": args.song, "country": args.country},
        "checks": [asdict(check) for check in checks],
        "summary": {
            "passed": sum(check.status == "passed" for check in checks),
            "degraded": sum(check.status == "degraded" for check in checks),
            "failed": sum(check.status in {"failed", "error"} for check in checks),
            "skipped": sum(check.status == "skipped" for check in checks),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 核心验收条件：MusicBrainz 艺人搜索和 Apple 歌曲搜索均可用。
    required_checks = {"musicbrainz_artist_search", "itunes_song_search"}
    failed_required = [check.name for check in checks if check.name in required_checks and check.status != "passed"]
    return 1 if failed_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
