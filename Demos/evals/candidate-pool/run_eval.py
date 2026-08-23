#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "agent-service"))

from app.intent_rules import classify_intent_rule  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def intent_eval(cases: list[dict]) -> tuple[list[dict], dict]:
    results = []
    for case in cases:
        request = case["input"]
        seeds = request.get("seedArtistIds", [])
        policy = classify_intent_rule(request["preferenceText"], seeds)
        expected = case["expected"]["intentMode"]
        results.append({"caseId": case["caseId"], "category": case["category"], "expected": expected,
                        "actual": policy["intent_mode"], "passed": expected == policy["intent_mode"],
                        "facets": policy["preference_facets"]})
    by_category = {}
    for category in sorted({item["category"] for item in results}):
        selected = [item for item in results if item["category"] == category]
        by_category[category] = sum(item["passed"] for item in selected) / len(selected)
    passed = sum(item["passed"] for item in results)
    return results, {"mode": "intent-fixture", "caseCount": len(results), "passed": passed,
                     "failed": len(results) - passed, "accuracy": passed / len(results),
                     "accuracyByCategory": by_category}


def result_eval(cases: list[dict], results_path: Path, catalog_path: Path, use_judge: bool = False) -> tuple[list[dict], dict]:
    from app.evaluation import aggregate_metrics, evaluate_candidate_result
    raw_results = {item["caseId"]: item["result"] for item in read_jsonl(results_path)}
    catalog = {str(item["recordingId"]): item for item in read_jsonl(catalog_path)}
    evaluated = [evaluate_candidate_result(case, raw_results[case["caseId"]], catalog)
                 for case in cases if case["caseId"] in raw_results]
    summary = {"mode": "candidate-result", "caseCount": len(evaluated), **aggregate_metrics(evaluated)}
    if use_judge:
        from app.candidate_judge import CandidateQualityJudge

        async def judge_all():
            judge = CandidateQualityJudge()
            output = {}
            for case in cases:
                if case["caseId"] in raw_results:
                    judged = await judge.evaluate(case, raw_results[case["caseId"]], catalog)
                    output[case["caseId"]] = judged.model_dump(mode="json")
            return output

        judgments = asyncio.run(judge_all())
        for item in evaluated:
            item["llmJudge"] = judgments.get(item["caseId"])
        all_scores = [score for judgment in judgments.values() for score in judgment["scores"].values()]
        summary["llmJudgeOverallMean"] = sum(all_scores) / len(all_scores) if all_scores else None
    return evaluated, summary


def write_report(output_dir: Path, details: list[dict], summary: dict) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"candidate-eval-{stamp}.json"
    markdown_path = output_dir / f"candidate-eval-{stamp}.md"
    payload = {"generatedAt": datetime.now(timezone.utc).isoformat(), "summary": summary, "details": details}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [item for item in details if item.get("passed") is False]
    lines = ["# Candidate Pool Agent Eval", "", f"- Mode: `{summary['mode']}`",
             f"- Cases: {summary['caseCount']}", f"- Summary: `{json.dumps(summary, ensure_ascii=False)}`",
             "", "## Failures", ""]
    lines.extend(f"- `{item.get('caseId')}`: expected `{item.get('expected')}`, got `{item.get('actual')}`" for item in failures)
    if not failures:
        lines.append("- None")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate candidate-pool intent and result contracts")
    parser.add_argument("--mode", choices=["intent", "result"], default="intent")
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output-dir", type=Path, default=HERE / "reports")
    parser.add_argument("--llm-judge", action="store_true", help="Run the independent DeepSeek quality judge in result mode")
    args = parser.parse_args()
    cases = read_jsonl(args.cases)
    if args.mode == "intent":
        details, summary = intent_eval(cases)
    else:
        if not args.results or not args.catalog:
            parser.error("--results and --catalog are required in result mode")
        details, summary = result_eval(cases, args.results, args.catalog, args.llm_judge)
    json_path, markdown_path = write_report(args.output_dir, details, summary)
    print(json.dumps({"summary": summary, "jsonReport": str(json_path), "markdownReport": str(markdown_path)}, ensure_ascii=False, indent=2))
    return 1 if summary.get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
