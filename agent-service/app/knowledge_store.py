"""Milvus storage for approved, non-lyric song theme cards only."""
from __future__ import annotations

import hashlib
from typing import Any


def card_text(card: dict[str, Any]) -> str:
    """The sole embed payload; it intentionally excludes raw lyrics and source paths."""
    return "\n".join(filter(None, [
        f"歌曲：{card.get('title', '')}", f"艺人：{card.get('artist', '')}",
        f"主题：{'、'.join(card.get('themes', []))}", f"情绪：{'、'.join(card.get('moods', []))}",
        f"场景：{'、'.join(card.get('scenes', []))}", f"叙事视角：{card.get('narrativePerspective', '')}",
        f"概述：{card.get('summary', '')}",
    ]))


class ThemeCardKnowledgeStore:
    def __init__(self, uri: str, model_name: str, collection_name: str):
        self.uri, self.model_name, self.collection_name = uri, model_name, collection_name
        self._model = None

    def _embedder(self):
        if self._model is None:
            # CPU-only ONNX inference. Model files download only on the first
            # real import/search, never while building the service image.
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._embedder().embed(texts)]

    def upsert_approved(self, cards: list[dict[str, Any]]) -> int:
        approved = [card for card in cards if card.get("sourceType") == "LOCAL_CURATED" and card.get("reviewStatus") == "APPROVED"]
        if not approved: return 0
        from pymilvus import MilvusClient
        texts = [card_text(card) for card in approved]
        vectors = self._embed_texts(texts)
        client = MilvusClient(uri=self.uri)
        if not client.has_collection(self.collection_name):
            client.create_collection(collection_name=self.collection_name, dimension=len(vectors[0]), metric_type="COSINE", auto_id=False, enable_dynamic_field=True)
        rows = []
        for card, vector in zip(approved, vectors):
            fingerprint = str(card["sourceFingerprint"])
            # MilvusClient.create_collection uses an int64 primary key by
            # default.  Keep upserts idempotent with a deterministic positive
            # 63-bit value rather than relying on auto-generated IDs.
            primary_key = int.from_bytes(hashlib.sha256(fingerprint.encode()).digest()[:8], "big") & ((1 << 63) - 1)
            rows.append({"id": primary_key, "vector": vector, "title": card["title"], "artist": card["artist"], "themes": card.get("themes", []), "moods": card.get("moods", []), "scenes": card.get("scenes", []), "summary": card.get("summary", ""), "sourceType": "LOCAL_CURATED", "reviewStatus": "APPROVED"})
        client.upsert(collection_name=self.collection_name, data=rows)
        return len(rows)

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        from pymilvus import MilvusClient
        client = MilvusClient(uri=self.uri)
        if not client.has_collection(self.collection_name): return []
        vector = self._embed_texts([query])[0]
        hits = client.search(collection_name=self.collection_name, data=[vector], limit=limit, output_fields=["title", "artist", "themes", "moods", "scenes", "summary", "sourceType", "reviewStatus"])[0]
        return [{"claimId": hit["id"], "score": hit.get("distance"), **hit.get("entity", {})} for hit in hits]
