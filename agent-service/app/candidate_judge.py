from __future__ import annotations

import json
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .settings import settings


class CandidateJudgeScores(BaseModel):
    intent_understanding: int = Field(ge=1, le=5)
    preference_relevance: int = Field(ge=1, le=5)
    scope_adherence: int = Field(ge=1, le=5)
    intent_appropriate_variety: int = Field(ge=1, le=5)
    reason_specificity: int = Field(ge=1, le=5)
    pool_coherence: int = Field(ge=1, le=5)


class CandidateJudgeResult(BaseModel):
    scores: CandidateJudgeScores
    rationale: str = Field(min_length=8, max_length=500)
    concerns: list[str] = Field(default_factory=list, max_length=8)


class CandidateQualityJudge:
    """Independent eval-only judge. It is not a user-facing business Agent."""

    def __init__(self) -> None:
        self.model = None if not settings.deepseek_api_key else ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
            temperature=0,
        )

    async def evaluate(self, case: dict[str, Any], result: dict[str, Any], catalog: dict[str, dict]) -> CandidateJudgeResult:
        if self.model is None:
            raise RuntimeError("LLM_JUDGE_API_KEY_MISSING")
        pool = result.get("candidatePool", result)
        recordings = []
        reasons = {str(item.get("recordingId")): item.get("reason", "") for item in pool.get("items", [])}
        for recording_id in pool.get("recordingIds", []):
            item = catalog.get(str(recording_id))
            if item:
                recordings.append({
                    "recordingId": str(recording_id), "title": item.get("title"),
                    "artistName": item.get("artistName"), "albumTitle": item.get("albumTitle"),
                    "reason": reasons.get(str(recording_id), ""),
                })
        payload = {
            "preferenceText": case.get("input", {}).get("preferenceText"),
            "expectedIntent": case.get("expected", {}).get("intentMode"),
            "intentPolicy": pool.get("intentPolicy"),
            "candidateSummary": pool.get("candidateSummary"),
            "recordings": recordings[:64],
        }
        prompt = """你是歌曲世界杯候选池的独立质量评测器，不是生成候选的 Agent。
只依据给定用户输入、类型化意图和受信目录元数据评分，不使用模型记忆补充音乐事实，也不读取或推测思维链。
六个维度各打 1–5 分：intent_understanding、preference_relevance、scope_adherence、intent_appropriate_variety、reason_specificity、pool_coherence。
多样性必须服从意图；明确单艺人锁定时，不得因艺人单一扣分。rationale 简述整体依据，concerns 只列可观察问题。
输入：""" + json.dumps(payload, ensure_ascii=False)
        structured = self.model.with_structured_output(CandidateJudgeResult)
        return await structured.ainvoke(prompt)
