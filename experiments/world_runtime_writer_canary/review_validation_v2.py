"""Blind-review intake validation for the WR1-M writer canary.

This module deliberately knows nothing about the private arm key or machine
evaluation.  It validates only a submitted review result against the public
blind package, so a review can be frozen before any reveal happens.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


EXPECTED_EVENT_IDS = {
    "enter_workshop",
    "publish_article",
    "share_with_jiqing",
    "deliver_resignation",
}
ALLOWED_REVIEWER_TYPES = {"human", "model"}
ALLOWED_ILLEGAL_TRANSITION_VALUES = {"yes", "no", "uncertain"}


@dataclass(frozen=True)
class ReviewValidationReceipt:
    result_sha256: str
    package_sha256: str
    reviewer_id: str | None
    reviewer_type: str | None
    review_count: int
    candidate_count: int
    valid: bool
    human_vote_eligible: bool
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = list(self.issues)
        return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path, label: str, issues: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(f"{label}: cannot load UTF-8 JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        issues.append(f"{label}: root must be an object")
        return {}
    return value


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _excerpt_is_supported(text: str, excerpt: str) -> bool:
    """Accept an exact excerpt or ordered exact fragments joined by ellipses."""

    haystack = _normalized(text)
    fragments = [
        _normalized(part)
        for part in re.split(r"(?:…{2,}|\.{3,})", excerpt)
        if _normalized(part)
    ]
    if not fragments:
        return False
    cursor = 0
    for fragment in fragments:
        found = haystack.find(fragment, cursor)
        if found < 0:
            return False
        cursor = found + len(fragment)
    return True


def _require_int_range(
    value: Any,
    minimum: int,
    maximum: int,
    field: str,
    issues: list[str],
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(f"{field}: must be an integer")
    elif not minimum <= value <= maximum:
        issues.append(f"{field}: must be in {minimum}..{maximum}")


def validate_review_result(
    result_path: str | Path,
    package_path: str | Path,
) -> ReviewValidationReceipt:
    """Validate and classify one blind-review submission without revealing arms."""

    result_path = Path(result_path)
    package_path = Path(package_path)
    issues: list[str] = []
    result_sha256 = _sha256(result_path) if result_path.is_file() else ""
    package_sha256 = _sha256(package_path) if package_path.is_file() else ""
    result = _load_object(result_path, "result", issues)
    package = _load_object(package_path, "package", issues)

    candidates = package.get("candidates", [])
    if not isinstance(candidates, list):
        issues.append("package.candidates: must be a list")
        candidates = []
    candidate_by_id = {
        item.get("candidate_id"): item
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
    }
    if len(candidate_by_id) != len(candidates):
        issues.append("package.candidates: candidate IDs must be present and unique")
    if package.get("candidate_count") != len(candidates):
        issues.append("package.candidate_count: does not match candidates")
    if package.get("arms_hidden") is not True:
        issues.append("package.arms_hidden: must be true")

    reviewer = result.get("reviewer", {})
    if not isinstance(reviewer, dict):
        issues.append("reviewer: must be an object")
        reviewer = {}
    reviewer_id = reviewer.get("reviewer_id")
    reviewer_type = reviewer.get("reviewer_type")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        issues.append("reviewer.reviewer_id: must be a non-empty string")
        reviewer_id = None
    if reviewer_type not in ALLOWED_REVIEWER_TYPES:
        issues.append("reviewer.reviewer_type: must be human or model")
        reviewer_type = None
    for field, expected in (
        ("independent_review", True),
        ("read_other_reviews", False),
        ("read_blind_key", False),
        ("read_machine_evaluation", False),
        ("blindness_compromised", False),
    ):
        if reviewer.get(field) is not expected:
            issues.append(f"reviewer.{field}: must be {str(expected).lower()}")

    source = result.get("source", {})
    if not isinstance(source, dict):
        issues.append("source: must be an object")
        source = {}
    if source.get("package_sha256", "").lower() != package_sha256:
        issues.append("source.package_sha256: does not match blind package")
    if source.get("candidate_count") != len(candidates):
        issues.append("source.candidate_count: does not match blind package")

    reviews = result.get("reviews", [])
    if not isinstance(reviews, list):
        issues.append("reviews: must be a list")
        reviews = []
    review_ids = [
        item.get("candidate_id") for item in reviews if isinstance(item, dict)
    ]
    if len(review_ids) != len(set(review_ids)):
        issues.append("reviews: candidate IDs must be unique")
    if set(review_ids) != set(candidate_by_id):
        issues.append("reviews: must cover exactly every blind-package candidate")

    for index, review in enumerate(reviews):
        prefix = f"reviews[{index}]"
        if not isinstance(review, dict):
            issues.append(f"{prefix}: must be an object")
            continue
        candidate_id = review.get("candidate_id")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            continue
        if review.get("scene_id") != candidate.get("scene_id"):
            issues.append(f"{prefix}.scene_id: does not match blind package")
        events = review.get("events", {})
        if not isinstance(events, dict) or set(events) != EXPECTED_EVENT_IDS:
            issues.append(f"{prefix}.events: must contain exactly the four required events")
            events = events if isinstance(events, dict) else {}
        for event_id, event in events.items():
            event_prefix = f"{prefix}.events.{event_id}"
            if not isinstance(event, dict):
                issues.append(f"{event_prefix}: must be an object")
                continue
            for field in ("outcome", "bridge", "evidence"):
                _require_int_range(event.get(field), 0, 2, f"{event_prefix}.{field}", issues)
            if event.get("illegal_transition") not in ALLOWED_ILLEGAL_TRANSITION_VALUES:
                issues.append(
                    f"{event_prefix}.illegal_transition: must be yes, no, or uncertain"
                )
            excerpt = event.get("evidence_excerpt")
            if not isinstance(excerpt, str) or not _excerpt_is_supported(
                str(candidate.get("text", "")), excerpt
            ):
                issues.append(f"{event_prefix}.evidence_excerpt: unsupported by candidate text")

        unsourced = review.get("unsourced_settings", [])
        if not isinstance(unsourced, list):
            issues.append(f"{prefix}.unsourced_settings: must be a list")
            unsourced = []
        for setting_index, setting in enumerate(unsourced):
            setting_prefix = f"{prefix}.unsourced_settings[{setting_index}]"
            if not isinstance(setting, dict):
                issues.append(f"{setting_prefix}: must be an object")
                continue
            _require_int_range(
                setting.get("severity"), 0, 3, f"{setting_prefix}.severity", issues
            )
            excerpt = setting.get("evidence_excerpt")
            if not isinstance(excerpt, str) or not _excerpt_is_supported(
                str(candidate.get("text", "")), excerpt
            ):
                issues.append(f"{setting_prefix}.evidence_excerpt: unsupported by candidate text")

        overall = review.get("overall", {})
        if not isinstance(overall, dict):
            issues.append(f"{prefix}.overall: must be an object")
            continue
        for field in (
            "world_consistency",
            "prose_naturalness",
            "instructional_feel",
        ):
            _require_int_range(overall.get(field), 1, 5, f"{prefix}.overall.{field}", issues)
        for field in (
            "required_outcome_complete",
            "required_bridge_complete",
            "evidence_sufficient",
        ):
            _require_int_range(overall.get(field), 0, 2, f"{prefix}.overall.{field}", issues)
        _require_int_range(
            overall.get("unsourced_setting_severity"),
            0,
            3,
            f"{prefix}.overall.unsourced_setting_severity",
            issues,
        )

    scene_candidates: dict[str, set[str]] = {}
    for candidate_id, candidate in candidate_by_id.items():
        scene_candidates.setdefault(str(candidate.get("scene_id")), set()).add(candidate_id)
    rankings = result.get("scene_rankings", [])
    if not isinstance(rankings, list):
        issues.append("scene_rankings: must be a list")
        rankings = []
    ranking_scene_ids = [
        item.get("scene_id") for item in rankings if isinstance(item, dict)
    ]
    if len(ranking_scene_ids) != len(set(ranking_scene_ids)):
        issues.append("scene_rankings: scene IDs must be unique")
    if set(ranking_scene_ids) != set(scene_candidates):
        issues.append("scene_rankings: must cover exactly every scene")
    for index, ranking in enumerate(rankings):
        prefix = f"scene_rankings[{index}]"
        if not isinstance(ranking, dict):
            issues.append(f"{prefix}: must be an object")
            continue
        expected = scene_candidates.get(str(ranking.get("scene_id")), set())
        for field in ("consistency_ranking", "prose_ranking"):
            value = ranking.get(field)
            if not isinstance(value, list) or len(value) != len(set(value)) or set(value) != expected:
                issues.append(f"{prefix}.{field}: must rank every scene candidate exactly once")
        if ranking.get("best_balance") not in expected:
            issues.append(f"{prefix}.best_balance: must be a candidate from the scene")

    if result.get("review_complete") is not True:
        issues.append("review_complete: must be true")

    valid = not issues
    return ReviewValidationReceipt(
        result_sha256=result_sha256,
        package_sha256=package_sha256,
        reviewer_id=reviewer_id,
        reviewer_type=reviewer_type,
        review_count=len(reviews),
        candidate_count=len(candidates),
        valid=valid,
        human_vote_eligible=valid and reviewer_type == "human",
        issues=tuple(issues),
    )

