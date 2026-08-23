#!/usr/bin/env python3
"""Audit experimental theme cards before a human promotes them to LOCAL_CURATED."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


REQUIRED = {"title", "artist", "themes", "moods", "scenes", "summary", "sourceType", "reviewStatus", "sourceFingerprint"}
FORBIDDEN = {"lyrics", "lyric", "rawLyrics", "embedding"}


def issues_for(card: dict) -> list[str]:
    issues: list[str] = []
    missing = sorted(key for key in REQUIRED if not card.get(key))
    if missing: issues.append("MISSING_" + "_".join(missing))
    if FORBIDDEN.intersection(card): issues.append("FORBIDDEN_RAW_CONTENT_FIELD")
    if card.get("sourceType") != "EXPERIMENTAL_LYRIC_DERIVED": issues.append("UNEXPECTED_SOURCE_TYPE")
    if card.get("reviewStatus") != "PENDING_REVIEW": issues.append("UNEXPECTED_REVIEW_STATUS")
    if len(str(card.get("summary", ""))) > 160: issues.append("SUMMARY_TOO_LONG")
    if any(len(str(tag)) > 30 for key in ("themes", "moods", "scenes") for tag in card.get(key, [])): issues.append("TAG_TOO_LONG")
    if not (2 <= len(card.get("themes", [])) <= 6): issues.append("THEME_COUNT_OUT_OF_RANGE")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("Demos/generated/lyric-theme-cards.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("Demos/generated/lyric-theme-card-audit.json"))
    args = parser.parse_args()
    lines = args.input.read_text(encoding="utf-8").splitlines()
    cards: list[dict] = []; incomplete_tail = 0
    for index, line in enumerate(lines):
        if not line.strip(): continue
        try: cards.append(json.loads(line))
        except json.JSONDecodeError:
            # The extractor appends one JSON line at a time; while it is live only
            # an incomplete final line is tolerated, never an earlier corruption.
            if index == len(lines) - 1: incomplete_tail = 1; continue
            raise
    seen: set[tuple[str, str]] = set(); invalid: list[dict] = []
    for card in cards:
        issues = issues_for(card); identity = (str(card.get("artist", "")).strip(), str(card.get("title", "")).strip())
        if identity in seen: issues.append("DUPLICATE_ARTIST_TITLE")
        seen.add(identity)
        if issues: invalid.append({"artist": card.get("artist"), "title": card.get("title"), "issues": issues})
    report = {
        "cardCount": len(cards), "validCount": len(cards) - len(invalid), "invalidCount": len(invalid),
        "artistCount": len({card.get("artist") for card in cards}), "topThemes": Counter(tag for card in cards for tag in card.get("themes", [])).most_common(20),
        "invalidCards": invalid, "incompleteTailLineSkipped": incomplete_tail,
        "promotionRule": "Only a separately reviewed selection may change sourceType to LOCAL_CURATED.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True); args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("cardCount", "validCount", "invalidCount", "artistCount")}, ensure_ascii=False))
    if invalid: raise SystemExit(2)


if __name__ == "__main__": main()
