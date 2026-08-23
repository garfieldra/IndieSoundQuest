from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal, TypedDict
from uuid import UUID
import asyncio

from pydantic import BaseModel


class RunContext(TypedDict):
    request_id: UUID
    run_id: UUID
    skill: Literal["candidate_generation", "tournament_report"]
    deadline_at: datetime
    max_tool_calls: int
    max_subagent_calls: int


class ToolCallRecord(BaseModel):
    name: str
    kind: Literal["tool", "subagent"]
    status: Literal["success", "failed", "skipped"]
    duration_ms: int = 0
    error_code: str | None = None


class AgentBlackboard(TypedDict, total=False):
    context: RunContext
    plan: dict[str, Any]
    facts_snapshot: dict[str, Any] | None
    preference_signals: list[dict[str, Any]]
    evidence_registry: list[dict[str, Any]]
    tool_call_history: list[ToolCallRecord]
    draft_output: dict[str, Any] | None
    critique: dict[str, Any] | None
    validation_errors: list[str]
    warnings: list[dict[str, str]]


@dataclass
class RuntimeBudget:
    max_tool_calls: int = 8
    max_subagent_calls: int = 6
    deadline_seconds: int = 90
    tool_calls: int = 0
    subagent_calls: int = 0

    def context(self, request_id: UUID, run_id: UUID, skill: Literal["candidate_generation", "tournament_report"]) -> RunContext:
        return {
            "request_id": request_id,
            "run_id": run_id,
            "skill": skill,
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=self.deadline_seconds),
            "max_tool_calls": self.max_tool_calls,
            "max_subagent_calls": self.max_subagent_calls,
        }

    def check(self, kind: Literal["tool", "subagent"], context: RunContext) -> None:
        if datetime.now(timezone.utc) >= context["deadline_at"]:
            raise RuntimeError("AGENT_DEADLINE_EXCEEDED")
        if kind == "tool":
            if self.tool_calls >= self.max_tool_calls:
                raise RuntimeError("AGENT_TOOL_BUDGET_EXCEEDED")
            self.tool_calls += 1
        else:
            if self.subagent_calls >= self.max_subagent_calls:
                raise RuntimeError("AGENT_SUBAGENT_BUDGET_EXCEEDED")
            self.subagent_calls += 1


async def invoke_with_budget(
    fn: Callable[[], Awaitable[Any]],
    *,
    name: str,
    kind: Literal["tool", "subagent"],
    context: RunContext,
    budget: RuntimeBudget,
    history: list[ToolCallRecord],
    timeout_seconds: int | None = None,
) -> Any:
    started = datetime.now(timezone.utc)
    budget.check(kind, context)
    try:
        # A provider/tool may stall without closing its socket.  Bounded calls let
        # the ReAct supervisor observe a failure and choose another action instead
        # of leaving the whole user request hanging indefinitely.
        # The deterministic fallbacks preserve progress, so a slow LLM must not
        # dominate an interactive candidate request.  Eight seconds is ample for
        # normal structured decisions and keeps online evidence collection moving.
        effective_timeout = timeout_seconds if timeout_seconds is not None else (8 if kind == "subagent" else 120)
        result = await asyncio.wait_for(fn(), timeout=effective_timeout)
        status = "success"
        error_code = None
    except Exception as exc:
        status = "failed"
        error_code = str(exc)[:120]
        raise
    finally:
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        history.append(ToolCallRecord(name=name, kind=kind, status=status, duration_ms=duration_ms, error_code=error_code))
    return result
