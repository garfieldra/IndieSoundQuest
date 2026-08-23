from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph

from .report_llm import ReportDecision, ReportGenerator
from .report_schemas import CritiqueResult, PreferenceReport, TournamentReportRequest
from .runtime import AgentBlackboard, RuntimeBudget, invoke_with_budget
from .subagents import CriticSubagent, EvidenceRegistry, NetworkResearchSubagent, PreferenceAnalysisSubagent, RecommendationValidationSubagent
from .tools import KnowledgeSearchTool, TournamentFactsTool, WebSearchTool


class ReportState(TypedDict, total=False):
    request: TournamentReportRequest
    board: AgentBlackboard
    result: PreferenceReport
    error_code: str
    decision: ReportDecision
    iteration: int
    action_history: list[dict]


def _extract_signals(facts: dict) -> list[dict]:
    entries = {str(item["entryId"]): item for item in facts.get("entries", [])}
    matches = facts.get("matches", [])
    winners = [entries.get(str(match.get("winnerEntryId"))) for match in matches]
    winners = [item for item in winners if item]
    artist_counts: dict[str, int] = {}
    for item in winners:
        artist_counts[item["artistName"]] = artist_counts.get(item["artistName"], 0) + 1
    evidence = [
        {"evidenceId": str(match["matchId"]), "sourceType": "match", "sourceId": str(match["matchId"])}
        for match in matches[:5]
    ]
    signals = [{"name": "晋级选择轨迹", "confidence": "medium", "description": "你在多轮一对一选择中反复让这些作品进入下一轮，说明它们构成了本场最稳定的偏好线索。", "evidence": evidence}]
    if len(artist_counts) == 1 and artist_counts:
        signals.append({"name": "艺人集中偏好", "confidence": "medium", "description": f"晋级作品主要集中在{next(iter(artist_counts))}，本场结果更适合从这位艺人的不同阶段和不同专辑继续展开。", "evidence": evidence[:2]})
    else:
        signals.append({"name": "跨艺人探索倾向", "confidence": "low", "description": "晋级路径没有完全集中于单一艺人，说明你可能愿意沿着相近气质跨艺人继续探索，而不只停留在熟悉的名字里。", "evidence": evidence[:2]})
    signals.append({"name": "选择的辨识度", "confidence": "low", "description": "冠军与完整晋级路径共同构成了本场最有辨识度的选择轨迹，可以作为下一轮探索时回看的参照坐标。", "evidence": evidence[:2]})
    return signals


def _fallback_report(facts: dict, signals: list[dict], request: TournamentReportRequest) -> PreferenceReport:
    entries = facts.get("entries", [])
    matches = facts.get("matches", [])
    by_id = {str(item["entryId"]): item for item in entries}
    winner = by_id.get(str(matches[-1]["winnerEntryId"])) if matches else None
    winner_text = f"本场冠军是《{winner['title']}》" if winner else "本场赛事已完成"
    evidence = [{"evidenceId": str(item["matchId"]), "sourceType": "match", "sourceId": str(item["matchId"])} for item in matches[:3]]
    song_items = [item for item in entries if item.get("recordingId") and item != winner][:5]
    artist_ids: list[str] = []
    artist_names: list[str] = []
    for item in entries:
        if item.get("artistId") and str(item["artistId"]) not in artist_ids:
            artist_ids.append(str(item["artistId"]))
            artist_names.append(item.get("artistName", "相关艺人"))
    # The fallback is deliberately conservative: it is only used when the model is unavailable.
    return PreferenceReport.model_validate({
        "schemaVersion": "1.0", "tournamentId": str(request.tournament_id), "tournamentVersion": request.tournament_version,
        "summary": f"{winner_text}。这份报告基于本场{facts.get('size', request.tournament_version)}首歌曲世界杯的完整投票轨迹生成：它记录的是你在一次次相邻比较里愿意留下什么，而不是给你的现实身份下定义。以下观察只描述本场选择中出现的音乐倾向，并为下一轮探索提供可验证的入口。",
        "dimensions": [
            {"name": signal["name"], "confidence": signal["confidence"], "explanation": signal["description"], "evidence": evidence[:2] or [{"evidenceId": "facts", "sourceType": "vote"}]}
            for signal in signals[:3]
        ],
        "songRecommendations": [{"recordingId": str(item["recordingId"]), "reason": "这首歌来自本场已验证的候选目录，可作为继续比较和探索的入口。", "evidence": evidence[:1]} for item in song_items],
        "artistRecommendations": [{"artistId": artist_id, "reason": f"你在本场赛事中多次接触到{artist_names[index]}的作品，可以从这位艺人的其他作品继续探索。", "evidence": evidence[:1]} for index, artist_id in enumerate(artist_ids[:2])],
        "personalityEasterEgg": "你在这场比赛里更像一位会反复比较细节、也愿意让一首歌慢慢证明自己的听众：先保留微妙的差异，再在关键轮次相信真正留下来的感觉。这只是基于本场音乐选择的娱乐性观察，不代表稳定的人格或现实身份结论。",
        "disclaimer": "仅基于本场歌曲世界杯的音乐选择，用于娱乐和音乐探索，不代表心理、教育、职业或现实身份结论。",
        "warnings": ["MODEL_UNAVAILABLE_FALLBACK"]
    })


def _critique(report: PreferenceReport, facts: dict) -> CritiqueResult:
    valid_ids = {str(item.get("recordingId")) for item in facts.get("entries", [])}
    issues: list[str] = []
    if any(str(item.recording_id) not in valid_ids for item in report.song_recommendations):
        issues.append("RECOMMENDATION_ENTITY_NOT_IN_FACTS")
    if not report.disclaimer or "仅基于" not in report.disclaimer:
        issues.append("DISCLAIMER_MISSING")
    return CritiqueResult(passed=not issues, issues=issues, risk_level="high" if issues else "low")


def _knowledge_query(facts: dict, signals: list[dict]) -> str:
    entries = {str(item.get("entryId")): item for item in facts.get("entries", [])}
    winners = [entries.get(str(match.get("winnerEntryId"))) for match in facts.get("matches", [])]
    titles = [str(item.get("title", "")) for item in winners if item][:4]
    artists = [str(item.get("artistName", "")) for item in winners if item][:3]
    dimensions = [str(item.get("name", "")) for item in signals][:3]
    return " ".join(part for part in [*titles, *artists, *dimensions] if part)[:500]


def _safe_knowledge_context(items: list[dict]) -> tuple[list[dict], list[str]]:
    context, tags = [], []
    for item in items[:6]:
        themes = [str(value)[:30] for value in item.get("themes", [])[:3] if str(value).strip()]
        moods = [str(value)[:30] for value in item.get("moods", [])[:2] if str(value).strip()]
        scenes = [str(value)[:30] for value in item.get("scenes", [])[:2] if str(value).strip()]
        context.append({"title": str(item.get("title", ""))[:160], "artist": str(item.get("artist", ""))[:120], "themes": themes, "moods": moods, "scenes": scenes, "summary": str(item.get("summary", ""))[:160]})
        for tag in [*themes, *moods, *scenes]:
            if tag not in tags: tags.append(tag)
    return context, tags[:5]


def _next_eligible_action(summary: dict) -> str:
    """Break an LLM no-op loop without imposing a normal fixed workflow.

    The supervisor remains responsible for every useful choice. This helper is
    reached only when it asks for an action whose required evidence is already
    present; it then advances to missing evidence or a quality gate instead of
    wasting the bounded ReAct budget on a duplicate action.
    """
    if not summary["analyzed"]:
        return "analyze_tournament"
    if not summary["webSearched"]:
        return "search_web"
    if not summary["knowledgeSearched"]:
        return "search_knowledge"
    if not summary["drafted"]:
        return "draft_report"
    if not summary["critiqued"]:
        return "critique_report"
    return "submit_report"


def build_report_graph(facts_tool: TournamentFactsTool, generator: ReportGenerator, web: WebSearchTool, knowledge: KnowledgeSearchTool):
    preference_analyst = PreferenceAnalysisSubagent()
    network_researcher = NetworkResearchSubagent(web)
    recommendation_validator = RecommendationValidationSubagent()
    critic_agent = CriticSubagent()

    async def initialize(state: ReportState):
        request = state["request"]
        budget = RuntimeBudget(max_tool_calls=8, max_subagent_calls=8, deadline_seconds=120)
        context = budget.context(request.request_id, uuid4(), "tournament_report")
        history = []
        facts = await invoke_with_budget(
            lambda: facts_tool.get(request.tournament_id, request.guest_id),
            name="get_tournament_facts", kind="tool", context=context, budget=budget, history=history,
        )
        board: AgentBlackboard = {
            "context": context, "runtime_budget": budget, "facts_snapshot": facts,
            "tool_call_history": history, "evidence_registry": [], "warnings": [],
        }
        return {"board": board, "iteration": 0, "action_history": []}

    async def supervisor(state: ReportState):
        board = state["board"]
        summary = {
            "analyzed": bool(board.get("preference_signals")),
            "webSearched": "network_sources" in board,
            "webSourceCount": len(board.get("network_sources", [])),
            "knowledgeSearched": "knowledge_context" in board,
            "knowledgeCount": len(board.get("knowledge_context", [])),
            "drafted": "result" in state,
            "critiqued": bool(board.get("critique")),
            "critiquePassed": bool(board.get("critique", {}).get("passed")),
            "validationErrors": board.get("validation_errors", []),
            "iteration": state["iteration"],
            "remainingIterations": max(0, 9 - state["iteration"]),
            "recentActions": state["action_history"][-4:],
            "musicContext": {
                "artists": sorted({str(item.get("artistName", "")) for item in board["facts_snapshot"].get("entries", []) if item.get("artistName")})[:4],
                "recentWinners": [str(item.get("title", "")) for item in board["facts_snapshot"].get("entries", [])[:3]],
            },
        }
        try:
            decision = await generator.decide(summary)
        except Exception:
            if not summary["analyzed"]:
                action = "analyze_tournament"
            elif not summary["drafted"]:
                action = "draft_report"
            elif not summary["critiqued"]:
                action = "critique_report"
            else:
                action = "submit_report"
            decision = ReportDecision(action=action, decision_summary="模型决策暂不可用，采用受控降级动作")
        duplicate_actions = {
            "analyze_tournament": summary["analyzed"],
            "search_web": summary["webSearched"],
            "search_knowledge": summary["knowledgeSearched"],
        }
        if duplicate_actions.get(decision.action, False):
            decision = ReportDecision(
                action=_next_eligible_action(summary),
                decision_summary="检测到重复的无收益动作，改为补齐当前缺失的证据或质量门。",
            )
        # Mandatory report quality gates remain runtime guardrails, not a fixed orchestration path.
        if decision.action == "submit_report" and not summary["drafted"]:
            decision = ReportDecision(action="draft_report", decision_summary="提交前必须先生成报告草稿")
        if decision.action == "submit_report" and not summary["critiqued"]:
            decision = ReportDecision(action="critique_report", decision_summary="提交前必须经过独立审查")
        if state["iteration"] >= 8:
            if summary["drafted"] and summary["critiqued"]:
                decision = ReportDecision(action="submit_report", decision_summary="达到预算，提交已审查报告")
            else:
                decision = ReportDecision(action="finish_degraded", decision_summary="达到预算，生成并审查保守报告")
        return {
            "decision": decision,
            "iteration": state["iteration"] + 1,
            "action_history": state["action_history"] + [{"action": decision.action, "summary": decision.decision_summary}],
        }

    async def execute(state: ReportState):
        request, board = state["request"], state["board"]
        action = state["decision"].action
        context, budget, history = board["context"], board["runtime_budget"], board["tool_call_history"]

        if action == "analyze_tournament":
            registry = EvidenceRegistry()
            signals = await invoke_with_budget(
                lambda: preference_analyst.analyze(board["facts_snapshot"], registry),
                name="preference_analysis", kind="subagent", context=context, budget=budget, history=history,
            )
            board["preference_signals"] = signals
            board["evidence_registry"] = registry.values()
            return {"board": board}

        if action == "search_web":
            registry = EvidenceRegistry()
            signals = board.get("preference_signals") or _extract_signals(board["facts_snapshot"])
            try:
                sources = await invoke_with_budget(
                    lambda: network_researcher.research(board["facts_snapshot"], signals, registry, state["decision"].query),
                    name="network_research", kind="subagent", context=context, budget=budget, history=history,
                )
            except Exception:
                sources = []
            board["network_sources"] = sources
            board["evidence_registry"].extend(registry.values())
            if not sources:
                board["warnings"].append({"code": "WEB_RESEARCH_UNAVAILABLE", "message": "网络研究未返回可用资料。"})
            return {"board": board}

        if action == "search_knowledge":
            signals = board.get("preference_signals") or _extract_signals(board["facts_snapshot"])
            try:
                raw_context = await invoke_with_budget(
                    lambda: knowledge.search_verified(_knowledge_query(board["facts_snapshot"], signals), []),
                    name="search_knowledge", kind="tool", context=context, budget=budget, history=history,
                )
            except Exception:
                raw_context = []
            context_items, tags = _safe_knowledge_context(raw_context)
            board["knowledge_context"] = context_items
            board["knowledge_tags"] = tags
            return {"board": board}

        if action == "draft_report":
            signals = board.get("preference_signals") or _extract_signals(board["facts_snapshot"])
            try:
                report = await invoke_with_budget(
                    lambda: generator.generate(board["facts_snapshot"], signals, request.tournament_id, request.tournament_version, board.get("network_sources", []), board.get("knowledge_context", [])),
                    name="draft_report", kind="subagent", context=context, budget=budget, history=history,
                )
            except Exception:
                report = None
            if report is None:
                report = _fallback_report(board["facts_snapshot"], signals, request)
            try:
                web_songs, web_artists = await invoke_with_budget(
                    lambda: generator.discover_from_web(board.get("network_sources", [])),
                    name="network_discovery", kind="subagent", context=context, budget=budget, history=history,
                )
            except Exception:
                web_songs, web_artists = [], []
            if web_songs:
                catalog_songs = [item for item in report.song_recommendations if item.source_status != "web_discovered"]
                report.song_recommendations = (web_songs + catalog_songs)[:7]
            elif board.get("network_sources"):
                report.warnings.append("WEB_DISCOVERY_EMPTY_FALLBACK_TO_CATALOG")
            if web_artists:
                catalog_artists = [item for item in report.artist_recommendations if item.source_status != "web_discovered"]
                report.artist_recommendations = (web_artists + catalog_artists)[:3]
            report.exploration_tags = list(board.get("knowledge_tags", []))[:5]
            warnings = await invoke_with_budget(
                lambda: recommendation_validator.validate(report, board["facts_snapshot"], board.get("network_sources", [])),
                name="recommendation_validation", kind="subagent", context=context, budget=budget, history=history,
            )
            report.warnings.extend(warnings)
            board.pop("critique", None)
            return {"board": board, "result": report}

        if action == "critique_report":
            if "result" not in state:
                board["validation_errors"] = ["REPORT_DRAFT_MISSING"]
                return {"board": board}
            review = await invoke_with_budget(
                lambda: critic_agent.review(state["result"], board["facts_snapshot"], board.get("network_sources", [])),
                name="critic_review", kind="subagent", context=context, budget=budget, history=history,
            )
            board["critique"] = review.model_dump()
            board["validation_errors"] = review.issues
            return {"board": board}

        if action == "submit_report":
            if "result" not in state or not board.get("critique", {}).get("passed"):
                return {"board": board, "error_code": "REPORT_CRITIQUE_FAILED"}
            return {"board": board, "result": state["result"]}

        if action == "finish_degraded":
            signals = board.get("preference_signals") or _extract_signals(board["facts_snapshot"])
            report = state.get("result") or _fallback_report(board["facts_snapshot"], signals, request)
            review = await critic_agent.review(report, board["facts_snapshot"], board.get("network_sources", []))
            board["critique"] = review.model_dump()
            if not review.passed:
                return {"board": board, "error_code": "REPORT_CRITIQUE_FAILED"}
            return {"board": board, "result": report}

        raise RuntimeError("REPORT_ACTION_NOT_ALLOWED")

    def after_execute(state: ReportState):
        action = state["decision"].action
        return "end" if action in {"submit_report", "finish_degraded"} else "supervisor"

    graph = StateGraph(ReportState)
    graph.add_node("initialize", initialize)
    graph.add_node("supervisor", supervisor)
    graph.add_node("execute_action", execute)
    graph.set_entry_point("initialize")
    graph.add_edge("initialize", "supervisor")
    graph.add_edge("supervisor", "execute_action")
    graph.add_conditional_edges("execute_action", after_execute, {"supervisor": "supervisor", "end": END})
    return graph.compile()
