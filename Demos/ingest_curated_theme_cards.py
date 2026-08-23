#!/usr/bin/env python3
"""Import only reviewer-approved, non-lyric cards into the formal Milvus collection."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent-service"))
from app.knowledge_store import ThemeCardKnowledgeStore

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", type=Path, default=Path("Demos/generated/local-curated-theme-cards.jsonl")); parser.add_argument("--dry-run", action="store_true"); args = parser.parse_args()
    if not args.input.exists():
        if args.dry_run:
            print(json.dumps({"approvedCards": 0, "collection": os.getenv("KNOWLEDGE_COLLECTION", "isq_song_theme_cards_v1"), "reason": "no curated cards yet"}, ensure_ascii=False)); return
        raise SystemExit(f"curated input does not exist: {args.input}")
    cards = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved = [card for card in cards if card.get("sourceType") == "LOCAL_CURATED" and card.get("reviewStatus") == "APPROVED"]
    if args.dry_run: print(json.dumps({"approvedCards": len(approved), "collection": os.getenv("KNOWLEDGE_COLLECTION", "isq_song_theme_cards_v1")}, ensure_ascii=False)); return
    store = ThemeCardKnowledgeStore(os.getenv("MILVUS_URI", "http://localhost:19530"), os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"), os.getenv("KNOWLEDGE_COLLECTION", "isq_song_theme_cards_v1"))
    print(json.dumps({"upserted": store.upsert_approved(cards)}, ensure_ascii=False))

if __name__ == "__main__": main()
