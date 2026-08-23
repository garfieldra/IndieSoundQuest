from __future__ import annotations

import re
from collections import Counter
from typing import Any


_GENERIC_REASON = re.compile(r"^(?:符合.{0,8}偏好|来自.{0,12}目录|适合你|推荐这首歌)[。！]?$", re.IGNORECASE)


def evaluate_candidate_result(case: dict[str, Any], result: dict[str, Any], catalog: dict[str, dict]) -> dict[str, Any]:
    expected = case.get("expected", {})
    pool = result.get("candidatePool", result)
    ids = [str(value) for value in pool.get("recordingIds", [])]
    items = pool.get("items", [])
    intent = pool.get("intentPolicy") or result.get("intentPolicy") or {}
    allowed = {str(value) for value in expected.get("allowedArtistIds", [])}
    found = [catalog.get(recording_id) for recording_id in ids]
    external = [item for item in found if item and item.get("sourceType") == "external"]
    generic_count = sum(1 for item in items if _GENERIC_REASON.match(str(item.get("reason", "")).strip()))
    locked_violations = sum(
        1 for item in found
        if allowed and item and str(item.get("artistId")) not in allowed
    )
    active_size = int(case.get("input", {}).get("size", 16))
    trace = pool.get("traceSummary") or result.get("traceSummary") or {}
    unresolved_hint_leaks = sum(item is None for item in found) + sum(
        1 for item in items if item.get("trustState") in {"DISCOVERY_HINT", "MB_SEARCHED", "MB_AMBIGUOUS", "MB_VERIFIED"}
    )
    version_groups = Counter(
        item.get("versionGroupKey") for item in found if item and item.get("versionGroupKey")
    )
    version_collisions = sum(count - 1 for count in version_groups.values() if count > 1)
    metrics = {
        "caseId": case.get("caseId"),
        "contractValid": bool(pool.get("status") or result.get("status")) and len(items) == len(ids),
        "recordingIdUnique": len(ids) == len(set(ids)),
        "catalogExistenceRate": _ratio(sum(item is not None for item in found), len(ids)),
        "lockedArtistPrecision": _ratio(len(found) - locked_violations, len(found)) if allowed else None,
        "externalEntityVerificationRate": _ratio(
            sum(bool(item.get("musicbrainzMbid") and item.get("recordingId")) for item in external),
            len(external),
        ) if external else None,
        "activePoolComplete": len(ids) >= active_size,
        "reservePoolComplete": len(ids) >= active_size * 2,
        "genericReasonRate": _ratio(generic_count, len(items)),
        "versionCollisionCount": version_collisions,
        "toolBudgetViolationCount": int(int(trace.get("toolCalls", 0)) > 10),
        "unresolvedHintLeakCount": unresolved_hint_leaks,
        "intentMode": intent.get("intentMode"),
        "terminationReason": pool.get("terminationReason") or result.get("terminationReason"),
    }
    expected_insufficient = bool(expected.get("expectInsufficient"))
    metrics["hardGatePassed"] = all([
        metrics["contractValid"], metrics["recordingIdUnique"],
        metrics["catalogExistenceRate"] == 1.0,
        metrics["lockedArtistPrecision"] in {None, 1.0},
        metrics["externalEntityVerificationRate"] in {None, 1.0},
        metrics["versionCollisionCount"] == 0,
        metrics["toolBudgetViolationCount"] == 0,
        metrics["unresolvedHintLeakCount"] == 0,
        expected_insufficient or metrics["activePoolComplete"],
    ])
    return metrics


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    boolean_keys = ["contractValid", "recordingIdUnique", "activePoolComplete", "reservePoolComplete", "hardGatePassed"]
    rates = {
        key + "Rate": _ratio(sum(bool(item.get(key)) for item in results), len(results))
        for key in boolean_keys
    }
    numeric_keys = ["catalogExistenceRate", "lockedArtistPrecision", "externalEntityVerificationRate", "genericReasonRate"]
    for key in numeric_keys:
        values = [float(item[key]) for item in results if item.get(key) is not None]
        rates[key] = sum(values) / len(values) if values else None
    rates["terminationReasons"] = dict(Counter(item.get("terminationReason") or "UNKNOWN" for item in results))
    for key in ["versionCollisionCount", "toolBudgetViolationCount", "unresolvedHintLeakCount"]:
        rates[key] = sum(int(item.get(key, 0)) for item in results)
    return rates


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
