"""Finalize Phase 3 Batch 1 metrics from the completed human review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.quality.baseline import DEFAULT_RAG, ROOT, load_json
from tests.quality.phase3_retrieval_eval import (
    human_review_failure_observations,
    human_review_metrics,
)


DEFAULT_REVIEW = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"
DEFAULT_SHADOW_REPORT = ROOT / "reports" / "phase3-shadow-retrieval.json"
DEFAULT_OUTPUT = ROOT / "reports" / "phase3-human-evaluation.json"


def build_evaluation(annotation: dict, review: dict, shadow_report: dict) -> dict:
    metrics = human_review_metrics(annotation, review)
    failures = human_review_failure_observations(review)
    writer_uses_legacy = (
        shadow_report.get("profile", {}).get("writer_uses") == "legacy"
        and shadow_report.get("production_switched") is False
    )
    gates = {
        "human_precision_at_5_at_least_0_68": (
            metrics["human_precision_at_5"] is not None
            and metrics["human_precision_at_5"] >= 0.68
        ),
        # This is the only new recall value comparable to the legacy 66.7%
        # section-based baseline. Fact coverage is deliberately a separate gate.
        "section_recall_at_5_above_0_6667": (
            metrics["section_recall_at_5"] is not None
            and metrics["section_recall_at_5"] > 0.6667
        ),
        "late_chapter_human_precision_at_5_above_0_40": (
            metrics["late_chapter_human_precision_at_5"] is not None
            and metrics["late_chapter_human_precision_at_5"] > 0.40
        ),
        "writer_input_and_behavior_unchanged": writer_uses_legacy,
    }
    return {
        "schema_version": 1,
        "sources": {
            "review": "tests/quality/phase3_shadow_candidates_review.json",
            "annotations": "tests/rag_annotation_07d1391e.json",
            "shadow_report": "reports/phase3-shadow-retrieval.json",
        },
        "metric_scope": {
            "release_recall": "section_recall_at_5, comparable to the legacy section-based 66.7% baseline",
            "additional_recall": "fact_coverage_recall, stricter and not substituted for the legacy recall gate",
            "late_query_rule": "current_section >= 13",
        },
        "metrics": metrics,
        "failure_observations": failures,
        "gates": gates,
        "all_release_gates_passed": all(gates.values()),
        "production_switched": False,
        "decision": "remain_shadow",
        "next_batch_recommendation": (
            "Do not start automatically. Phase 3 Batch 2 should first ablate broad intents, "
            "character-overlap saturation, threshold, query count and token budget; label "
            "unselected candidates before assigning missing facts causally to coarse recall."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, default=DEFAULT_RAG)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--shadow-report", type=Path, default=DEFAULT_SHADOW_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    evaluation = build_evaluation(
        load_json(args.annotations),
        load_json(args.review),
        load_json(args.shadow_report),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "metrics": evaluation["metrics"],
        "gates": evaluation["gates"],
        "decision": evaluation["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
