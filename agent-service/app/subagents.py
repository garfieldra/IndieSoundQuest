from __future__ import annotations

from collections import Counter
from typing import Any

from .report_schemas import CritiqueResult, PreferenceReport
from .tools import WebSearchTool


class EvidenceRegistry:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def add(self, item: dict[str, Any]) -> dict[str, Any]:
        evidence_id = item["evidenceId"]
        self._items[evidence_id] = item
        return item

    def values(self) -> list[dict[str, Any]]:
        return list(self._items.values())


class PreferenceAnalysisSubagent:
    """只从赛事快照归纳信号；不接触网络内容，也不作现实身份推断。"""
    async def analyze(self, facts: dict[str, Any], registry: EvidenceRegistry) -> list[dict[str, Any]]:
        entries = {str(item["entryId"]): item for item in facts.get("entries", [])}
        matches = facts.get("matches", [])
        winners = [entries.get(str(match.get("winnerEntryId"))) for match in matches]
        winners = [item for item in winners if item]
        evidence = []
        for match in matches[:5]:
            evidence.append(registry.add({"evidenceId": str(match["matchId"]), "kind": "match_fact", "confidence": "high", "source": {"matchId": str(match["matchId"]), "entityId": None, "url": None}}))
        counts = Counter(item["artistName"] for item in winners)
        signals = [{"name": "晋级选择轨迹", "confidence": "medium", "description": "你在多轮一对一选择中反复让这些作品进入下一轮，说明它们构成了本场最稳定的偏好线索。", "evidence": evidence}]
        if len(counts) == 1 and counts:
            artist = next(iter(counts))
            signals.append({"name": "艺人集中偏好", "confidence": "medium", "description": f"晋级作品主要集中在{artist}，本场结果更适合从这位艺人的不同阶段和不同专辑继续展开。", "evidence": evidence[:2]})
        else:
            signals.append({"name": "跨艺人探索倾向", "confidence": "low", "description": "晋级路径没有完全集中于单一艺人，说明你可能愿意沿着相近气质跨艺人继续探索，而不只停留在熟悉的名字里。", "evidence": evidence[:2]})
        signals.append({"name": "选择的辨识度", "confidence": "low", "description": "冠军与完整晋级路径共同构成了本场最有辨识度的选择轨迹，可以作为下一轮探索时回看的参照坐标。", "evidence": evidence[:2]})
        return signals


class NetworkResearchSubagent:
    """封装网络搜索，并把结果登记为 web_source；无 Key 时安全返回空列表。"""
    def __init__(self, web: WebSearchTool) -> None:
        self.web = web

    async def research(self, facts: dict[str, Any], signals: list[dict[str, Any]], registry: EvidenceRegistry, query_hint: str | None = None) -> list[dict[str, Any]]:
        artist_names = sorted({str(entry.get("artistName", "")).strip() for entry in facts.get("entries", []) if entry.get("artistName")})
        artist_hint = " ".join(artist_names[:2]) or "中文独立音乐"
        entries = {str(item.get("entryId")): item for item in facts.get("entries", [])}
        winner_titles = [str(entries[str(match.get("winnerEntryId"))].get("title", "")) for match in facts.get("matches", []) if str(match.get("winnerEntryId")) in entries]
        queries = [
            {"query": (query_hint or f"喜欢 {artist_hint} 的人 推荐 相似歌手 中文独立音乐").strip(), "purpose": "cross_artist_discovery"},
            {"query": f"{artist_hint} 相似歌手 推荐", "purpose": "similar_artist"},
        ]
        if winner_titles:
            queries.append({"query": f"{winner_titles[-1]} {artist_hint} 相似歌曲 推荐", "purpose": "song_path_discovery"})
        if hasattr(self.web, "search_many"):
            sources = await self.web.search_many(queries)
        else:
            sources = await self.web.search(queries[0]["query"])
        for index, source in enumerate(sources):
            registry.add({"evidenceId": f"web:{index}:{source['sourceUrl']}", "kind": "web_source", "confidence": "low", "source": {"matchId": None, "entityId": None, "url": source["sourceUrl"]}})
        return sources


class RecommendationValidationSubagent:
    """分开校验目录推荐与网络发现推荐，避免把后者伪装成已入库实体。"""
    async def validate(self, report: PreferenceReport, facts: dict[str, Any], network_sources: list[dict[str, Any]]) -> list[str]:
        valid_recordings = {str(item.get("recordingId")) for item in facts.get("entries", [])}
        valid_urls = {item.get("sourceUrl") for item in network_sources}
        warnings = []
        if any(item.source_status == "catalog_verified" and str(item.recording_id) not in valid_recordings for item in report.song_recommendations):
            warnings.append("UNVERIFIED_CATALOG_RECORDING")
        web_items = [*filter(lambda item: item.source_status == "web_discovered", report.song_recommendations), *filter(lambda item: item.source_status == "web_discovered", report.artist_recommendations)]
        if any(item.source_url not in valid_urls for item in web_items):
            warnings.append("UNVERIFIED_WEB_SOURCE")
        return warnings


class CriticSubagent:
    """确定性硬规则审查；LLM Critic 在下一次实现中作为额外审查层接入。"""
    async def review(self, report: PreferenceReport, facts: dict[str, Any], network_sources: list[dict[str, Any]]) -> CritiqueResult:
        valid_ids = {str(item.get("recordingId")) for item in facts.get("entries", [])}
        valid_urls = {item.get("sourceUrl") for item in network_sources}
        issues: list[str] = []
        if any(item.source_status == "catalog_verified" and str(item.recording_id) not in valid_ids for item in report.song_recommendations):
            issues.append("RECOMMENDATION_ENTITY_NOT_IN_FACTS")
        web_items = [*filter(lambda item: item.source_status == "web_discovered", report.song_recommendations), *filter(lambda item: item.source_status == "web_discovered", report.artist_recommendations)]
        if any(item.source_url not in valid_urls for item in web_items):
            issues.append("WEB_RECOMMENDATION_SOURCE_NOT_IN_RESEARCH")
        if any(not dimension.evidence for dimension in report.dimensions):
            issues.append("DIMENSION_EVIDENCE_MISSING")
        if "仅基于" not in report.disclaimer:
            issues.append("DISCLAIMER_MISSING")
        return CritiqueResult(passed=not issues, issues=issues, risk_level="high" if issues else "low")
