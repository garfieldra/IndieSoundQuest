from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


def camel_case(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.title() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=camel_case, populate_by_name=True)


class CandidatePoolRequest(ApiModel):
    request_id: UUID
    guest_id: str
    size: Literal[16, 32]
    candidate_count: Literal[32, 64] | None = None
    preference_text: str = Field(min_length=3, max_length=1000)
    seed_artist_ids: list[UUID] = Field(default_factory=list)
    confirmed_artists: list["ConfirmedArtist"] = Field(default_factory=list, max_length=8)
    exclude_recording_ids: list[UUID] = Field(default_factory=list)


class ConfirmedArtist(ApiModel):
    mention: str = Field(min_length=1, max_length=120)
    mbid: UUID
    name: str = Field(min_length=1, max_length=160)


IntentMode = Literal["ARTIST_LOCKED", "ARTIST_SEEDED", "OPEN_DISCOVERY"]
IntentConfidence = Literal["low", "medium", "high"]
TerminationReason = Literal[
    "TARGET_REACHED_AND_VALIDATED",
    "ACTIVE_SIZE_REACHED_SHORT_RESERVE",
    "INSUFFICIENT_VERIFIED_CANDIDATES",
    "BUDGET_EXHAUSTED",
    "STAGNATION_LIMIT_REACHED",
    "MODEL_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
    "CONTRACT_REJECTED",
    "ENTITY_CLARIFICATION_REQUIRED",
]


class PreferenceFacets(ApiModel):
    language: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    scene: list[str] = Field(default_factory=list)
    genre: list[str] = Field(default_factory=list)
    era: list[str] = Field(default_factory=list)


class IntentPolicy(ApiModel):
    intent_mode: IntentMode
    allowed_artist_ids: list[UUID] = Field(default_factory=list)
    allowed_artist_names: list[str] = Field(default_factory=list)
    seed_artist_ids: list[UUID] = Field(default_factory=list)
    preference_facets: PreferenceFacets = Field(default_factory=PreferenceFacets)
    confidence: IntentConfidence = "medium"
    evidence_spans: list[str] = Field(default_factory=list, max_length=5)


class CandidateItem(ApiModel):
    recording_id: UUID
    reason: str = Field(min_length=8, max_length=120)
    evidence: list[dict] = Field(default_factory=list)
    exploration_rationale: list[dict] = Field(default_factory=list, max_length=2)
    evidence_summary: list[dict] = Field(default_factory=list, max_length=2)


class CandidatePoolResult(ApiModel):
    request_id: UUID
    status: Literal["needs_clarification", "ready_for_confirmation", "insufficient_candidates", "model_unavailable"]
    size: Literal[16, 32]
    reserve_size: int = 0
    recording_ids: list[UUID] = Field(default_factory=list)
    candidate_summary: str
    items: list[CandidateItem] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    intent_policy: IntentPolicy | None = None
    termination_reason: TerminationReason
    trace_summary: dict = Field(default_factory=dict)
    clarifications: list[dict] = Field(default_factory=list, max_length=8)
