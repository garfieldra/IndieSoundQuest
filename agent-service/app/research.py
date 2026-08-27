"""Bounded contracts for optional domestic community research.

Platform pages are never stored or passed through wholesale.  This module is
deliberately independent from LangChain so both business Agents can use the
same validated evidence shape without creating a third business Agent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ResearchProvider = Literal["ZHIHU", "BILIBILI", "DOUBAN", "XIAOHONGSHU_PUBLIC_LINK"]
ResearchSourceType = Literal["ANSWER", "ARTICLE", "VIDEO", "VIDEO_TRANSCRIPT", "MUSIC_SUBJECT", "DOULIST", "PUBLIC_LINK"]
ResearchPurpose = Literal["candidate_discovery", "cultural_context", "recommendation_validation"]
ContentConfidence = Literal["LOW", "MEDIUM"]

_HOSTS: dict[str, tuple[str, ...]] = {
    "ZHIHU": ("zhihu.com",),
    "BILIBILI": ("bilibili.com", "b23.tv"),
    "DOUBAN": ("douban.com",),
    "XIAOHONGSHU_PUBLIC_LINK": ("xiaohongshu.com", "xhslink.com"),
}
_SOURCE_TYPES: dict[str, set[str]] = {
    "ZHIHU": {"ANSWER", "ARTICLE"},
    "BILIBILI": {"VIDEO", "VIDEO_TRANSCRIPT"},
    "DOUBAN": {"MUSIC_SUBJECT", "DOULIST"},
    "XIAOHONGSHU_PUBLIC_LINK": {"PUBLIC_LINK"},
}


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: ResearchProvider
    source_type: ResearchSourceType = Field(alias="sourceType")
    source_url: str = Field(alias="sourceUrl", max_length=2048)
    title: str = Field(min_length=1, max_length=180)
    author_display_name: str | None = Field(default=None, alias="authorDisplayName", max_length=80)
    published_at: datetime | None = Field(default=None, alias="publishedAt")
    snippet: str = Field(min_length=1, max_length=280)
    query_purpose: ResearchPurpose = Field(alias="queryPurpose")
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), alias="retrievedAt")
    content_confidence: ContentConfidence = Field(default="LOW", alias="contentConfidence")
    platform_metadata: dict[str, str | int] = Field(default_factory=dict, alias="platformMetadata")

    @field_validator("title", "author_display_name", "snippet", mode="before")
    @classmethod
    def remove_controls_and_compact(cls, value: object) -> str | None:
        if value is None:
            return None
        return " ".join(str(value).replace("\x00", " ").split())

    @field_validator("platform_metadata", mode="before")
    @classmethod
    def constrain_metadata(cls, value: object) -> dict[str, str | int]:
        if not isinstance(value, dict):
            return {}
        safe = {"bvid", "questionId", "answerId", "subjectId", "doulistId", "contentKind"}
        result: dict[str, str | int] = {}
        for key, raw in value.items():
            if key in safe and isinstance(raw, (str, int)) and len(str(raw)) <= 100:
                result[key] = raw
        return result

    @model_validator(mode="after")
    def validate_platform_url_and_type(self):
        parsed = urlparse(self.source_url)
        host = (parsed.hostname or "").lower()
        allowed = _HOSTS[self.provider]
        if parsed.scheme != "https" or not host or not any(host == base or host.endswith(f".{base}") for base in allowed):
            raise ValueError("sourceUrl must be an https URL belonging to its provider")
        if self.source_type not in _SOURCE_TYPES[self.provider]:
            raise ValueError("sourceType is not allowed for provider")
        return self

    def as_web_source(self) -> dict:
        """Compatibility projection used by existing evidence extraction."""
        return {
            "kind": "domestic_research_source",
            "sourceUrl": self.source_url,
            "sourceTitle": self.title,
            "summary": self.snippet,
            "queryPurpose": self.query_purpose,
            "researchProvider": self.provider,
            "researchSourceType": self.source_type,
            "contentConfidence": self.content_confidence,
        }


def deduplicate_research_sources(sources: list[ResearchSource]) -> list[ResearchSource]:
    unique: dict[tuple[str, str], ResearchSource] = {}
    for source in sources:
        unique.setdefault((source.provider, source.source_url), source)
    return list(unique.values())
