from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=lambda value: value.split("_")[0] + "".join(part.title() for part in value.split("_")[1:]), populate_by_name=True)


class TournamentReportRequest(ReportApiModel):
    request_id: UUID
    report_id: UUID
    tournament_id: UUID
    guest_id: str
    tournament_version: int = 1
    include_personality_easter_egg: bool = True


class EvidenceRef(ReportApiModel):
    evidence_id: str
    source_type: Literal["match", "vote", "catalog", "knowledge", "web"]
    source_id: str | None = None
    source_url: str | None = None


class PreferenceDimension(ReportApiModel):
    name: str = Field(min_length=1, max_length=30)
    confidence: Literal["low", "medium", "high"]
    explanation: str = Field(min_length=20, max_length=240)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=5)


class SongRecommendation(ReportApiModel):
    # catalog_verified：来自 Java 音乐目录；web_discovered：网络发现，尚未写入目录。
    source_status: Literal["catalog_verified", "web_discovered"] = "catalog_verified"
    recording_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    artist_name: str | None = Field(default=None, min_length=1, max_length=120)
    source_url: str | None = Field(default=None, min_length=8, max_length=1000)
    source_title: str | None = Field(default=None, min_length=1, max_length=300)
    search_query: str | None = Field(default=None, min_length=1, max_length=300)
    reason: str = Field(min_length=8, max_length=160)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_origin(self):
        if self.source_status == "catalog_verified" and self.recording_id is None:
            raise ValueError("catalog_verified recommendation requires recordingId")
        if self.source_status == "web_discovered" and not all([self.title, self.artist_name, self.source_url, self.source_title, self.search_query]):
            raise ValueError("web_discovered recommendation requires title, artistName, sourceUrl, sourceTitle and searchQuery")
        return self


class ArtistRecommendation(ReportApiModel):
    source_status: Literal["catalog_verified", "web_discovered"] = "catalog_verified"
    artist_id: UUID | None = None
    artist_name: str | None = Field(default=None, min_length=1, max_length=120)
    source_url: str | None = Field(default=None, min_length=8, max_length=1000)
    source_title: str | None = Field(default=None, min_length=1, max_length=300)
    search_query: str | None = Field(default=None, min_length=1, max_length=300)
    reason: str = Field(min_length=8, max_length=160)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_origin(self):
        if self.source_status == "catalog_verified" and self.artist_id is None:
            raise ValueError("catalog_verified recommendation requires artistId")
        if self.source_status == "web_discovered" and not all([self.artist_name, self.source_url, self.source_title, self.search_query]):
            raise ValueError("web_discovered recommendation requires artistName, sourceUrl, sourceTitle and searchQuery")
        return self


class PreferenceReport(ReportApiModel):
    schema_version: Literal["1.0"]
    tournament_id: UUID
    tournament_version: int
    summary: str = Field(min_length=80, max_length=420)
    dimensions: list[PreferenceDimension] = Field(min_length=3, max_length=5)
    song_recommendations: list[SongRecommendation] = Field(min_length=5, max_length=7)
    # 本地目录可能只有单一艺人；推荐验证阶段会尽量补足 2–3 位，数据不足时通过 warnings 明示降级。
    artist_recommendations: list[ArtistRecommendation] = Field(max_length=3)
    # A small, user-visible summary of approved local theme-card context. It is
    # explanatory only: it never authorizes a recommendation by itself.
    exploration_tags: list[str] = Field(default_factory=list, max_length=5)
    personality_easter_egg: str = Field(min_length=80, max_length=120)
    disclaimer: str = Field(min_length=20, max_length=180)
    warnings: list[str] = Field(default_factory=list)


class CritiqueResult(ReportApiModel):
    passed: bool
    issues: list[str] = Field(default_factory=list, max_length=12)
    risk_level: Literal["low", "medium", "high"]
