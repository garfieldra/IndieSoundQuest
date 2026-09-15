"""Safe public run narration and validated-answer chunking."""

from __future__ import annotations

import re


PUBLIC_TOOL_NAMES = {
    "search_web": "网络音乐搜索",
    "read_source": "公开资料精读",
    "research_musicbrainz": "MusicBrainz 作品关系研究",
    "search_wikimedia": "Wikimedia 百科研究",
    "search_lastfm": "Last.fm 相似关系发现",
    "search_domestic_content": "中文内容检索",
    "search_knowledge": "Milvus 主题知识库",
    "resolve_musicbrainz": "MusicBrainz 身份核验",
    "expand_artist_catalog": "艺人作品扩展",
    "recommend_music": "音乐推荐整理",
    "analyze_music": "音乐分析",
    "generate_exploration_report": "对话探索报告",
}


def public_commentary(run_id: str, action: str, observation: dict | None = None) -> dict:
    """Build a safe research note without exposing model or tool scratch data."""
    observation = observation or {}
    count = next((int(observation[key]) for key in ("verifiedCount", "sourceCount", "outputCount", "count") if isinstance(observation.get(key), (int, float))), 0)
    notes = {
        "search_web": f"我已经从公开资料中找到{f' {count} 条' if count else ''}可用线索，接下来会核对它们是否真正支持你的问题。",
        "read_source": "我已经读过一批与核心问题最相关的页面；接下来会区分可验证事实、作品解读和仍然缺少的证据。",
        "research_musicbrainz": "作品身份、版本或署名关系已经完成一轮核对；我会把目录事实与乐评性判断分开使用。",
        "search_wikimedia": "百科与历史语境已经补入当前研究，但它只用于背景事实，不会替代对作品本身的分析。",
        "search_lastfm": "听众相似关系提供了新的探索线索；这些线索仍需经过规范音乐目录核验后才能进入推荐。",
        "search_domestic_content": "我已经补充中文内容平台的公开观点，接下来会过滤转载与缺少依据的说法。",
        "search_knowledge": "本地主题资料已作为补充加入；在线公开资料仍然是当前结论的主要依据。",
        "resolve_musicbrainz": f"我已经核验{f' {count} 个' if count else ''}歌曲身份，避免把同名作品或不同版本混在一起。",
        "expand_artist_catalog": f"明确艺人的作品目录已扩展{f'，得到 {count} 条候选' if count else ''}；下一步会按你的实际偏好筛选，而不是只按知名度排序。",
        "recommend_music": "推荐候选已经形成；我正在检查每一项是否具体、可跳转，并且与本轮偏好有清楚的关联。",
        "analyze_music": "分析草稿已经形成；我正在检查事实是否有来源、解释是否有作品依据，以及哪些判断需要保留不确定性。",
        "generate_exploration_report": "这段对话里的偏好线索已经整理成报告草稿，我正在去掉过度推断并保留可以继续追问的方向。",
    }
    message = notes.get(action)
    if not message:
        name = PUBLIC_TOOL_NAMES.get(action, action.replace("_", " "))
        message = f"{name}已经返回结果；我会根据新增证据决定继续查找、交叉核验，还是开始组织回答。"
    return {"runId": run_id, "phase": action, "status": "commentary", "message": message}


def response_chunks(text: str, target: int = 96, maximum: int = 160) -> list[str]:
    """Split an already-approved public answer into lossless durable chunks."""
    text = str(text or "")
    if not text:
        return []
    units = re.findall(r".*?(?:\n\n|[。！？；!?]\s*|$)", text, re.S)
    chunks: list[str] = []
    current = ""
    for unit in (value for value in units if value):
        while len(unit) > maximum:
            head, unit = unit[:maximum], unit[maximum:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if current and len(current) + len(unit) > maximum:
            chunks.append(current)
            current = ""
        current += unit
        if len(current) >= target:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks
