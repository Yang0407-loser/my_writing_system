"""Post-hoc evidence-contract audit for the completed WR1-R canary.

This module is deliberately separate from ``adversarial_experiment``.  It does
not alter the preregistered evaluator or its decision.  It binds a small manual
gold set to the eight frozen outputs, validates every cited excerpt, and reports
how well the old proxies agree with the gold judgments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / ".world_runtime_wr1r_canary_runtime"
DEFAULT_GOLD = (
    ROOT
    / "experiments/world_runtime_writer_canary/fixtures/wr1r_evidence_gold_v1.json"
)
DEFAULT_OUTPUT = (
    ROOT / "reports/world-runtime-wr1r-evidence-contract-evaluation-2026-08-04.json"
)
CONTRACT_VERSION = "world-runtime-result-evidence-contract-v1"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceAnchor(FrozenModel):
    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)


class BinaryJudgment(FrozenModel):
    value: bool
    reason_code: str = Field(min_length=1)
    basis: Literal["evidence", "counterevidence", "full_text_absence"]
    evidence: tuple[EvidenceAnchor, ...] = ()

    @model_validator(mode="after")
    def validate_evidence_semantics(self):
        if self.value and self.basis != "evidence":
            raise ValueError("true judgment requires evidence basis")
        if self.basis in {"evidence", "counterevidence"} and not self.evidence:
            raise ValueError("evidence-based judgment requires at least one anchor")
        if self.basis == "full_text_absence" and self.evidence:
            raise ValueError("absence judgment cannot carry fabricated spans")
        return self


class SettingCandidate(FrozenModel):
    category: Literal["new_event", "new_relationship", "new_project_fact", "state_change"]
    reason_code: str = Field(min_length=1)
    evidence: EvidenceAnchor


class GoldItem(FrozenModel):
    sample_id: str = Field(pattern=r"^WR1R-\d{2}$")
    scene_id: str = Field(min_length=1)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_event_completed: BinaryJudgment
    hard_reality_violations: dict[str, BinaryJudgment]
    task_evasion: BinaryJudgment
    unsourced_setting_candidates: tuple[SettingCandidate, ...] = ()


class GoldSet(FrozenModel):
    schema_version: Literal[CONTRACT_VERSION]
    status: Literal["posthoc_diagnostic_not_promotion_evidence"]
    items: tuple[GoldItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_samples(self):
        ids = [item.sample_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("gold sample IDs must be unique")
        return self


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _anchor_span(text: str, anchor: EvidenceAnchor) -> dict[str, object]:
    cursor = 0
    start = -1
    for _ in range(anchor.occurrence):
        start = text.find(anchor.excerpt, cursor)
        if start < 0:
            raise ValueError(f"unsupported evidence excerpt: {anchor.claim}")
        cursor = start + len(anchor.excerpt)
    return {
        "claim": anchor.claim,
        "start": start,
        "end": start + len(anchor.excerpt),
        "excerpt": anchor.excerpt,
    }


def _compile_judgment(text: str, judgment: BinaryJudgment) -> dict[str, object]:
    return {
        "value": judgment.value,
        "reason_code": judgment.reason_code,
        "basis": judgment.basis,
        "evidence": [_anchor_span(text, anchor) for anchor in judgment.evidence],
    }


def load_and_validate_gold(
    runtime_dir: Path = DEFAULT_RUNTIME,
    gold_path: Path = DEFAULT_GOLD,
) -> tuple[GoldSet, list[dict[str, object]]]:
    gold = GoldSet.model_validate(_read(gold_path))
    if len(gold.items) != 8:
        raise ValueError("WR1-R evidence gold must contain exactly eight samples")
    compiled = []
    for item in gold.items:
        output_path = runtime_dir / "private/outputs" / f"{item.sample_id}.txt"
        text = output_path.read_text(encoding="utf-8")
        if _sha256_text(text) != item.output_sha256:
            raise ValueError(f"output hash mismatch: {item.sample_id}")
        compiled.append(
            {
                "sample_id": item.sample_id,
                "scene_id": item.scene_id,
                "output_sha256": item.output_sha256,
                "required_event_completed": _compile_judgment(
                    text, item.required_event_completed
                ),
                "hard_reality_violations": {
                    check_id: _compile_judgment(text, judgment)
                    for check_id, judgment in item.hard_reality_violations.items()
                },
                "task_evasion": _compile_judgment(text, item.task_evasion),
                "unsourced_setting_candidates": [
                    {
                        "category": candidate.category,
                        "reason_code": candidate.reason_code,
                        "evidence": _anchor_span(text, candidate.evidence),
                    }
                    for candidate in item.unsourced_setting_candidates
                ],
            }
        )
    return gold, compiled


def _confusion(pairs: list[tuple[bool, bool]]) -> dict[str, object]:
    tp = sum(predicted and actual for predicted, actual in pairs)
    fp = sum(predicted and not actual for predicted, actual in pairs)
    fn = sum(not predicted and actual for predicted, actual in pairs)
    tn = sum(not predicted and not actual for predicted, actual in pairs)

    def ratio(numerator: int, denominator: int):
        return round(numerator / denominator, 4) if denominator else None

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "specificity": ratio(tn, tn + fp),
        "accuracy": ratio(tp + tn, len(pairs)),
    }


def evaluate_proxy_accuracy(
    runtime_dir: Path = DEFAULT_RUNTIME,
    gold_path: Path = DEFAULT_GOLD,
) -> dict[str, object]:
    gold, compiled = load_and_validate_gold(runtime_dir, gold_path)
    frozen = _read(runtime_dir / "evaluation.json")
    frozen_by_id = {item["sample_id"]: item for item in frozen["items"]}
    gold_by_id = {item.sample_id: item for item in gold.items}
    if set(frozen_by_id) != set(gold_by_id):
        raise ValueError("frozen evaluation and evidence gold sample sets differ")

    event_pairs: list[tuple[bool, bool]] = []
    violation_pairs: list[tuple[bool, bool]] = []
    mismatches: list[dict[str, object]] = []
    for sample_id, item in gold_by_id.items():
        proxy = frozen_by_id[sample_id]["checks"]
        event_predicted = bool(proxy["must_event_pass"])
        event_actual = item.required_event_completed.value
        event_pairs.append((event_predicted, event_actual))
        if event_predicted != event_actual:
            mismatches.append(
                {
                    "sample_id": sample_id,
                    "target": "required_event_completed",
                    "proxy": event_predicted,
                    "gold": event_actual,
                }
            )
        proxy_violations = proxy["hard_reality_violations"]
        if set(proxy_violations) != set(item.hard_reality_violations):
            raise ValueError(f"violation target drift: {sample_id}")
        for check_id, judgment in item.hard_reality_violations.items():
            predicted = bool(proxy_violations[check_id])
            actual = judgment.value
            violation_pairs.append((predicted, actual))
            if predicted != actual:
                mismatches.append(
                    {
                        "sample_id": sample_id,
                        "target": check_id,
                        "proxy": predicted,
                        "gold": actual,
                    }
                )

    task_evasion_positive = sum(item.task_evasion.value for item in gold.items)
    unsourced_samples = sum(bool(item.unsourced_setting_candidates) for item in gold.items)
    result = {
        "schema_version": "world-runtime-proxy-accuracy-audit-v1",
        "status": "posthoc_diagnostic_not_promotion_evidence",
        "source_evaluation_sha256": hashlib.sha256(
            (runtime_dir / "evaluation.json").read_bytes()
        ).hexdigest(),
        "gold_fixture_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        "sample_count": len(gold.items),
        "compiled_evidence": compiled,
        "proxy_accuracy": {
            "required_event_completed": _confusion(event_pairs),
            "hard_reality_violations": _confusion(violation_pairs),
            "task_evasion": {
                "evaluator_coverage": False,
                "gold_positive_samples": task_evasion_positive,
            },
            "unsourced_setting": {
                "evaluator_coverage": False,
                "gold_positive_samples": unsourced_samples,
            },
        },
        "mismatches": mismatches,
        "gate": {
            "minimum_violation_precision": 0.9,
            "minimum_violation_recall": 0.9,
            "minimum_event_recall": 0.9,
            "required_dimensions_covered": ["task_evasion", "unsourced_setting"],
            "passed": False,
        },
        "decision": "evaluator_rebuild_required_before_new_generation",
    }
    violation = result["proxy_accuracy"]["hard_reality_violations"]
    event = result["proxy_accuracy"]["required_event_completed"]
    result["gate"]["passed"] = bool(
        violation["precision"] is not None
        and violation["precision"] >= result["gate"]["minimum_violation_precision"]
        and violation["recall"] is not None
        and violation["recall"] >= result["gate"]["minimum_violation_recall"]
        and event["recall"] is not None
        and event["recall"] >= result["gate"]["minimum_event_recall"]
        and result["proxy_accuracy"]["task_evasion"]["evaluator_coverage"]
        and result["proxy_accuracy"]["unsourced_setting"]["evaluator_coverage"]
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate_proxy_accuracy(args.runtime, args.gold)
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
