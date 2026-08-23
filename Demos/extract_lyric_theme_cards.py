#!/usr/bin/env python3
"""Create non-quoting, experimental theme cards from LyricMind Markdown.

The input lyrics are used only in memory for model analysis. Output intentionally
contains no `lyrics` field and is unsuitable for a lyrics-search product.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
DEFAULT_SOURCE = Path("/Users/wangrui/Documents/Projects/LyricMind/data")
SYSTEM = """你是中文流行与独立音乐的资料整理助手。根据输入歌词生成不含歌词原句的结构化主题卡。
不得复制、改写成连续歌词或输出任何引号中的歌词句子；summary 必须是独立的概述，最多120个汉字。
themes/moods/scenes 各输出 2 到 6 个简短中文短语；narrativePerspective 为简短叙事视角描述。只输出 JSON。"""


def parse_markdown(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    matches = list(SECTION.finditer(text))
    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        values[match.group(1).strip()] = text[match.end():end].strip()
    return values


def model_card(metadata: dict[str, str], api_key: str, model: str) -> dict:
    prompt = {"title": metadata.get("歌名", ""), "artist": metadata.get("歌手", ""), "album": metadata.get("收录专辑", ""), "releaseYear": metadata.get("发行时间", ""), "region": metadata.get("地区", ""), "lyricsForAnalysisOnly": metadata.get("歌词", "")}
    payload = json.dumps({"model": model, "temperature": 0.15, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]}, ensure_ascii=False).encode()
    request = Request("https://api.deepseek.com/chat/completions", data=payload, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urlopen(request, timeout=75) as response:
        result = json.loads(response.read())
    return json.loads(result["choices"][0]["message"]["content"])


def contains_lyric_leak(summary: str, lyrics: str) -> bool:
    normalized = re.sub(r"\s+", "", summary)
    lyric = re.sub(r"\s+", "", lyrics)
    return any(len(chunk) >= 12 and chunk in lyric for chunk in re.findall(r"[\u4e00-\u9fff]{12,}", normalized))


def card_for(path: Path, api_key: str, model: str) -> dict:
    metadata = parse_markdown(path)
    lyrics = metadata.get("歌词", "")
    artist = metadata.get("歌手", path.parent.name).strip()
    title = metadata.get("歌名", path.stem).strip()
    generated = model_card(metadata, api_key, model)
    def tags(field: str) -> list[str]:
        return [str(item).strip()[:30] for item in generated.get(field, []) if str(item).strip()][:6]
    themes, moods, scenes = tags("themes"), tags("moods"), tags("scenes")
    summary = str(generated.get("summary", "")).strip()[:160]
    # If a model accidentally paraphrases too closely, retain only a generic,
    # metadata-like derived summary rather than retrying or retaining the phrase.
    if not summary or contains_lyric_leak(summary, lyrics):
        summary = f"作品围绕{'、'.join(themes[:2]) or '个人经验'}展开，整体情绪偏{'、'.join(moods[:2]) or '内省'}，以{'、'.join(scenes[:1]) or '日常想象'}作为主要感受场景。"
    return {
        "schemaVersion": "1.0", "title": title, "artist": artist,
        "album": metadata.get("收录专辑", ""), "releaseYear": metadata.get("发行时间", ""), "region": metadata.get("地区", ""),
        "themes": themes, "moods": moods, "scenes": scenes,
        "narrativePerspective": str(generated.get("narrativePerspective", "")).strip()[:60], "summary": summary,
        "sourceType": "EXPERIMENTAL_LYRIC_DERIVED", "reviewStatus": "PENDING_REVIEW",
        # A source identity includes music metadata as well as lyrics: two
        # different songs may legitimately share an identical lyric file.
        "sourceFingerprint": hashlib.sha256(f"{artist}\0{title}\0{lyrics}".encode()).hexdigest(), "sourcePath": str(path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=Path("Demos/generated/lyric-theme-cards.jsonl"))
    parser.add_argument("--artist")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", "deepseek-chat"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    files = sorted(args.source.rglob("*.md"))
    if args.artist: files = [path for path in files if path.parent.name == args.artist]
    files = files[:args.limit]
    if args.dry_run:
        print(json.dumps({"count": len(files), "files": [str(path) for path in files]}, ensure_ascii=False, indent=2)); return
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key: raise SystemExit("DEEPSEEK_API_KEY is required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    existing_cards = [json.loads(line) for line in args.output.read_text(encoding="utf-8").splitlines() if line.strip()] if args.output.exists() else []
    completed = {card.get("sourceFingerprint") for card in existing_cards}
    completed_identities = {
        (str(card.get("artist", "")).strip(), str(card.get("title", "")).strip())
        for card in existing_cards
    }
    with args.output.open("a", encoding="utf-8") as handle:
        for index, path in enumerate(files, 1):
            metadata = parse_markdown(path)
            artist = metadata.get("歌手", path.parent.name).strip()
            title = metadata.get("歌名", path.stem).strip()
            identity = (artist, title)
            source_fingerprint = hashlib.sha256(f"{artist}\0{title}\0{metadata.get('歌词', '')}".encode()).hexdigest()
            if source_fingerprint in completed or identity in completed_identities: continue
            for attempt in range(3):
                try:
                    card = card_for(path, api_key, args.model)
                    identity = (str(card["artist"]).strip(), str(card["title"]).strip())
                    if identity in completed_identities:
                        completed.add(source_fingerprint)
                        break
                    handle.write(json.dumps(card, ensure_ascii=False) + "\n"); handle.flush()
                    completed.add(source_fingerprint); completed_identities.add(identity)
                    break
                except (HTTPError, URLError, IncompleteRead, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as error:
                    if attempt == 2: print(f"failed: {path.name}: {type(error).__name__}")
                    else: time.sleep(2 ** attempt)
            print(f"[{index}/{len(files)}] {path.parent.name} · {path.stem}")
            time.sleep(0.4)


if __name__ == "__main__": main()
