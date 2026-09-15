"""Runtime artifacts for evidence-aware music analysis.

These models describe a mutable workspace and public claims.  They do not
encode an execution order: the unified conversation Agent remains responsible
for choosing, repeating, skipping, or stopping research actions.
"""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .schemas import ConversationAgentRequest


ClaimType = Literal["FACT", "OBSERVATION", "INTERPRETATION", "LISTENER_INFERENCE"]
ClaimStatus = Literal["supported", "partial", "conflicting", "unsupported"]
SourceQualityTier = Literal["A", "B", "C", "D"]


class EvidenceClaim(BaseModel):
    statement: str = Field(min_length=12, max_length=420)
    claim_type: ClaimType
    dimension: str = Field(min_length=2, max_length=80)
    evidence_refs: list[str] = Field(default_factory=list, max_length=6)
    confidence: Literal["high", "medium", "low"] = "medium"
    boundary: str = Field(default="", max_length=300)
    status: ClaimStatus = "partial"


class MusicAnalysisWorkspace(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=1000)
    working_thesis: str = Field(default="", max_length=600)
    selected_dimensions: list[str] = Field(default_factory=list, max_length=8)
    observations: list[str] = Field(default_factory=list, max_length=12)
    claims: list[EvidenceClaim] = Field(default_factory=list, max_length=10)
    open_questions: list[str] = Field(default_factory=list, max_length=8)
    source_conflicts: list[str] = Field(default_factory=list, max_length=6)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    possible_next_actions: list[str] = Field(default_factory=list, max_length=6)
    evidence_source_count: int = Field(default=0, ge=0, le=100)
    revision: int = Field(default=1, ge=1)


class MusicAnalysisPlan(BaseModel):
    """A model-authored revision of the workspace, not an execution DAG."""

    subject: str = Field(min_length=1, max_length=300)
    resolved_question: str = Field(min_length=1, max_length=1000)
    working_thesis: str = Field(default="", max_length=600)
    selected_dimensions: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    open_questions: list[str] = Field(default_factory=list, max_length=8)
    possible_next_actions: list[str] = Field(default_factory=list, max_length=6)


class MusicAnalysisDraft(BaseModel):
    core_judgment: str = Field(min_length=24, max_length=600)
    answer: str = Field(min_length=200, max_length=5000)
    claims: list[EvidenceClaim] = Field(default_factory=list, min_length=1, max_length=10)
    uncertainties: list[str] = Field(default_factory=list, max_length=6)
    related_works: list[str] = Field(default_factory=list, max_length=3)


class EvidenceClaimReview(BaseModel):
    claim_index: int = Field(ge=0, le=9)
    verdict: ClaimStatus
    supported_refs: list[str] = Field(default_factory=list, max_length=6)
    rationale: str = Field(min_length=8, max_length=300)
    revised_statement: str | None = Field(default=None, min_length=12, max_length=420)
    boundary: str = Field(default="", max_length=300)


class MusicAnalysisReview(BaseModel):
    revised_answer: str = Field(min_length=200, max_length=5000)
    revised_core_judgment: str | None = Field(default=None, min_length=24, max_length=600)
    items: list[EvidenceClaimReview] = Field(default_factory=list, min_length=1, max_length=10)
    conflicts: list[str] = Field(default_factory=list, max_length=6)
    review_summary: str = Field(min_length=12, max_length=400)


class AnalysisEvidenceCoverage(BaseModel):
    covered_dimensions: list[str] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    source_count: int = 0
    independent_domain_count: int = 0
    full_text_source_count: int = 0
    requires_full_text: bool = False
    ready_for_fact_heavy_answer: bool = False
    blocking_gaps: list[str] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)


def classify_source_quality(source: dict) -> dict:
    """Return a transparent heuristic tier; this never proves a claim true."""
    host = (urlparse(str(source.get("sourceUrl") or source.get("url") or "")).hostname or "").lower()
    title = str(source.get("sourceTitle") or source.get("title") or "").lower()
    has_excerpt = len(str(source.get("pageExcerpt") or source.get("excerpt") or "").strip()) >= 120
    if host.endswith(("musicbrainz.org", "discogs.com")):
        tier, score, kind = "A", 0.88, "structured_catalog"
    elif host.endswith(("wikipedia.org", "wikimedia.org", "wikidata.org")):
        tier, score, kind = "B", 0.74, "reference"
    elif host.endswith("last.fm"):
        tier, score, kind = "C", 0.58, "listener_signal"
    elif host.endswith(("bandcamp.com", "soundcloud.com", "youtube.com", "youtu.be")):
        tier, score, kind = "B", 0.72, "artist_or_release_platform"
    elif host.endswith(("zhihu.com", "douban.com", "bilibili.com")):
        tier, score, kind = "C", 0.56, "community"
    elif has_excerpt and re.search(r"采访|访谈|专访|制作|录音|credits?|interview", title, re.I):
        tier, score, kind = "B", 0.70, "editorial_or_interview"
    elif has_excerpt:
        tier, score, kind = "C", 0.60, "readable_web_page"
    else:
        tier, score, kind = "D", 0.38, "search_snippet"
    return {"tier": tier, "score": score, "kind": kind, "hasFullTextExcerpt": has_excerpt}


def apply_claim_reviews(
    claims: list[EvidenceClaim], reviews: list[EvidenceClaimReview],
    allowed_refs: set[str], source_quality: dict[str, dict],
) -> tuple[list[EvidenceClaim], dict]:
    """Apply model verdicts while enforcing references and confidence in code."""
    review_by_index = {item.claim_index: item for item in reviews if item.claim_index < len(claims)}
    output: list[EvidenceClaim] = []
    reviewed = 0
    for index, claim in enumerate(claims):
        review = review_by_index.get(index)
        if review is None:
            output.append(sanitize_claims([claim], allowed_refs)[0])
            continue
        reviewed += 1
        refs = [ref for ref in review.supported_refs if ref in allowed_refs]
        verdict = review.verdict
        if verdict == "supported" and not refs:
            verdict = "unsupported"
        values = claim.model_dump()
        values["evidence_refs"] = refs
        values["status"] = verdict
        if review.revised_statement:
            values["statement"] = review.revised_statement
        values["boundary"] = review.boundary or claim.boundary
        best_score = max((float(source_quality.get(ref, {}).get("score", 0)) for ref in refs), default=0)
        if verdict == "supported":
            values["confidence"] = "high" if best_score >= 0.75 and len(refs) >= 2 else "medium"
        elif verdict in {"partial", "conflicting"}:
            values["confidence"] = "medium" if best_score >= 0.7 else "low"
        else:
            values["confidence"] = "low"
            values["boundary"] = values["boundary"] or "引用正文不足以支持这项表述。"
        output.append(EvidenceClaim.model_validate(values))
    counts = {status: sum(1 for item in output if item.status == status) for status in ("supported", "partial", "conflicting", "unsupported")}
    return output, {"semanticReviewApplied": reviewed > 0, "reviewedClaimCount": reviewed, "verdictCounts": counts}


def deep_analysis_intent(request: ConversationAgentRequest) -> bool:
    """Detect a strong analysis contract, including terse follow-up turns.

    This is a product guard and degraded-mode fallback, not the semantic
    planner.  The model is still free to choose the actual research actions.
    """
    text = request.user_message.strip()
    broad_preference_request = bool(re.search(
        r"(?:分析|总结|看看).{0,12}(?:我的|本轮|这次).{0,10}(?:音乐)?偏好|"
        r"(?:生成|整理|写).{0,8}(?:探索|偏好|音乐)?报告",
        text,
        re.I,
    ))
    concrete_music_subject = bool(re.search(
        r"《[^》]+》|这首歌|这首曲|这张专辑|这个版本|歌词|编曲|配器|旋律|和声|节奏|音色|演唱|制作|混音",
        text,
        re.I,
    ))
    direct = bool(re.search(
        r"深入(?:分析|解读)|详细(?:分析|解读)|细致(?:分析|解读)|"
        r"分析一下|解读一下|具体分析|具体体现|为什么.{0,40}(?:听起来|感觉|会|让人|显得)|"
        r"(?:歌词|编曲|配器|旋律|和声|节奏|音色|演唱|制作|混音|声场|空间感|创作背景).{0,24}(?:分析|解读|作用|表达|体现|区别|关系)|"
        r"(?:这首歌|这首曲|这张专辑|这个版本).{0,28}(?:表达|意味着|位置|特别|亲密|压抑|疏离)|"
        r"(?:比较|对比).{0,60}(?:歌曲|作品|版本|专辑|时期)|"
        r"(?:歌曲|作品|版本|专辑|时期).{0,60}(?:比较|对比|区别)",
        text, re.I,
    ))
    if direct and not (broad_preference_request and not concrete_music_subject):
        return True
    has_analysis_context = any(
        isinstance(card, dict) and card.get("cardType") == "MUSIC_ANALYSIS"
        for card in request.recent_cards
    )
    return has_analysis_context and bool(re.search(
        r"为什么|具体|展开|深挖|换个角度|另一种解释|依据|证据|哪里|怎么体现|继续分析|再分析",
        text, re.I,
    ))


def latest_analysis_context(request: ConversationAgentRequest) -> dict:
    """Return only the latest persisted, user-visible analysis artifact."""
    for card in reversed(request.recent_cards):
        if not isinstance(card, dict) or card.get("cardType") != "MUSIC_ANALYSIS":
            continue
        payload = card.get("payload")
        if isinstance(payload, dict):
            return {"messageId": str(card.get("messageId") or ""), "payload": payload}
    return {}


def inherited_analysis_sources(request: ConversationAgentRequest) -> list[dict]:
    """Rehydrate prior public evidence so a follow-up need not research from zero."""
    context = latest_analysis_context(request)
    output: list[dict] = []
    for item in list(context.get("payload", {}).get("sources") or [])[:12]:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        output.append({
            "sourceTitle": str(item.get("title") or "公开音乐资料")[:180],
            "sourceUrl": str(item.get("url") or "")[:1000],
            "summary": str(item.get("summary") or "")[:1000],
            "searchProvider": str(item.get("provider") or "analysis-memory")[:40],
            "inheritedFromAnalysisCard": True,
        })
    return output


def _inherited_claims(payload: dict) -> list[EvidenceClaim]:
    output: list[EvidenceClaim] = []
    for item in list(payload.get("claims") or [])[:10]:
        if not isinstance(item, dict) or not item.get("statement"):
            continue
        try:
            output.append(EvidenceClaim.model_validate({
                "statement": item.get("statement"),
                "claim_type": item.get("claimType") or "INTERPRETATION",
                "dimension": item.get("dimension") or "既有分析",
                "evidence_refs": item.get("evidenceRefs") or [],
                "confidence": item.get("confidence") or "low",
                "boundary": item.get("boundary") or "",
                "status": item.get("status") or "partial",
            }))
        except Exception:
            continue
    return output


def initial_analysis_workspace(request: ConversationAgentRequest) -> MusicAnalysisWorkspace:
    text = request.user_message.strip()
    previous = latest_analysis_context(request)
    previous_payload = previous.get("payload", {})
    quoted = re.findall(r"《([^》]{1,100})》", text)
    previous_subject = str(previous_payload.get("subject") or "").strip()
    subject = "、".join(quoted[:3]) if quoted else (previous_subject or text[:180])
    dimension_terms = {
        "版本与身份": r"版本|录音室|现场版|重制|翻唱|credits|词曲作者|制作人|编曲者",
        "歌词与叙事": r"歌词|叙事|意象|称谓|文本|词作",
        "编曲与音色": r"编曲|配器|音色|乐器|留白",
        "旋律与和声": r"旋律|和声|调性|张力",
        "节奏与律动": r"节奏|律动|速度|重拍",
        "演唱表达": r"演唱|唱法|咬字|气声|音域|人声",
        "制作与空间": r"制作|混音|混响|声场|空间感|失真|压缩",
        "创作与专辑语境": r"创作背景|专辑|生涯|时期|时代|地域|采访",
        "横向比较": r"比较|对比|区别|不同版本|相似",
    }
    selected = [label for label, pattern in dimension_terms.items() if re.search(pattern, text, re.I)]
    if not selected:
        selected = list(previous_payload.get("selectedDimensions") or [])[:5]
    if not selected:
        selected = ["核心表达", "与问题直接相关的声音或文本线索"]
    previous_question = str(previous_payload.get("question") or "").strip()
    resolved_question = (
        f"此前问题：{previous_question or previous_payload.get('coreJudgment', '')}\n本轮追问：{text}"
        if previous else text
    )
    previous_judgment = str(previous_payload.get("coreJudgment") or "").strip()
    return MusicAnalysisWorkspace(
        subject=subject,
        question=resolved_question[:1000],
        working_thesis=previous_judgment[:600],
        selected_dimensions=selected[:5],
        observations=([f"继承上一轮核心判断：{previous_judgment}"] if previous_judgment else []),
        claims=_inherited_claims(previous_payload),
        open_questions=[f"哪些可核验资料能够支持本轮关于“{subject[:100]}”的追问？"],
        possible_next_actions=["检索与核心问题直接相关的公开资料", "在证据足够时形成分析"],
        evidence_source_count=len(inherited_analysis_sources(request)),
        revision=max(1, int(previous_payload.get("workspaceRevision") or 0) + 1),
    )


def workspace_after_research(
    workspace: dict | MusicAnalysisWorkspace, source_count: int, observation: str
) -> MusicAnalysisWorkspace:
    current = workspace if isinstance(workspace, MusicAnalysisWorkspace) else MusicAnalysisWorkspace.model_validate(workspace)
    values = current.model_dump()
    values["evidence_source_count"] = max(current.evidence_source_count, source_count)
    values["observations"] = [*current.observations, observation][-12:]
    values["possible_next_actions"] = ["继续补足会改变结论的证据", "形成当前证据边界内的分析"]
    values["revision"] = current.revision + 1
    return MusicAnalysisWorkspace.model_validate(values)


_DIMENSION_EVIDENCE_PATTERNS = {
    "版本与身份": r"版本|录音室|现场|重制|翻唱|词曲|制作人|编曲|credits?|version|live|remaster|cover",
    "歌词与叙事": r"歌词|文本|叙事|意象|主题|词作|作词",
    "编曲与音色": r"编曲|配器|乐器|音色|arrang|instrument",
    "旋律与和声": r"旋律|和声|调性|melod|harmon|chord",
    "节奏与律动": r"节奏|律动|速度|鼓|rhythm|tempo|beat",
    "演唱表达": r"演唱|唱腔|唱法|人声|咬字|vocal|voice",
    "制作与空间": r"制作|制作人|录音|混音|母带|声场|producer|production|mix|master",
    "创作与专辑语境": r"创作|专辑|发行|采访|幕后|背景|生涯|时期|词曲|credits?|interview",
    "横向比较": r"比较|对比|版本|翻唱|现场|时期|version|live|cover",
}

_DIMENSION_QUERY_HINTS = {
    "版本与身份": "版本 词曲 编曲 制作人 credits",
    "歌词与叙事": "歌词主题 叙事 词作者 访谈",
    "编曲与音色": "编曲 配器 乐器 制作解析",
    "旋律与和声": "旋律 和声 作曲 分析",
    "节奏与律动": "节奏 律动 鼓 编曲分析",
    "演唱表达": "演唱 唱法 人声 录音 访谈",
    "制作与空间": "制作人 编曲 混音 录音 credits",
    "创作与专辑语境": "创作背景 专辑 采访 制作人 credits",
    "横向比较": "版本 比较 现场 录音室 专辑时期",
}


def assess_analysis_evidence(
    workspace: dict | MusicAnalysisWorkspace, sources: list[dict]
) -> AnalysisEvidenceCoverage:
    """Describe evidence gaps without prescribing a research sequence."""
    current = workspace if isinstance(workspace, MusicAnalysisWorkspace) else MusicAnalysisWorkspace.model_validate(workspace)
    covered: list[str] = []
    missing: list[str] = []
    for dimension in current.selected_dimensions:
        pattern = _DIMENSION_EVIDENCE_PATTERNS.get(dimension)
        if not pattern:
            continue
        has_evidence = any(re.search(
            pattern,
            " ".join(str(source.get(field) or "") for field in ("sourceTitle", "summary", "pageExcerpt")),
            re.I,
        ) for source in sources)
        (covered if has_evidence else missing).append(dimension)

    domains = {
        urlparse(str(source.get("sourceUrl") or "")).hostname
        for source in sources if source.get("sourceUrl")
    }
    domains.discard(None)
    full_text_count = sum(1 for source in sources if len(str(source.get("pageExcerpt") or "").strip()) >= 120)
    requires_full_text = bool(re.search(
        r"创作背景|创作过程|采访|幕后|制作人|编曲者|词曲作者|credits?|录音|混音|为什么写|怎么写",
        current.question,
        re.I,
    ))
    blocking: list[str] = []
    if requires_full_text and full_text_count == 0:
        blocking.append("尚未精读能够支持创作或制作事实的原始页面")
    if requires_full_text and len(domains) < 2:
        blocking.append("创作或制作事实尚缺少第二个独立来源")
    if missing:
        blocking.append("仍缺少与问题相关的证据维度：" + "、".join(missing))

    subject = current.subject[:120]
    suggestions = [f"{subject} {_DIMENSION_QUERY_HINTS[item]}" for item in missing if item in _DIMENSION_QUERY_HINTS]
    if requires_full_text and not suggestions:
        suggestions.append(f"{subject} 创作背景 采访 制作人 credits")
    return AnalysisEvidenceCoverage(
        covered_dimensions=covered,
        missing_dimensions=missing,
        source_count=len(sources),
        independent_domain_count=len(domains),
        full_text_source_count=full_text_count,
        requires_full_text=requires_full_text,
        ready_for_fact_heavy_answer=not blocking,
        blocking_gaps=blocking,
        suggested_queries=list(dict.fromkeys(suggestions))[:4],
    )


def workspace_with_coverage(
    workspace: dict | MusicAnalysisWorkspace, coverage: AnalysisEvidenceCoverage
) -> MusicAnalysisWorkspace:
    current = workspace if isinstance(workspace, MusicAnalysisWorkspace) else MusicAnalysisWorkspace.model_validate(workspace)
    values = current.model_dump()
    values["open_questions"] = coverage.blocking_gaps[:8]
    actions: list[str] = []
    if coverage.suggested_queries:
        actions.append("按证据缺口选择新的公开资料检索切面")
    if coverage.source_count and coverage.full_text_source_count == 0:
        actions.append("精读最可能支持核心事实的公开来源")
    actions.append("证据足够时形成分析；不足时明确边界")
    values["possible_next_actions"] = actions[:6]
    values["revision"] = current.revision + 1
    return MusicAnalysisWorkspace.model_validate(values)


def sanitize_claims(claims: list[EvidenceClaim], allowed_refs: set[str]) -> list[EvidenceClaim]:
    sanitized: list[EvidenceClaim] = []
    for claim in claims:
        refs = [value for value in claim.evidence_refs if value in allowed_refs]
        values = claim.model_dump()
        values["evidence_refs"] = refs
        if claim.claim_type == "FACT" and not refs:
            values["status"] = "unsupported"
            values["confidence"] = "low"
            values["boundary"] = claim.boundary or "当前公开资料不足以核验这项事实。"
        sanitized.append(EvidenceClaim.model_validate(values))
    return sanitized
