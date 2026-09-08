"""Declarative control-plane metadata for the unified music exploration Agent.

Registries intentionally expose only capability summaries.  Executable tools and
their credentials remain owned by the service composition in ``main.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ToolKind = Literal["atomic", "composite"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    kind: ToolKind
    summary: str
    evidence_type: str
    timeout_seconds: int
    cacheable: bool = False


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    version: str
    trigger_summary: str
    allowed_tools: tuple[str, ...]
    output_summary: str


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]):
        self._tools = {tool.name: tool for tool in tools}

    def summaries(self, names: tuple[str, ...] | None = None) -> list[dict[str, str]]:
        selected = self._tools.values() if names is None else (self._tools[name] for name in names if name in self._tools)
        return [{"name": item.name, "kind": item.kind, "summary": item.summary, "evidenceType": item.evidence_type} for item in selected]


class SkillRegistry:
    def __init__(self, skills: list[SkillDefinition], tools: ToolRegistry):
        self._skills = {skill.name: skill for skill in skills}; self._tools = tools

    def summaries(self) -> list[dict[str, object]]:
        return [{"name": skill.name, "version": skill.version, "trigger": skill.trigger_summary, "output": skill.output_summary, "tools": self._tools.summaries(skill.allowed_tools)} for skill in self._skills.values()]


tool_registry = ToolRegistry([
    ToolDefinition("search_web", "atomic", "从公开资料发现音乐线索", "PUBLIC_SOURCE", 15, True),
    ToolDefinition("search_knowledge", "atomic", "检索本地歌曲主题与文化语境", "KNOWLEDGE_CARD", 10, True),
    ToolDefinition("verify_musicbrainz", "atomic", "核验歌曲和艺人的规范身份", "CATALOG_IDENTITY", 12, True),
    ToolDefinition("build_candidate_pool", "composite", "构建并核验可确认的赛事候选池", "CANDIDATE_POOL", 900),
    ToolDefinition("analyze_tournament", "composite", "归纳赛事中的关键选择与比较", "TOURNAMENT_SIGNAL", 30),
    ToolDefinition("generate_exploration_report", "composite", "根据对话和反馈生成探索报告", "REPORT_CLAIM", 320),
    ToolDefinition("generate_tournament_report", "composite", "根据赛事与对话生成赛后报告", "REPORT_CLAIM", 320),
    ToolDefinition("record_recommendation_feedback", "atomic", "写入用户对推荐的明确反馈", "PREFERENCE_EVENT", 5),
])

skill_registry = SkillRegistry([
    SkillDefinition("song_world_cup", "1.0", "用户要求淘汰赛，或需要高密度偏好信号", ("build_candidate_pool", "verify_musicbrainz", "analyze_tournament", "generate_tournament_report"), "世界杯启动卡、候选池或赛后报告卡"),
    SkillDefinition("open_music_exploration", "1.0", "用户希望直接探索、推荐或理解自己的音乐偏好", ("search_web", "search_knowledge", "generate_exploration_report", "record_recommendation_feedback"), "对话回答、探索报告与推荐卡"),
    SkillDefinition("taste_profile_review", "1.0", "用户希望复盘长期偏好或修正推荐方向", ("generate_exploration_report", "record_recommendation_feedback"), "长期偏好解释与可执行探索方向"),
], tool_registry)
