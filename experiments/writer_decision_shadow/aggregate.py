from __future__ import annotations

import itertools
import statistics
from collections import Counter, defaultdict
from typing import Any

from .models import SceneDecisionTicket, ShadowCorpus, ValidatorReview
from .review import PROCESS_CATEGORIES, UNAUTHORIZED_CATEGORIES, validate_reviews


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _majority(values: list[str]) -> str | None:
    counts = Counter(values).most_common()
    if not counts:
        return None
    if len(counts) == 1 or counts[0][1] > counts[1][1]:
        return counts[0][0]
    return None


def _fleiss_kappa(items: list[list[str]]) -> float | None:
    if not items:
        return None
    raters = len(items[0])
    if raters < 2 or any(len(item) != raters for item in items):
        return None
    categories = sorted({value for item in items for value in item})
    total_ratings = len(items) * raters
    category_totals = Counter(value for item in items for value in item)
    expected = sum((category_totals[category] / total_ratings) ** 2 for category in categories)
    observed_rows = []
    for item in items:
        counts = Counter(item)
        observed_rows.append(
            sum(count * (count - 1) for count in counts.values())
            / (raters * (raters - 1))
        )
    observed = statistics.mean(observed_rows)
    if expected == 1:
        return 1.0
    return round((observed - expected) / (1 - expected), 4)


def _agreement(items: list[dict[str, Any]], *, label_key: str) -> dict[str, Any]:
    label_rows = [item[label_key] for item in items]
    unanimous = sum(len(set(row)) == 1 for row in label_rows)
    majority = sum(_majority(row) is not None for row in label_rows)
    pair_equal = 0
    pair_total = 0
    for row in label_rows:
        for left, right in itertools.combinations(row, 2):
            pair_total += 1
            pair_equal += left == right
    return {
        "item_count": len(label_rows),
        "unanimous_count": unanimous,
        "unanimous_rate": _rate(unanimous, len(label_rows)),
        "majority_count": majority,
        "majority_rate": _rate(majority, len(label_rows)),
        "pairwise_equal_count": pair_equal,
        "pairwise_comparison_count": pair_total,
        "pairwise_agreement": _rate(pair_equal, pair_total),
        "fleiss_kappa": _fleiss_kappa(label_rows),
    }


def _paragraph_number(value: str) -> int:
    return int(value[1:])


def _localization_class(left: set[str], right: set[str]) -> str:
    if not left or not right:
        return "not_comparable"
    if left & right:
        return "exact_overlap"
    if any(
        abs(_paragraph_number(a) - _paragraph_number(b)) == 1
        for a in left
        for b in right
    ):
        return "adjacent_overlap"
    return "no_overlap"


def _hard_locations(row: Any) -> set[str]:
    if row.status == "present":
        return set(row.evidence_paragraphs)
    if row.status == "contradicted":
        return set(row.contradiction_paragraphs)
    if row.status == "violated":
        return set(row.violation_paragraphs)
    return set()


def _soft_locations(row: Any) -> set[str]:
    if row.status == "fail":
        return set(row.violation_paragraphs)
    return set(row.evidence_paragraphs)


def aggregate_reviews(
    ticket: SceneDecisionTicket,
    corpus: ShadowCorpus,
    raw_reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    reviews, validation = validate_reviews(ticket, corpus, raw_reviews)
    review_by_id: dict[str, ValidatorReview] = {
        review.reviewer_id: review for review in reviews
    }
    reviewer_ids = list(review_by_id)
    ticket_refs = {item.ref_id for item in ticket.source_refs}
    obligations = [
        *ticket.hard_obligations,
        *ticket.soft_topology_obligations,
    ]
    source_rows = {
        item.decision_id: {
            "source_refs": item.source_refs,
            "all_refs_resolved": set(item.source_refs) <= ticket_refs,
        }
        for item in obligations
    }
    source_covered = sum(item["all_refs_resolved"] for item in source_rows.values())

    hard_items = []
    soft_items = []
    hard_location_counts = Counter()
    soft_location_counts = Counter()
    hard_modes = {
        item.decision_id: item.verification_mode
        for item in ticket.hard_obligations
    }
    execution_by_sample: dict[str, Any] = {}

    for sample_index, sample in enumerate(corpus.samples):
        execution = Counter()
        hard_details = []
        for obligation_index, obligation in enumerate(ticket.hard_obligations):
            rows = [
                review.samples[sample_index].hard_obligations[obligation_index]
                for review in reviews
            ]
            labels = [row.status for row in rows]
            majority = _majority(labels)
            hard_items.append(
                {
                    "blind_id": sample.blind_id,
                    "decision_id": obligation.decision_id,
                    "labels": labels,
                    "majority": majority,
                }
            )
            for left, right in itertools.combinations(rows, 2):
                hard_location_counts[
                    _localization_class(_hard_locations(left), _hard_locations(right))
                ] += 1
            mode = hard_modes[obligation.decision_id]
            if majority is None:
                outcome = "split"
            elif majority == "unverifiable":
                outcome = "unverifiable"
            elif mode in {"presence", "state_match"}:
                outcome = {
                    "present": "executed",
                    "absent": "missing",
                    "contradicted": "conflict",
                }.get(majority, "conflict")
            else:
                outcome = "executed" if majority == "respected" else "conflict"
            execution[outcome] += 1
            hard_details.append(
                {
                    "decision_id": obligation.decision_id,
                    "majority_status": majority,
                    "execution_outcome": outcome,
                }
            )

        for obligation_index, obligation in enumerate(
            ticket.soft_topology_obligations
        ):
            rows = [
                review.samples[sample_index].soft_topology[obligation_index]
                for review in reviews
            ]
            labels = [row.status for row in rows]
            soft_items.append(
                {
                    "blind_id": sample.blind_id,
                    "decision_id": obligation.decision_id,
                    "labels": labels,
                    "majority": _majority(labels),
                }
            )
            for left, right in itertools.combinations(rows, 2):
                soft_location_counts[
                    _localization_class(_soft_locations(left), _soft_locations(right))
                ] += 1

        total = len(ticket.hard_obligations)
        execution_by_sample[sample.blind_id] = {
            "hard_obligation_count": total,
            "executed": execution["executed"],
            "missing": execution["missing"],
            "conflict": execution["conflict"],
            "unverifiable": execution["unverifiable"],
            "split": execution["split"],
            "execution_rate": _rate(execution["executed"], total),
            "details": hard_details,
        }

    unauthorized: dict[str, Any] = {}
    for category_index, category in enumerate(UNAUTHORIZED_CATEGORIES):
        votes = 0
        majority_samples = []
        evidence = []
        for sample_index, sample in enumerate(corpus.samples):
            rows = [
                review.samples[sample_index].unauthorized_content[category_index]
                for review in reviews
            ]
            detected = [row.detected for row in rows]
            votes += sum(detected)
            if sum(detected) >= 2:
                majority_samples.append(sample.blind_id)
            for reviewer_id, row in zip(reviewer_ids, rows):
                if row.detected:
                    evidence.append(
                        {
                            "reviewer_id": reviewer_id,
                            "blind_id": sample.blind_id,
                            "paragraphs": row.paragraphs,
                            "description": row.description,
                        }
                    )
        unauthorized[category] = {
            "detected_votes": votes,
            "majority_sample_count": len(majority_samples),
            "majority_samples": majority_samples,
            "evidence": evidence,
        }

    process_logs: dict[str, Any] = {}
    for category_index, category in enumerate(PROCESS_CATEGORIES):
        votes = 0
        majority_samples = []
        evidence = []
        for sample_index, sample in enumerate(corpus.samples):
            rows = [
                review.samples[sample_index].process_log_checks[category_index]
                for review in reviews
            ]
            detected = [row.detected for row in rows]
            votes += sum(detected)
            if sum(detected) >= 2:
                majority_samples.append(sample.blind_id)
            for reviewer_id, row in zip(reviewer_ids, rows):
                if row.detected:
                    evidence.append(
                        {
                            "reviewer_id": reviewer_id,
                            "blind_id": sample.blind_id,
                            "paragraphs": row.paragraphs,
                            "description": row.description,
                        }
                    )
        process_logs[category] = {
            "detected_votes": votes,
            "majority_sample_count": len(majority_samples),
            "majority_samples": majority_samples,
            "evidence": evidence,
        }

    hard_agreement = _agreement(hard_items, label_key="labels")
    soft_agreement = _agreement(soft_items, label_key="labels")
    return {
        "schema_version": "1.0",
        "experiment_scope": (
            "Tests whether predeclared decisions are independently verifiable; "
            "does not compare style arms or prove prose-quality improvement."
        ),
        "validation": validation,
        "ticket_source_coverage": {
            "covered_obligations": source_covered,
            "total_obligations": len(source_rows),
            "coverage": _rate(source_covered, len(source_rows)),
            "by_obligation": source_rows,
        },
        "hard_decision_agreement": hard_agreement,
        "soft_topology_agreement": soft_agreement,
        "evidence_localization": {
            "hard": dict(hard_location_counts),
            "soft": dict(soft_location_counts),
        },
        "unauthorized_content_detection": unauthorized,
        "process_log_detection": process_logs,
        "decision_execution_coverage": execution_by_sample,
        "acceptance_checks": {
            "ticket_source_coverage_100pct": source_covered == len(source_rows),
            "hard_pairwise_agreement_at_least_90pct": (
                hard_agreement["pairwise_agreement"] >= 0.9
            ),
            "soft_pairwise_agreement_at_least_75pct": (
                soft_agreement["pairwise_agreement"] >= 0.75
            ),
        },
        "single_total_score_prohibited": True,
        "route_effect_conclusion_allowed": False,
    }

