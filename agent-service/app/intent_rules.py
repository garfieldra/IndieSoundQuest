from __future__ import annotations

import re


LOCK_PATTERNS = [
    r"只(?:要|选|听|放|用|限于|限定)?[^，。！？]{0,40}(?:的歌|歌曲|作品|曲目|歌手)",
    r"仅(?:限|限于|选择|使用|要)[^，。！？]{0,40}",
    r"(?:个人|单人|专属)(?:歌曲)?世界杯",
    r"不要其他(?:歌手|艺人)",
    r"不(?:要|包含|考虑)别的(?:歌手|艺人)",
]
SEEDED_PATTERNS = [
    r"我喜欢", r"从.{1,30}(?:开始|出发)", r"以.{1,30}为(?:起点|线索|中心)",
    r"类似", r"相近", r"像.{1,30}一样",
]
FACET_TERMS = {
    "language": ["中文", "华语", "粤语", "台语", "英语", "日语", "韩语"],
    "mood": ["克制", "温柔", "忧郁", "明亮", "治愈", "孤独", "浪漫", "热烈", "安静", "冷峻"],
    "scene": ["雨夜", "夜晚", "傍晚", "散步", "通勤", "开车", "学习", "睡前", "旅行", "雨天", "清晨"],
    "genre": ["独立", "民谣", "摇滚", "电子", "爵士", "流行", "后摇", "说唱", "朋克", "梦幻流行", "灵魂乐", "R&B", "r&b"],
    "era": ["早期", "近期", "九十年代", "千禧年", "2000年代", "2010年代", "老歌", "新歌"],
}


def classify_intent_rule(preference: str, seed_artist_ids: list[str]) -> dict:
    """Pure deterministic safety baseline, intentionally free of model/runtime dependencies."""
    locked_evidence = [match.group(0)[:80] for pattern in LOCK_PATTERNS if (match := re.search(pattern, preference))]
    if locked_evidence:
        mode, evidence = "ARTIST_LOCKED", locked_evidence
    elif seed_artist_ids or any(re.search(pattern, preference) for pattern in SEEDED_PATTERNS):
        mode, evidence = "ARTIST_SEEDED", [preference[:80]]
    else:
        mode, evidence = "OPEN_DISCOVERY", [preference[:80]]
    facets = {
        name: [term for term in terms if term.lower() in preference.lower()]
        for name, terms in FACET_TERMS.items()
    }
    # Compact date forms are common in Chinese music prompts and are not covered
    # by literal vocabulary alone.
    if re.search(r"(?:90|九十)年代", preference): facets["era"].append("90年代")
    if re.search(r"(?:00|零零|2000)年代", preference): facets["era"].append("00年代")
    return {
        "intent_mode": mode,
        "allowed_artist_ids": list(seed_artist_ids) if mode == "ARTIST_LOCKED" else [],
        "allowed_artist_names": [],
        "seed_artist_ids": list(seed_artist_ids),
        "preference_facets": facets,
        "confidence": "high" if locked_evidence else "medium",
        "evidence_spans": evidence[:5],
    }
