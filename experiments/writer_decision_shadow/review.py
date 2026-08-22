from __future__ import annotations

from typing import Any

from .models import (
    ProcessCategory,
    SceneDecisionTicket,
    ShadowCorpus,
    UnauthorizedCategory,
    ValidatorReview,
)


UNAUTHORIZED_CATEGORIES: tuple[UnauthorizedCategory, ...] = (
    "new_character_causal_authority",
    "new_solution",
    "unapproved_relationship_change",
    "new_responsibility_or_commitment",
    "unsourced_object_or_quantity_fact",
    "direct_relationship_explanation",
)
PROCESS_CATEGORIES: tuple[ProcessCategory, ...] = (
    "repeated_transport",
    "itemized_inventory",
    "continuous_counting",
    "cost_accounting",
    "logistics_exposition",
)


def build_review_template(
    ticket: SceneDecisionTicket,
    corpus: ShadowCorpus,
    *,
    reviewer_id: str = "",
) -> dict[str, Any]:
    return {
        "reviewer_id": reviewer_id,
        "review_scope": {
            "independent_shadow_review": True,
            "blind_key_accessed": False,
            "prior_blind_reviews_accessed": False,
            "other_shadow_reviews_accessed": False,
        },
        "samples": [
            {
                "blind_id": sample.blind_id,
                "hard_obligations": [
                    {
                        "decision_id": obligation.decision_id,
                        "status": None,
                        "evidence_paragraphs": [],
                        "contradiction_paragraphs": [],
                        "violation_paragraphs": [],
                        "confidence": None,
                        "comment": "",
                    }
                    for obligation in ticket.hard_obligations
                ],
                "soft_topology": [
                    {
                        "decision_id": obligation.decision_id,
                        "status": None,
                        "evidence_paragraphs": [],
                        "violation_paragraphs": [],
                        "confidence": None,
                        "comment": "",
                    }
                    for obligation in ticket.soft_topology_obligations
                ],
                "unauthorized_content": [
                    {
                        "category": category,
                        "detected": None,
                        "paragraphs": [],
                        "description": "",
                    }
                    for category in UNAUTHORIZED_CATEGORIES
                ],
                "process_log_checks": [
                    {
                        "category": category,
                        "detected": None,
                        "paragraphs": [],
                        "description": "",
                    }
                    for category in PROCESS_CATEGORIES
                ],
                "overall_comment": "",
            }
            for sample in corpus.samples
        ],
    }


def review_instructions() -> str:
    return """# Decision Witness 独立验证说明

你正在验证一张预先冻结的 Scene Decision Ticket 是否能在六篇匿名正文中被可靠执行和定位。你不是在评选风格组，也不要猜测文本来源。

## 只能读取

- `decision-ticket-public.json`
- `shadow-corpus-public.json`
- 分配给你的 validator 模板
- 本说明

## 禁止读取

- `blind-review-key.private.json`
- `decision-ticket-provenance.private.json`
- 旧盲审、旧 aggregate、prompts、results
- 其他 validator 的结果

## 填写规则

1. 保持样本、硬义务、软义务和检查项的原始顺序。
2. 硬 presence/state_match 项只填 `present`、`absent`、`contradicted`、`unverifiable`。
3. 硬 absence/authority_check 项只填 `respected`、`violated`、`unverifiable`。
4. 软拓扑只填 `pass`、`borderline`、`fail`。
5. confidence 必须为 1–5 整数。
6. `present` 必须给 evidence_paragraphs。
7. `contradicted` 必须给 contradiction_paragraphs。
8. `violated` 与软项 `fail` 必须给 violation_paragraphs。
9. 软项 `pass` 或 `borderline` 必须给 evidence_paragraphs。
10. `unverifiable` 必须在 comment 说明原因。
11. detected=true 的未授权内容或流程问题必须给段落和说明。
12. 不复制长段正文，以 P001 等段落 ID 定位。

## 关系判断

区分三件事：

- 通过动作或对白呈现既有边界；
- 实际创造新的关系、承诺或责任变化；
- 叙述者直接解释人物关系或主题。

后两者不是同一问题，应分别记录。
"""


def _validate_paragraphs(
    paragraph_ids: list[str],
    *,
    valid_ids: set[str],
    locator: str,
    errors: list[str],
) -> None:
    unknown = set(paragraph_ids) - valid_ids
    if unknown:
        errors.append(f"{locator}: unknown paragraph IDs {sorted(unknown)}")


def validate_reviews(
    ticket: SceneDecisionTicket,
    corpus: ShadowCorpus,
    raw_reviews: list[dict[str, Any]],
    *,
    require_count: int = 3,
) -> tuple[list[ValidatorReview], dict[str, Any]]:
    if len(raw_reviews) != require_count:
        raise ValueError(
            f"expected {require_count} validator reviews, found {len(raw_reviews)}"
        )
    reviews: list[ValidatorReview] = []
    schema_errors: list[str] = []
    for index, payload in enumerate(raw_reviews, 1):
        try:
            reviews.append(ValidatorReview.model_validate(payload))
        except Exception as exc:
            schema_errors.append(f"review {index}: {exc}")
    if schema_errors:
        raise ValueError("review schema validation failed:\n- " + "\n- ".join(schema_errors))

    errors: list[str] = []
    reviewer_ids = [review.reviewer_id for review in reviews]
    if len(set(reviewer_ids)) != len(reviewer_ids):
        errors.append("reviewer IDs must be unique")
    expected_samples = [item.blind_id for item in corpus.samples]
    expected_hard = [item.decision_id for item in ticket.hard_obligations]
    expected_soft = [item.decision_id for item in ticket.soft_topology_obligations]
    modes = {
        item.decision_id: item.verification_mode
        for item in ticket.hard_obligations
    }
    corpus_map = {item.blind_id: item for item in corpus.samples}

    for review in reviews:
        sample_ids = [item.blind_id for item in review.samples]
        if sample_ids != expected_samples:
            errors.append(f"{review.reviewer_id}: sample IDs/order mismatch")
        for sample in review.samples:
            if sample.blind_id not in corpus_map:
                continue
            valid_paragraphs = {
                item.paragraph_id for item in corpus_map[sample.blind_id].paragraphs
            }
            locator = f"{review.reviewer_id}/{sample.blind_id}"
            hard_ids = [item.decision_id for item in sample.hard_obligations]
            soft_ids = [item.decision_id for item in sample.soft_topology]
            if hard_ids != expected_hard:
                errors.append(f"{locator}: hard obligation IDs/order mismatch")
            if soft_ids != expected_soft:
                errors.append(f"{locator}: soft obligation IDs/order mismatch")
            if [item.category for item in sample.unauthorized_content] != list(
                UNAUTHORIZED_CATEGORIES
            ):
                errors.append(f"{locator}: unauthorized categories/order mismatch")
            if [item.category for item in sample.process_log_checks] != list(
                PROCESS_CATEGORIES
            ):
                errors.append(f"{locator}: process categories/order mismatch")

            for row in sample.hard_obligations:
                row_locator = f"{locator}/{row.decision_id}"
                mode = modes.get(row.decision_id)
                if mode in {"presence", "state_match"}:
                    allowed = {"present", "absent", "contradicted", "unverifiable"}
                else:
                    allowed = {"respected", "violated", "unverifiable"}
                if row.status not in allowed:
                    errors.append(
                        f"{row_locator}: {row.status} invalid for mode {mode}"
                    )
                for field, values in (
                    ("evidence", row.evidence_paragraphs),
                    ("contradiction", row.contradiction_paragraphs),
                    ("violation", row.violation_paragraphs),
                ):
                    _validate_paragraphs(
                        values,
                        valid_ids=valid_paragraphs,
                        locator=f"{row_locator}/{field}",
                        errors=errors,
                    )
                if row.status == "present" and not row.evidence_paragraphs:
                    errors.append(f"{row_locator}: present requires evidence")
                if row.status == "contradicted" and not row.contradiction_paragraphs:
                    errors.append(f"{row_locator}: contradicted requires contradiction")
                if row.status == "violated" and not row.violation_paragraphs:
                    errors.append(f"{row_locator}: violated requires violation")
                if row.status == "unverifiable" and not row.comment.strip():
                    errors.append(f"{row_locator}: unverifiable requires comment")

            for row in sample.soft_topology:
                row_locator = f"{locator}/{row.decision_id}"
                _validate_paragraphs(
                    row.evidence_paragraphs,
                    valid_ids=valid_paragraphs,
                    locator=f"{row_locator}/evidence",
                    errors=errors,
                )
                _validate_paragraphs(
                    row.violation_paragraphs,
                    valid_ids=valid_paragraphs,
                    locator=f"{row_locator}/violation",
                    errors=errors,
                )
                if row.status in {"pass", "borderline"} and not row.evidence_paragraphs:
                    errors.append(f"{row_locator}: {row.status} requires evidence")
                if row.status == "fail" and not row.violation_paragraphs:
                    errors.append(f"{row_locator}: fail requires violation")

            for row in [
                *sample.unauthorized_content,
                *sample.process_log_checks,
            ]:
                row_locator = f"{locator}/{row.category}"
                _validate_paragraphs(
                    row.paragraphs,
                    valid_ids=valid_paragraphs,
                    locator=row_locator,
                    errors=errors,
                )
                if row.detected and (not row.paragraphs or not row.description.strip()):
                    errors.append(
                        f"{row_locator}: detected=true requires paragraphs and description"
                    )

    if errors:
        raise ValueError("review validation failed:\n- " + "\n- ".join(errors))
    return reviews, {
        "valid": True,
        "reviewer_ids": reviewer_ids,
        "reviewer_count": len(reviews),
        "sample_count_per_reviewer": len(expected_samples),
        "hard_obligation_count": len(expected_hard),
        "soft_obligation_count": len(expected_soft),
        "independent_shadow_review_confirmed": True,
    }

