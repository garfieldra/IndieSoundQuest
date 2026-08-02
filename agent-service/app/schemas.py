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
    preference_text: str = Field(min_length=3, max_length=1000)
    seed_artist_ids: list[UUID] = []
    exclude_recording_ids: list[UUID] = []


class CandidateItem(ApiModel):
    recording_id: UUID
    reason: str
    evidence: list[dict] = []


class CandidatePoolResult(ApiModel):
    request_id: UUID
    status: Literal["ready_for_confirmation", "insufficient_candidates", "model_unavailable"]
    size: Literal[16, 32]
    recording_ids: list[UUID] = []
    candidate_summary: str
    items: list[CandidateItem] = []
    warnings: list[dict] = []
