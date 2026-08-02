"""Resolve Apple/iTunes cover URLs for the Anpu seed list.

Usage: python sync_seed_covers.py > anpu_cover_candidates.json
Review the JSON before turning approved results into a Flyway migration.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from opencc import OpenCC

SEED_SQL = Path(__file__).parents[1] / "java-service/src/main/resources/db/migration/V2__seed_anpu_recordings.sql"
API = "https://itunes.apple.com/search"
T2S = OpenCC("t2s")


def songs() -> list[tuple[str, str]]:
    text = SEED_SQL.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "UUID_TO_BIN" in line and "@artist_id," in line]
    result: list[tuple[str, str]] = []
    for line in lines:
        values = line.split("@artist_id,", 1)[1].split(")", 1)[0].split(",")
        result.append((values[0].strip("'"), values[1].strip("'")))
    return result


def resolve(client: httpx.Client, title: str, album: str) -> dict[str, object]:
    response = client.get(API, params={"term": f"{title} {album} 張懸", "entity": "song", "country": "TW", "limit": 25})
    response.raise_for_status()
    candidates = response.json().get("results", [])
    def normalize(value: str) -> str:
        return T2S.convert(value).lower().replace("（", "(").replace("）", ")").replace("，", ",").replace(" ", "")
    exact = next((item for item in candidates if normalize(item.get("trackName", "")) == normalize(title) and item.get("artistName") in {"張懸", "安溥"} and normalize(album) in normalize(item.get("collectionName", ""))), None)
    if exact is None:
        exact = next((item for item in candidates if normalize(item.get("trackName", "")) == normalize(title) and item.get("artistName") in {"張懸", "安溥"}), None)
    return {"title": title, "album": album, "matched": bool(exact), "status": "AVAILABLE" if exact else "UNAVAILABLE", "coverUrl": exact.get("artworkUrl100", "").replace("100x100", "600x600") if exact else None, "providerTrack": exact.get("trackName") if exact else None, "providerAlbum": exact.get("collectionName") if exact else None, "providerArtist": exact.get("artistName") if exact else None, "trackViewUrl": exact.get("trackViewUrl") if exact else None}


def main() -> None:
    with httpx.Client(timeout=15, headers={"User-Agent": "IndieSoundQuest/0.1 seed-cover-sync"}) as client:
        output = []
        for title, album in songs():
            output.append(resolve(client, title, album))
            time.sleep(0.25)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
