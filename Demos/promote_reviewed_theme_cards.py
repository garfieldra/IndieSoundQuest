#!/usr/bin/env python3
"""Promote only explicitly approved experimental cards; never bulk-promote by default."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("Demos/generated/lyric-theme-cards.jsonl"))
    parser.add_argument("--approved", type=Path, required=True, help="JSON array or approval-manifest JSON produced by a reviewer")
    parser.add_argument("--output", type=Path, default=Path("Demos/generated/local-curated-theme-cards.jsonl"))
    args = parser.parse_args()
    approval_input = json.loads(args.approved.read_text(encoding="utf-8"))
    approved = set(approval_input if isinstance(approval_input, list) else approval_input.get("approvedFingerprints", []))
    review_method = "MANUAL_REVIEW" if isinstance(approval_input, list) else approval_input.get("reviewMethod", "MANUAL_REVIEW")
    reviewed_at = None if isinstance(approval_input, list) else approval_input.get("reviewedAt")
    cards = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    promoted = []
    for card in cards:
        if card.get("sourceFingerprint") in approved:
            card = card | {"sourceType": "LOCAL_CURATED", "reviewStatus": "APPROVED", "reviewMethod": review_method, "reviewedAt": reviewed_at}
            promoted.append(card)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(card, ensure_ascii=False) + "\n" for card in promoted), encoding="utf-8")
    print(json.dumps({"approved": len(promoted), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__": main()
