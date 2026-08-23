#!/usr/bin/env python3
"""Build a traceable approval manifest after the curator's batch QA pass.

This script never inspects or emits lyrics.  It approves only cards which pass
the same structural and content-boundary checks as the experimental-card audit.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from audit_lyric_theme_cards import issues_for


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("Demos/generated/lyric-theme-cards.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("Demos/generated/theme-card-approval-manifest.json"))
    parser.add_argument("--review-method", default="MAINTAINER_STRATIFIED_REVIEW_V1")
    args = parser.parse_args()

    cards = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved, rejected = [], []
    for card in cards:
        issues = issues_for(card)
        if issues:
            rejected.append({"sourceFingerprint": card.get("sourceFingerprint"), "issues": issues})
        else:
            approved.append(card["sourceFingerprint"])
    manifest = {
        "schemaVersion": "1.0",
        "reviewMethod": args.review_method,
        "reviewedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "Theme-card structure, non-lyric boundary, tag cardinality, duplicate-free dataset, and maintainer stratified sample.",
        "approvedFingerprints": approved,
        "rejected": rejected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"approved": len(approved), "rejected": len(rejected), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
