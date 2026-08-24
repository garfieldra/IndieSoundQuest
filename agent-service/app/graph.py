from __future__ import annotations

from typing import Literal, TypedDict
from uuid import UUID

from langgraph.graph import END, StateGraph

from .llm import CandidateDecision, DeepSeekCandidateSelector, _complete_external_query_plan, _deterministic_discovery_hints
from .runtime import RunContext, RuntimeBudget, ToolCallRecord, invoke_with_budget
from .schemas import CandidateItem, CandidatePoolRequest, CandidatePoolResult, IntentPolicy
from .tools import KnowledgeSearchTool, MusicCatalogTool, WebSearchTool


class State(TypedDict, total=False):
    request: CandidatePoolRequest
    recordings: list[dict]
    knowledge: list[dict]
    web_sources: list[dict]
    discovery_hints: list[dict]
    external_queries: list[dict]
    ranked: list[dict]
    summary: str
    decision: CandidateDecision
    action_history: list[dict]
    tool_history: list[ToolCallRecord]
    budget: RuntimeBudget
    run_context: RunContext
    iteration: int
    musicbrainz_called: bool
    observations: list[dict]
    intent_policy: IntentPolicy
    termination_reason: str
    stagnation_count: int
    quality_gate: dict
    entity_resolution_complete: bool
    clarifications: list[dict]
    discovery_rounds: int
    musicbrainz_rounds: int
    preference_hypotheses: list[dict]
    artist_seeds: list[dict]
    artist_catalog_expanded: bool
    result: CandidatePoolResult


def build_candidate_pool_graph(
    catalog: MusicCatalogTool,
    web: WebSearchTool,
    knowledge: KnowledgeSearchTool,
    selector: DeepSeekCandidateSelector,
):
    async def initialize(state: State):
        # Online-first pools may need several evidence pages and polite
        # MusicBrainz batches before 16 active + 16 reserve records are
        # available.  This is a runtime budget (not a prescribed workflow):
        # the ReAct supervisor still chooses every next action.
        budget = RuntimeBudget(max_tool_calls=96, max_subagent_calls=64, deadline_seconds=900)
        return {
            "recordings": [], "knowledge": [], "web_sources": [], "discovery_hints": [], "external_queries": [],
            "ranked": [], "summary": "", "action_history": [], "tool_history": [],
            "budget": budget, "run_context": budget.context(state["request"].request_id, state["request"].request_id, "candidate_generation"),
            "iteration": 0, "musicbrainz_called": False, "observations": [],
            "stagnation_count": 0,
            "entity_resolution_complete": False, "clarifications": [],
            "discovery_rounds": 0, "musicbrainz_rounds": 0, "preference_hypotheses": [], "artist_seeds": [], "artist_catalog_expanded": False,
        }

    async def supervisor(state: State):
        request = state["request"]
        target = request.size * 2
        last_observation = state["observations"][-1] if state["observations"] else {}
        no_gain = last_observation.get("status") == "failed" or (
            last_observation.get("action") in {"search_catalog", "search_web", "resolve_musicbrainz"}
            and last_observation.get("outputCount", last_observation.get("hintCount", 0)) == 0
        )
        stagnation_count = state.get("stagnation_count", 0) + 1 if no_gain else 0
        summary = {
            "catalogSearched": any(item["action"] == "search_catalog" for item in state["action_history"]),
            "webSearched": any(item["action"] == "search_web" for item in state["action_history"]),
            "knowledgeSearched": any(item["action"] == "search_knowledge" for item in state["action_history"]),
            "musicBrainzCalled": state["musicbrainz_called"],
            "unresolvedHintCount": len(state["discovery_hints"]),
            "ranked": bool(state["ranked"]),
            "iteration": state["iteration"],
            "remainingIterations": max(0, 48 - state["iteration"]),
            "recentActions": state["action_history"][-4:],
            "recentObservations": state["observations"][-4:],
            "intentPolicyReady": "intent_policy" in state,
            "intentPolicy": state.get("intent_policy").model_dump(mode="json", by_alias=True) if state.get("intent_policy") else None,
            "stagnationCount": stagnation_count,
            "verifiedArtistCount": len({
                str(item.get("artistId") or _normalize_identity(item.get("artistName")))
                for item in state["recordings"] if item.get("artistId") or item.get("artistName")
            }),
            "entityResolutionComplete": state.get("entity_resolution_complete", False),
            "clarificationRequired": bool(state.get("clarifications")),
            "discoveryRounds": state.get("discovery_rounds", 0),
            "musicbrainzRounds": state.get("musicbrainz_rounds", 0),
            "preferenceHypotheses": state.get("preference_hypotheses", []),
            "resolvedArtistSeeds": state.get("artist_seeds", []),
            "artistCatalogExpanded": state.get("artist_catalog_expanded", False),
        }
        try:
            decision = await selector.decide(request.preference_text, target, len(state["recordings"]), summary)
        except Exception:
            decision = _fallback_decision(request, state)
        decision = _normalize_decision_reason(decision)
        if "intent_policy" not in state:
            decision = CandidateDecision(
                action="understand_preference", reason_code="intent_policy_missing",
                decision_summary="先形成可审计的用户意图策略",
            )
        elif not state.get("entity_resolution_complete"):
            decision = CandidateDecision(action="resolve_named_entities", reason_code="locked_artist_identity_unresolved", decision_summary="先核验用户提到的艺人实体")
        elif state.get("clarifications"):
            decision = CandidateDecision(action="request_clarification", reason_code="locked_artist_identity_unresolved", decision_summary="艺人名称存在必要歧义，等待用户确认")
        elif decision.action == "resolve_named_entities" and state.get("entity_resolution_complete"):
            decision = CandidateDecision(action="search_catalog" if not summary["catalogSearched"] else "search_web", reason_code="verified_candidates_below_target", decision_summary="艺人实体已解析，继续收集可核验歌曲")
        elif state.get("artist_seeds") and not state.get("artist_catalog_expanded") and len(state["recordings"]) < target:
            decision = CandidateDecision(action="expand_artist_catalog", reason_code="verified_candidates_below_target", decision_summary="已解析艺人作品不足，批量从 MusicBrainz 补充规范曲目")
        elif state["discovery_hints"] and decision.action in {"search_web", "search_catalog", "rerank_candidates"}:
            # This is an evidence-consumption guard: a ReAct search decision cannot
            # discard already observed, unverified song evidence by searching again.
            decision = CandidateDecision(action="resolve_musicbrainz", reason_code="external_hints_require_resolution", decision_summary="已有可追溯网页线索，先进行 MusicBrainz 核验")
        # Theme-card retrieval is an explanatory bonus, never an entity-discovery
        # dependency.  It cannot help an empty pool become playable, so defer it
        # until online discovery has already produced the required facts.
        elif decision.action == "search_knowledge" and len(state["recordings"]) < target:
            decision = CandidateDecision(action="search_web", reason_code="verified_candidates_below_target", decision_summary="候选事实不足，优先在线发现与 MusicBrainz 核验")
        # Business guard: a short reserve queue may only be accepted after the Agent has tried expansion.
        if decision.action in {"submit_candidates", "finish_insufficient"} and len(state["recordings"]) < target:
            if state.get("discovery_rounds", 0) < 5:
                decision = CandidateDecision(action="search_web", reason_code="verified_candidates_below_target", decision_summary="补位不足，先搜索相近方向")
            elif state["discovery_hints"]:
                decision = CandidateDecision(action="resolve_musicbrainz", reason_code="external_hints_require_resolution", decision_summary="将网络发现解析为规范歌曲")
        if (
            state.get("entity_resolution_complete")
            and len(state["recordings"]) < target
            and state.get("discovery_rounds", 0) == 0
            and decision.action == "search_catalog"
            and not (state.get("intent_policy") and state["intent_policy"].intent_mode == "ARTIST_LOCKED" and request.seed_artist_ids)
        ):
            decision = CandidateDecision(action="search_web", reason_code="verified_candidates_below_target", decision_summary="在线发现是默认主链路，先寻找可核验候选")
        policy = state.get("intent_policy")
        if (
            policy is not None
            and policy.intent_mode == "ARTIST_LOCKED"
            and len(state["recordings"]) >= target
            and decision.action == "search_web"
        ):
            decision = CandidateDecision(
                action="rerank_candidates" if not state["ranked"] else "submit_candidates",
                reason_code="candidate_pool_requires_rerank" if not state["ranked"] else "candidate_pool_ready_for_validation",
                decision_summary="锁定范围内的规范候选已充足，无需额外网络扩展",
            )
        narrow_open_catalog = (
            policy is not None
            and policy.intent_mode == "OPEN_DISCOVERY"
            and len(state["recordings"]) >= request.size
            and summary["verifiedArtistCount"] <= 1
        )
        # This is an evidence-seeking guard, not an artist quota: an open request backed
        # only by one artist must at least attempt expansion before final ranking.
        if narrow_open_catalog and not summary["webSearched"] and decision.action in {
            "rerank_candidates", "submit_candidates", "finish_insufficient"
        }:
            decision = CandidateDecision(
                action="search_web", reason_code="cross_artist_expansion_allowed",
                decision_summary="开放探索的本地结果过窄，尝试寻找可核验的相近方向",
            )
        # Quantity is a deterministic completion contract. Once active +
        # reserve are both satisfied, more discovery cannot improve playability
        # enough to justify another potentially minute-long verification batch.
        # The Agent still owns ranking and submission; this guard only prevents
        # tool overrun after the requested evidence target is already met.
        if len(state["recordings"]) >= target and not (narrow_open_catalog and not summary["webSearched"]) and decision.action in {
            "search_catalog", "expand_artist_catalog", "search_web", "resolve_musicbrainz",
        }:
            decision = CandidateDecision(
                action="rerank_candidates" if not state["ranked"] else "submit_candidates",
                reason_code="candidate_pool_requires_rerank" if not state["ranked"] else "candidate_pool_ready_for_validation",
                decision_summary="规范候选与补位池已充足，停止额外发现并进入质量校验",
            )
        # Keep this late so later quantity guards cannot overwrite the obligation
        # to consume already observed evidence.
        if len(state["recordings"]) < target and state["discovery_hints"] and decision.action not in {"resolve_musicbrainz", "request_clarification"}:
            decision = CandidateDecision(action="resolve_musicbrainz", reason_code="external_hints_require_resolution", decision_summary="先核验黑板中尚未消费的网页歌曲线索")
        # Runtime guardrails override invalid or endlessly repeated choices without prescribing normal tool order.
        runtime_termination = state.get("termination_reason")
        if state["iteration"] >= 47 or stagnation_count >= 3:
            runtime_termination = "STAGNATION_LIMIT_REACHED" if stagnation_count >= 3 else "BUDGET_EXHAUSTED"
            decision = CandidateDecision(
                action="submit_candidates" if len(state["recordings"]) >= request.size else "finish_insufficient",
                reason_code="budget_or_stagnation_limit_reached",
                decision_summary="已达到运行预算，进入最终校验",
            )
        recent = [item["action"] for item in state["action_history"][-2:]]
        if len(recent) == 2 and all(action == decision.action for action in recent) and not (decision.action == "resolve_musicbrainz" and state["discovery_hints"]):
            decision = CandidateDecision(action="rerank_candidates", reason_code="candidate_pool_requires_rerank", decision_summary="阻止无收益重复调用")
        if runtime_termination:
            decision = CandidateDecision(
                action="submit_candidates" if len(state["recordings"]) >= request.size else "finish_insufficient",
                reason_code="budget_or_stagnation_limit_reached",
                decision_summary="已达到运行预算，进入最终校验",
            )
        return {
            "decision": decision,
            "iteration": state["iteration"] + 1,
            "stagnation_count": stagnation_count,
            **({"termination_reason": runtime_termination} if runtime_termination else {}),
            "action_history": state["action_history"] + [{
                "step": state["iteration"] + 1, "action": decision.action,
                "reasonCode": decision.reason_code, "summary": decision.decision_summary,
            }],
        }

    async def execute(state: State):
        request = state["request"]
        action = state["decision"].action
        budget, history = state["budget"], state["tool_history"]
        context = state["run_context"]

        if action == "understand_preference":
            try:
                policy = await invoke_with_budget(
                    lambda: selector.infer_intent(request.preference_text, request.seed_artist_ids),
                    name="understand_preference", kind="subagent", context=context, budget=budget, history=history,
                )
            except Exception:
                from .llm import classify_intent_locally
                policy = classify_intent_locally(request.preference_text, request.seed_artist_ids)
            try:
                hypotheses = await invoke_with_budget(lambda: selector.derive_hypotheses(request.preference_text, policy), name="derive_preference_hypotheses", kind="subagent", context=context, budget=budget, history=history)
            except Exception:
                from .llm import _deterministic_hypotheses
                hypotheses = _deterministic_hypotheses(request.preference_text, policy)
            return {
                "intent_policy": policy, "preference_hypotheses": hypotheses, "tool_history": history,
                "observations": state["observations"] + [{
                    "action": action, "status": "success", "intentMode": policy.intent_mode,
                    "allowedArtistCount": len(policy.allowed_artist_ids) + len(policy.allowed_artist_names),
                    "hypothesisCount": len(hypotheses),
                }],
            }

        if action == "resolve_named_entities":
            if request.seed_artist_ids:
                return {"entity_resolution_complete": True, "tool_history": history,
                        "observations": state["observations"] + [{"action": action, "status": "success", "confirmedByUser": True}]}
            try:
                mentions = await invoke_with_budget(lambda: selector.extract_named_artists(request.preference_text), name="extract_named_artists", kind="subagent", context=context, budget=budget, history=history)
                confirmed_mentions = {_normalize_identity(item.mention) for item in request.confirmed_artists}
                unresolved_mentions = [mention for mention in mentions if _normalize_identity(mention) not in confirmed_mentions]
                resolutions = await invoke_with_budget(lambda: catalog.resolve_artist_candidates(unresolved_mentions), name="resolve_artist_candidates", kind="tool", context=context, budget=budget, history=history)
            except Exception as exc:
                return {"entity_resolution_complete": True, "tool_history": history,
                        "observations": state["observations"] + [{"action": action, "status": "failed", "error": type(exc).__name__}]}
            clarifications = [item for item in resolutions if _artist_clarification_required(item)]
            artist_seeds = [{"mbid": item["candidates"][0]["mbid"], "name": item["candidates"][0]["name"]} for item in resolutions if item.get("candidates") and item not in clarifications]
            artist_seeds.extend({"mbid": str(item.mbid), "name": item.name} for item in request.confirmed_artists)
            artist_seeds = list({item["mbid"]: item for item in artist_seeds}.values())
            policy = state.get("intent_policy")
            if policy and policy.intent_mode == "ARTIST_LOCKED" and request.confirmed_artists:
                policy = policy.model_copy(update={"allowed_artist_names": [item.name for item in request.confirmed_artists]})
            if policy and policy.intent_mode == "ARTIST_LOCKED" and artist_seeds:
                policy = policy.model_copy(update={"allowed_artist_names": [item["name"] for item in artist_seeds]})
            return {"entity_resolution_complete": True, "clarifications": clarifications, "artist_seeds": artist_seeds, "intent_policy": policy, "tool_history": history,
                    "observations": state["observations"] + [{"action": action, "status": "success", "mentionCount": len(mentions), "clarificationCount": len(clarifications)}]}

        if action == "request_clarification":
            return {"result": _clarification_result(state), "tool_history": history,
                    "observations": state["observations"] + [{"action": action, "status": "success", "outputCount": len(state.get("clarifications", []))}]}

        if action == "search_catalog":
            policy = state.get("intent_policy")
            artist_ids = policy.allowed_artist_ids if policy and policy.intent_mode == "ARTIST_LOCKED" else []
            try:
                items = await invoke_with_budget(
                    lambda: catalog.search_recordings(request.preference_text, artist_ids),
                    name="search_catalog", kind="tool", context=context, budget=budget, history=history,
                )
            except Exception as exc:
                return {"tool_history": history, "observations": state["observations"] + [{"action": action, "status": "failed", "error": type(exc).__name__}]}
            excluded = {str(item) for item in request.exclude_recording_ids}
            accepted = [
                item | {"trustState": "CATALOG_IMPORTED"}
                for item in items if item["id"] not in excluded and _candidate_allowed(item, policy) and _catalog_relevant(item, request, policy)
            ]
            merged = _merge_recordings(state["recordings"], accepted)
            return {"recordings": merged, "tool_history": history, "observations": state["observations"] + [{"action": action, "status": "success", "inputCount": len(items), "outputCount": len(accepted), "verifiedCount": len(merged)}]}

        if action == "expand_artist_catalog":
            try:
                # One resolved artist may be the user's entire scope. Request
                # enough canonical recordings for active + reserve in one page.
                per_artist_limit = min(100, max(40, request.size * 2))
                resolved = await invoke_with_budget(
                    lambda: catalog.discover_artist_recordings(state.get("artist_seeds", []), per_artist_limit),
                    name="expand_artist_catalog", kind="tool", context=context, budget=budget, history=history,
                )
            except Exception as exc:
                return {"artist_catalog_expanded": True, "tool_history": history, "observations": state["observations"] + [{"action": action, "status": "failed", "error": type(exc).__name__}]}
            imported = [{
                "id": str(item["recordingId"]), "title": item["title"], "artistName": item["artistName"],
                "artistId": str(item.get("artistId") or ""), "albumTitle": item.get("albumTitle") or "", "coverStatus": item.get("coverStatus") or "PENDING",
                "sourceUrl": item.get("sourceUrl"), "musicbrainzMbid": item.get("recordingMbid"), "catalogSource": item.get("catalogSource") or "EXTERNAL_VERIFIED", "trustState": "CATALOG_IMPORTED",
            } for item in resolved if item.get("status") == "RESOLVED" and item.get("recordingId") and item.get("recordingMbid") and _candidate_allowed(item, state.get("intent_policy"))]
            merged = _merge_recordings(state["recordings"], imported)
            return {"recordings": merged, "artist_catalog_expanded": True, "tool_history": history, "observations": state["observations"] + [{"action": action, "status": "success", "inputCount": len(resolved), "outputCount": len(imported), "verifiedCount": len(merged)}]}

        if action == "search_knowledge":
            try:
                claims = await invoke_with_budget(
                    lambda: knowledge.search_verified(request.preference_text, [item["id"] for item in state["recordings"]]),
                    name="search_knowledge", kind="tool", context=context, budget=budget, history=history,
                )
            except Exception as exc:
                return {"tool_history": history, "observations": state["observations"] + [{"action": action, "status": "failed", "error": type(exc).__name__}]}
            return {
                "knowledge": claims, "tool_history": history,
                "observations": state["observations"] + [{"action": action, "status": "success", "outputCount": len(claims)}],
            }

        if action == "search_web":
            try:
                planned_queries = await invoke_with_budget(
                    lambda: selector.plan_external_queries(
                        request.preference_text, state.get("intent_policy"), state["decision"].query,
                        state.get("preference_hypotheses", []), state.get("external_queries", []),
                    ),
                    name="plan_external_queries", kind="subagent", context=context, budget=budget, history=history,
                )
            except Exception:
                planned_queries = _complete_external_query_plan(request.preference_text, [], state.get("preference_hypotheses", []), state.get("external_queries", []))
            queries = [{"query": item.query, "purpose": item.purpose} for item in planned_queries]
            try:
                sources = await invoke_with_budget(
                    lambda: web.search_many(queries), name="search_web", kind="tool", context=context, budget=budget, history=history,
                )
            except Exception as exc:
                return {"web_sources": state["web_sources"], "discovery_hints": state["discovery_hints"], "tool_history": history, "observations": state["observations"] + [{"action": action, "status": "failed", "error": type(exc).__name__}]}
            try:
                hints = await invoke_with_budget(
                    lambda: selector.extract_discovery_hints(request.preference_text, sources),
                    name="extract_web_song_hints", kind="subagent", context=context, budget=budget, history=history, timeout_seconds=25,
                )
            except Exception:
                hints = _deterministic_discovery_hints(request.preference_text, sources)
            # Preserve every source-grounded pair that a deterministic parser
            # can see.  The LLM is useful for messy prose, but it must not be a
            # single point of failure for explicit playlist rows such as
            # "歌手 - 歌名" or "歌名 · 歌手".
            hints = _merge_discovery_hint_models(
                hints, _deterministic_discovery_hints(request.preference_text, sources),
            )
            enriched_count = 0
            if not hints and sources:
                try:
                    enriched_sources = await invoke_with_budget(
                        lambda: web.enrich_public_sources(sources, limit=5),
                        name="fetch_public_evidence_excerpt", kind="tool", context=context, budget=budget, history=history,
                    )
                    enriched_count = len(enriched_sources)
                    if enriched_sources:
                        sources = _merge_sources(sources, enriched_sources)
                        try:
                            hints = await invoke_with_budget(
                                lambda: selector.extract_discovery_hints(request.preference_text, sources),
                                name="extract_enriched_song_hints", kind="subagent", context=context, budget=budget, history=history, timeout_seconds=25,
                            )
                        except Exception:
                            hints = _deterministic_discovery_hints(request.preference_text, sources)
                        hints = _merge_discovery_hint_models(
                            hints, _deterministic_discovery_hints(request.preference_text, sources),
                        )
                except Exception:
                    pass
            accepted_hints = [
                {
                    "title": hint.title, "artistName": hint.artist_name, "sourceUrl": hint.source_url,
                    "sourceTitle": _source_for_url(sources, hint.source_url).get("sourceTitle", hint.source_title),
                    "evidenceSnippet": hint.evidence_snippet,
                    "queryPurpose": _source_for_url(sources, hint.source_url).get("queryPurpose", hint.query_purpose),
                    "trustState": "DISCOVERY_HINT",
                }
                for hint in hints
                if _hint_allowed(hint.artist_name, state.get("intent_policy"))
                and _hint_evidence_is_observed(hint, sources)
            ]
            return {
                "web_sources": _merge_sources(state["web_sources"], sources),
                "discovery_hints": _merge_hints(state["discovery_hints"], accepted_hints),
                "external_queries": state["external_queries"] + queries,
                "tool_history": history,
                "observations": state["observations"] + [{
                    "action": action, "status": "success", "sourceCount": len(sources),
                    "queryCount": len(queries), "hintCount": len(accepted_hints),
                    "hypotheses": [item.get("value") for item in state.get("preference_hypotheses", [])],
                    "rejectedHintCount": len(hints) - len(accepted_hints), "enrichedSourceCount": enriched_count,
                }],
                "discovery_rounds": state.get("discovery_rounds", 0) + 1,
            }

        if action == "resolve_musicbrainz":
            try:
                resolved = await invoke_with_budget(
                    lambda: catalog.resolve_and_import(state["discovery_hints"][:16]),
                    name="resolve_musicbrainz", kind="tool", context=context, budget=budget, history=history,
                )
            except Exception as exc:
                return {"musicbrainz_called": True, "discovery_hints": [], "tool_history": history, "observations": state["observations"] + [{"action": action, "status": "failed", "error": type(exc).__name__}]}
            imported = [
                {
                    "id": str(item["recordingId"]), "title": item["title"], "artistName": item["artistName"],
                    "artistId": str(item.get("artistId") or ""), "albumTitle": item.get("albumTitle") or "", "coverStatus": item.get("coverStatus") or "PENDING",
                    "sourceUrl": item.get("sourceUrl"), "musicbrainzMbid": item.get("recordingMbid"),
                    "catalogSource": item.get("catalogSource") or "EXTERNAL_VERIFIED",
                    "trustState": "CATALOG_IMPORTED",
                }
                for item in resolved
                if item.get("status") == "RESOLVED" and item.get("recordingId") and item.get("recordingMbid")
                and _candidate_allowed(item, state.get("intent_policy"))
            ]
            return {
                "recordings": _merge_recordings(state["recordings"], imported),
                "discovery_hints": state["discovery_hints"][16:], "musicbrainz_called": True, "tool_history": history,
                "musicbrainz_rounds": state.get("musicbrainz_rounds", 0) + 1,
                "observations": state["observations"] + [{
                    "action": action, "status": "success", "inputCount": len(resolved),
                    "outputCount": len(imported), "rejectedCount": len(resolved) - len(imported),
                    "resolutionStates": _resolution_state_counts(resolved),
                }],
            }

        if action == "rerank_candidates":
            available = _deduplicate_versions(state["recordings"])
            count = min(request.size * 2, len(available))
            try:
                selection = await invoke_with_budget(
                    lambda: selector.select(request.preference_text, count, available, state.get("intent_policy")),
                    name="rerank_candidates", kind="subagent", context=context, budget=budget, history=history,
                ) if count else None
            except Exception:
                selection = None
            by_id = {UUID(item["id"]): item for item in available}
            if selection and len(selection.selected) == count and all(item.recording_id in by_id for item in selection.selected):
                ranked = [by_id[item.recording_id] | {"reason": item.reason} for item in selection.selected]
                summary_text = selection.candidate_summary
            else:
                ranked = available[:count]
                summary_text = "这组候选依据你的输入与可验证音乐目录生成；确认后才会创建赛事。"
            return {"ranked": ranked, "summary": summary_text, "tool_history": history, "observations": state["observations"] + [{"action": action, "status": "success", "count": len(ranked)}]}

        if action == "submit_candidates":
            quality_gate = _validate_candidate_quality(state)
            if not quality_gate["passed"] and len(state["recordings"]) >= request.size and state["iteration"] < 9:
                return {
                    "quality_gate": quality_gate,
                    "observations": state["observations"] + [{"action": "validate_candidate_quality", "status": "success", "outputCount": 0, "issues": quality_gate["issues"]}],
                }
            return {"quality_gate": quality_gate, "result": _build_result(state, insufficient=False)}

        if action == "finish_insufficient":
            return {"result": _build_result(state, insufficient=True)}

        raise RuntimeError("CANDIDATE_ACTION_NOT_ALLOWED")

    def after_execute(state: State) -> Literal["supervisor", "end"]:
        return "end" if "result" in state else "supervisor"

    graph = StateGraph(State)
    graph.add_node("initialize", initialize)
    graph.add_node("supervisor", supervisor)
    graph.add_node("execute_action", execute)
    graph.set_entry_point("initialize")
    graph.add_edge("initialize", "supervisor")
    graph.add_edge("supervisor", "execute_action")
    graph.add_conditional_edges("execute_action", after_execute, {"supervisor": "supervisor", "end": END})
    # The Supervisor runtime permits up to 48 decisions.  LangGraph defaults
    # to 25 graph steps when callers do not supply a config, which can cut off
    # a valid online-discovery run before its own budget is reached.
    return graph.compile().with_config({"recursion_limit": 128})


def _merge_recordings(existing: list[dict], new_items: list[dict]) -> list[dict]:
    merged = {item["id"]: item for item in existing}
    merged.update({item["id"]: item for item in new_items})
    return list(merged.values())


def _normalize_decision_reason(decision: CandidateDecision) -> CandidateDecision:
    compatible = {
        "understand_preference": {"intent_policy_missing"},
        "resolve_named_entities": {"locked_artist_identity_unresolved"},
        "request_clarification": {"locked_artist_identity_unresolved"},
        "search_catalog": {"locked_artist_identity_unresolved", "verified_candidates_below_active_size", "verified_candidates_below_target"},
        "expand_artist_catalog": {"verified_candidates_below_active_size", "verified_candidates_below_target"},
        "search_knowledge": {"local_scope_exhausted", "cross_artist_expansion_allowed", "verified_candidates_below_target"},
        "search_web": {"local_scope_exhausted", "cross_artist_expansion_allowed", "verified_candidates_below_active_size", "verified_candidates_below_target"},
        "resolve_musicbrainz": {"external_hints_require_resolution"},
        "rerank_candidates": {"candidate_pool_requires_rerank"},
        "submit_candidates": {"candidate_pool_ready_for_validation", "budget_or_stagnation_limit_reached"},
        "finish_insufficient": {"verified_candidates_below_active_size", "budget_or_stagnation_limit_reached"},
    }
    if decision.reason_code in compatible[decision.action]:
        return decision
    defaults = {
        "understand_preference": "intent_policy_missing",
        "resolve_named_entities": "locked_artist_identity_unresolved",
        "request_clarification": "locked_artist_identity_unresolved",
        "search_catalog": "verified_candidates_below_active_size",
        "expand_artist_catalog": "verified_candidates_below_target",
        "search_knowledge": "local_scope_exhausted",
        "search_web": "cross_artist_expansion_allowed",
        "resolve_musicbrainz": "external_hints_require_resolution",
        "rerank_candidates": "candidate_pool_requires_rerank",
        "submit_candidates": "candidate_pool_ready_for_validation",
        "finish_insufficient": "verified_candidates_below_active_size",
    }
    return decision.model_copy(update={"reason_code": defaults[decision.action]})


def _fallback_decision(request: CandidatePoolRequest, state: State) -> CandidateDecision:
    if "intent_policy" not in state:
        return CandidateDecision(action="understand_preference", reason_code="intent_policy_missing", decision_summary="先形成可审计的用户意图策略")
    if not state.get("entity_resolution_complete"):
        return CandidateDecision(action="resolve_named_entities", reason_code="locked_artist_identity_unresolved", decision_summary="先核验用户提到的艺人实体")
    if state.get("clarifications"):
        return CandidateDecision(action="request_clarification", reason_code="locked_artist_identity_unresolved", decision_summary="等待用户确认歧义艺人")
    actions = {item["action"] for item in state["action_history"]}
    if "search_catalog" not in actions:
        return CandidateDecision(action="search_catalog", reason_code="verified_candidates_below_active_size", decision_summary="模型暂不可用，先读取规范目录")
    if state.get("artist_seeds") and not state.get("artist_catalog_expanded"):
        return CandidateDecision(action="expand_artist_catalog", reason_code="verified_candidates_below_target", decision_summary="批量核验明确艺人的作品")
    if state["discovery_hints"] and not state["musicbrainz_called"]:
        return CandidateDecision(action="resolve_musicbrainz", reason_code="external_hints_require_resolution", decision_summary="处理已发现的外部歌曲")
    if not state["ranked"]:
        return CandidateDecision(action="rerank_candidates", reason_code="candidate_pool_requires_rerank", decision_summary="对已有规范候选去重排序")
    return CandidateDecision(
        action="submit_candidates" if len(state["ranked"]) >= request.size else "finish_insufficient",
        reason_code="candidate_pool_ready_for_validation",
        decision_summary="进入最终数量与实体校验",
    )


def _merge_sources(existing: list[dict], new_items: list[dict]) -> list[dict]:
    merged = {item.get("sourceUrl"): item for item in existing if item.get("sourceUrl")}
    merged.update({item.get("sourceUrl"): item for item in new_items if item.get("sourceUrl")})
    return list(merged.values())


def _merge_hints(existing: list[dict], new_items: list[dict]) -> list[dict]:
    merged = {(item.get("artistName"), item.get("title")): item for item in existing}
    merged.update({(item.get("artistName"), item.get("title")): item for item in new_items})
    return list(merged.values())


def _merge_discovery_hint_models(primary: list[object], supplemental: list[object]) -> list[object]:
    """Keep model and parser discoveries without duplicating a paired fact."""
    merged: list[object] = []
    seen: set[tuple[str, str]] = set()
    for item in [*primary, *supplemental]:
        artist = _normalize_identity(str(getattr(item, "artist_name", "")))
        title = _normalize_identity(str(getattr(item, "title", "")))
        if not artist or not title or (artist, title) in seen:
            continue
        seen.add((artist, title))
        merged.append(item)
    return merged[:48]


def _artist_clarification_required(resolution: dict) -> bool:
    candidates = resolution.get("candidates") or []
    if not candidates:
        # A transport failure or an empty MusicBrainz response is not an identity
        # ambiguity.  Blocking here would ask the user to resolve an option we
        # cannot show; let the ReAct agent continue with online discovery instead.
        return False
    first = candidates[0]
    mention = _normalize_identity(str(resolution.get("mention", "")))
    exact = mention and mention == _normalize_identity(str(first.get("name", "")))
    score = int(first.get("score") or 0)
    second_score = int(candidates[1].get("score") or 0) if len(candidates) > 1 else 0
    if len(candidates) == 1 and score >= 95:
        return False
    return not (exact and score >= 95 and score - second_score >= 8)


def _clarification_result(state: State) -> CandidatePoolResult:
    request = state["request"]
    return CandidatePoolResult(
        request_id=request.request_id, status="needs_clarification", size=request.size,
        candidate_summary="需要先确认你提到的艺人，才能避免把歌曲加入错误的候选池。",
        warnings=[{"code": "ARTIST_IDENTITY_CLARIFICATION_REQUIRED", "message": "请确认存在歧义的艺人后继续生成。"}],
        intent_policy=state.get("intent_policy"), termination_reason="ENTITY_CLARIFICATION_REQUIRED",
        trace_summary=_trace_summary(state), clarifications=state.get("clarifications", []),
    )


def _source_for_url(sources: list[dict], url: str) -> dict:
    return next((item for item in sources if item.get("sourceUrl") == url), {})


def _hint_evidence_is_observed(hint: object, sources: list[dict]) -> bool:
    source = _source_for_url(sources, getattr(hint, "source_url", ""))
    if not source:
        return False
    evidence = _normalize_evidence(getattr(hint, "evidence_snippet", ""))
    observed = _normalize_evidence(" ".join([
        str(source.get("sourceTitle", "")), str(source.get("summary", "")), str(source.get("pageExcerpt", "")),
    ]))
    if len(evidence) >= 8 and evidence in observed:
        return True
    # Fallback parsers can quote title and track-list fields separately.  Accept
    # only when both normalized entities are visibly present in the same source.
    artist = _normalize_identity(getattr(hint, "artist_name", ""))
    title = _normalize_identity(getattr(hint, "title", ""))
    observed_identity = _normalize_identity(observed)
    return bool(artist and title and artist in observed_identity and title in observed_identity)


def _normalize_evidence(value: str) -> str:
    return "".join(value.split()).lower()


def _deduplicate_versions(items: list[dict]) -> list[dict]:
    result, seen = [], set()
    for item in items:
        key = ("".join(item.get("artistName", "").lower().split()), "".join(item.get("title", "").lower().split()))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _normalize_identity(value: str | None) -> str:
    return "".join(character.lower() for character in (value or "") if character.isalnum())


def _candidate_allowed(item: dict, policy: IntentPolicy | None) -> bool:
    if not policy or policy.intent_mode != "ARTIST_LOCKED":
        return True
    allowed_ids = {str(value) for value in policy.allowed_artist_ids}
    # An ID resolved by Java is stronger evidence than a model-produced display name.
    # Never let the latter widen an explicit locked scope.
    if allowed_ids:
        return bool(item.get("artistId") and str(item["artistId"]) in allowed_ids)
    allowed_names = {_normalize_identity(value) for value in policy.allowed_artist_names if value}
    return bool(allowed_names and _normalize_identity(item.get("artistName")) in allowed_names)


def _catalog_relevant(item: dict, request: CandidatePoolRequest, policy: IntentPolicy | None) -> bool:
    # A seeded request explicitly permits moving beyond the named artist.  The
    # catalog acts as a verified cache here, so filtering every non-seed work
    # by literal prompt tokens defeats the permitted discovery scope.
    if policy and policy.intent_mode == "ARTIST_SEEDED":
        return True
    if policy and str(item.get("artistId")) in {str(value) for value in policy.seed_artist_ids}:
        return True
    text = _normalize_identity(request.preference_text)
    return _normalize_identity(str(item.get("artistName", ""))) in text or _normalize_identity(str(item.get("title", ""))) in text


def _hint_allowed(artist_name: str, policy: IntentPolicy | None) -> bool:
    if not policy or policy.intent_mode != "ARTIST_LOCKED":
        return True
    # When Java IDs are the only lock evidence, MusicBrainz import will be checked again after resolution.
    if not policy.allowed_artist_names:
        return True
    allowed = {_normalize_identity(value) for value in policy.allowed_artist_names}
    return _normalize_identity(artist_name) in allowed


def _resolution_state_counts(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        state = item.get("trustState") or item.get("status") or "UNKNOWN"
        counts[state] = counts.get(state, 0) + 1
    return counts


def _fallback_reason(item: dict, policy: IntentPolicy | None) -> str:
    facets = policy.preference_facets if policy else None
    terms = []
    if facets:
        terms = (facets.mood + facets.scene + facets.genre + facets.language + facets.era)[:2]
    direction = "、".join(terms) if terms else "你描述的音乐方向"
    return f"《{item.get('title', '这首歌')}》来自已验证目录，可放进比赛中比较它与{direction}的贴合度。"


def _trace_summary(state: State) -> dict:
    return {
        "iterationCount": state.get("iteration", 0),
        "actions": [entry.get("action") for entry in state.get("action_history", [])],
        "reasonCodes": [entry.get("reasonCode") for entry in state.get("action_history", [])],
        "toolCalls": len(state.get("tool_history", [])),
        "verifiedCandidateCount": len(state.get("recordings", [])),
        "webSourceCount": len(state.get("web_sources", [])),
        "musicBrainzCalled": state.get("musicbrainz_called", False),
        "externalQueryCount": len(state.get("external_queries", [])),
        "toolsUsed": sorted({entry.get("action") for entry in state.get("action_history", []) if entry.get("action")}),
        "effectiveExpansionCount": min(5, sum(1 for item in state.get("observations", []) if item.get("action") in {"search_knowledge", "search_web", "resolve_musicbrainz"} and item.get("status") == "success" and (item.get("outputCount", 0) > 0 or item.get("hintCount", 0) > 0))),
        "qualityGate": state.get("quality_gate", {}),
    }


def _validate_candidate_quality(state: State) -> dict:
    """Deterministic final gate: facts only, never a hidden LLM judgement."""
    request, policy = state["request"], state.get("intent_policy")
    raw_available = state.get("ranked") or state.get("recordings", [])
    available = _deduplicate_versions(raw_available)
    eligible = [item for item in available if item.get("trustState") == "CATALOG_IMPORTED" and _candidate_allowed(item, policy)]
    issues: list[dict] = []
    if len(eligible) < request.size:
        issues.append({"code": "ACTIVE_COUNT_INSUFFICIENT", "severity": "BLOCKING"})
    duplicate_count = len(raw_available) - len(available)
    if duplicate_count:
        issues.append({"code": "DUPLICATE_VERSION_DETECTED", "severity": "BLOCKING"})
    if len(eligible) < request.size * 2:
        issues.append({"code": "RESERVE_CANDIDATES_INSUFFICIENT", "severity": "WARNING"})
    explanations = sum(1 for item in eligible if item.get("reason"))
    source_backed = sum(1 for item in eligible if item.get("sourceUrl"))
    external_count = sum(1 for item in eligible if item.get("catalogSource") == "EXTERNAL_VERIFIED")
    return {
        "passed": not any(issue["severity"] == "BLOCKING" for issue in issues),
        "issues": issues,
        "metrics": {
            "activeCount": min(request.size, len(eligible)), "totalCount": len(eligible),
            "catalogVerifiedRatio": 1.0 if eligible else 0.0,
            "explainedCandidateRatio": round(explanations / len(eligible), 2) if eligible else 0.0,
            "sourceBackedCandidateRatio": round(source_backed / len(eligible), 2) if eligible else 0.0,
            "externalVerifiedCount": external_count,
        },
    }


def _build_result(state: State, insufficient: bool) -> CandidatePoolResult:
    request = state["request"]
    policy = state.get("intent_policy")
    choices = [
        item for item in (state["ranked"] or _deduplicate_versions(state["recordings"]))
        if item.get("trustState") == "CATALOG_IMPORTED" and _candidate_allowed(item, policy)
    ]
    choices = choices[: request.size * 2]
    if insufficient or len(choices) < request.size * 2:
        dependency_reason = _insufficient_termination(state)
        termination_reason = (
            dependency_reason if dependency_reason == "DEPENDENCY_UNAVAILABLE"
            else state.get("termination_reason") or dependency_reason
        )
        return CandidatePoolResult(
            request_id=request.request_id, status="insufficient_candidates", size=request.size,
            reserve_size=max(0, len(choices) - request.size),
            recording_ids=[UUID(item["id"]) for item in choices],
            items=_candidate_items(choices, policy, {item.get("sourceUrl"): item for item in state["web_sources"]}, request.size),
            candidate_summary="已完成多轮在线发现与 MusicBrainz 核验，但可验证曲目仍不足以同时组成赛事与等量补位池。",
            warnings=[{"code": "INSUFFICIENT_CANDIDATES", "message": "可验证曲目不足以组成赛事与等量补位池，请调整兴趣方向后重试。"}],
            intent_policy=policy,
            termination_reason=termination_reason,
            trace_summary=_trace_summary(state),
        )
    warnings = []
    for issue in state.get("quality_gate", {}).get("issues", []):
        if issue.get("severity") == "WARNING":
            warnings.append({"code": issue["code"], "message": "候选池已满足开赛条件，但部分质量指标仍可继续优化。"})
    if any(item.get("action") == "resolve_musicbrainz" and item.get("status") == "failed" for item in state["observations"]):
        warnings.append({"code": "EXTERNAL_DISCOVERY_DEGRADED", "message": "外部歌曲核验暂不可用，本次候选范围可能较少。"})
    if len(choices) < request.size * 2:
        warnings.append({"code": "RESERVE_CANDIDATES_INSUFFICIENT", "message": "已满足开赛数量，但补位队列不足目标值。"})
    expansion_attempted = any(item.get("action") == "search_web" for item in state["observations"])
    imported_externally = any(
        item.get("action") == "resolve_musicbrainz" and item.get("outputCount", 0) > 0
        for item in state["observations"]
    )
    if expansion_attempted and not imported_externally:
        warnings.append({
            "code": "EXTERNAL_DISCOVERY_NO_VERIFIED_MATCHES",
            "message": "已尝试扩展相近方向，但没有找到通过实体核验的新歌曲；本次结果主要来自现有目录。",
        })
    source_by_url = {item.get("sourceUrl"): item for item in state["web_sources"]}
    termination_reason = state.get("termination_reason") or (
        "TARGET_REACHED_AND_VALIDATED" if len(choices) == request.size * 2 else "ACTIVE_SIZE_REACHED_SHORT_RESERVE"
    )
    return CandidatePoolResult(
        request_id=request.request_id, status="ready_for_confirmation", size=request.size,
        reserve_size=max(0, len(choices) - request.size),
        recording_ids=[UUID(item["id"]) for item in choices],
        items=_candidate_items(choices, policy, source_by_url, request.size),
        candidate_summary=state["summary"] or "候选池已通过规范实体与去重校验。",
        warnings=warnings,
        intent_policy=policy,
        termination_reason=termination_reason,
        trace_summary=_trace_summary(state),
    )


def _candidate_item(item: dict, policy: IntentPolicy | None, source_by_url: dict, pool_role: str = "MAIN") -> CandidateItem:
    reason = item.get("reason") or _fallback_reason(item, policy)
    source = source_by_url.get(item.get("sourceUrl"))
    rationale = [{"kind": "preference_match", "text": reason[:160]}]
    evidence_summary: list[dict] = []
    if source:
        domain = ""
        try:
            from urllib.parse import urlparse
            domain = urlparse(str(source.get("sourceUrl", ""))).netloc
        except ValueError:
            pass
        snippet = str(item.get("evidenceSnippet") or source.get("summary") or "").strip()[:280]
        if snippet:
            rationale.append({"kind": "creative_context", "text": snippet})
        evidence_summary.append({
            "title": str(source.get("sourceTitle") or "公开音乐资料")[:180],
            "domain": domain[:120], "url": str(source.get("sourceUrl") or ""),
            "trustLevel": "MEDIUM",
        })
    else:
        rationale.append({"kind": "catalog_match", "text": "基于已核验本地曲库与本场偏好生成。"})
    return CandidateItem(
        recording_id=item["id"], reason=reason,
        evidence=[source] if source else [],
        exploration_rationale=rationale[:2], evidence_summary=evidence_summary,
        discovery_sources=[{
            "type": "WEB_SEARCH" if source else "CATALOG",
            "provider": str(source.get("searchProvider") or "public_web") if source else "java_catalog",
            "url": str(source.get("sourceUrl") or "") if source else "",
            "query": str(source.get("searchQuery") or "") if source else "",
        }],
        quality_dimensions={
            "preferenceRelevance": "supported" if reason else "unknown",
            "identityConfidence": "verified",
            "sourceConfidence": "medium" if source else "catalog_verified",
            "constraintMatch": "passed",
        },
        pool_role=pool_role,
        verification_status="VERIFIED" if item.get("musicbrainzMbid") else "CATALOG_VERIFIED",
    )


def _candidate_items(choices: list[dict], policy: IntentPolicy | None, source_by_url: dict, active_size: int) -> list[CandidateItem]:
    return [
        _candidate_item(item, policy, source_by_url, "MAIN" if index < active_size else "RESERVE")
        for index, item in enumerate(choices)
    ]


def _insufficient_termination(state: State) -> str:
    relevant = [
        item for item in state.get("observations", [])
        if item.get("action") in {"search_catalog", "search_web", "resolve_musicbrainz"}
    ]
    if relevant and all(item.get("status") == "failed" for item in relevant):
        return "DEPENDENCY_UNAVAILABLE"
    return "INSUFFICIENT_VERIFIED_CANDIDATES"
