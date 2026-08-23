#!/usr/bin/env python3
"""Recover valid JSONL rows after an interrupted local experimental batch."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", type=Path, default=Path("Demos/generated/lyric-theme-cards.jsonl")); args = parser.parse_args()
    valid, invalid = [], []
    for number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try: valid.append(json.loads(line))
        except json.JSONDecodeError: invalid.append(number)
    unique_by_fingerprint = {card["sourceFingerprint"]: card for card in valid}
    unique: dict[tuple[str, str], dict] = {}
    for card in unique_by_fingerprint.values():
        identity = (str(card.get("artist", "")).strip(), str(card.get("title", "")).strip())
        unique.setdefault(identity, card)
    temp = args.input.with_suffix(".jsonl.repaired")
    temp.write_text("".join(json.dumps(card, ensure_ascii=False) + "\n" for card in unique.values()), encoding="utf-8")
    temp.replace(args.input)
    print(json.dumps({"valid": len(valid), "deduplicated": len(unique), "discardedLines": invalid}, ensure_ascii=False))

if __name__ == "__main__": main()
