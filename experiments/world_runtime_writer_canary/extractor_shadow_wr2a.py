"""WR2-A deterministic typed-delta extractor, isolated from state commit.

The extractor is deliberately narrow: it recognizes the transition families in
the Saturday Bakery canary and emits evidence-bound ProposedTypedDelta values.
It never reads expected validation outcomes and it never mutates canonical
state.  Evaluation code is kept below the extraction boundary and uses a
pre-existing WR1E holdout text partition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary import delta_shadow_wr2a as wr2a


ROOT = Path(__file__).resolve().parents[2]
HOLDOUT_SOURCE = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr1e_evaluator_holdout_v1.json"
HOLDOUT_EXPECTED = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2a_extractor_holdout_expected_v1.json"
DEFAULT_REPORT = ROOT / "reports/world-runtime-wr2a-extractor-shadow-result-2026-08-04.json"
EXTRACTOR_VERSION = "world-runtime-bakery-delta-extractor-wr2a-v1"

_ENTITY_IDS = {
    "陈屿": "character:chen-yu",
    "陈姐": "character:chen-jie",
    "陈晨": "character:chen-chen",
    "小周": "character:xiao-zhou",
}
_KNOWN_PROJECT_NAMES = {"林晚", "周野", "季晴"}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _find_span(text: str, patterns: Iterable[str], *, start: int = 0) -> tuple[int, int, str] | None:
    candidates: list[tuple[int, int, str]] = []
    for pattern in patterns:
        match = re.search(pattern, text[start:], flags=re.DOTALL)
        if match:
            span_start = start + match.start()
            span_end = start + match.end()
            candidates.append((span_start, span_end, text[span_start:span_end]))
    return min(candidates, key=lambda item: (item[0], item[1])) if candidates else None


class _DeltaBuilder:
    def __init__(self, *, text: str, sample_id: str, scene_id: str, state_variant: str, base_revision: int):
        self.text = text
        self.sample_id = sample_id
        self.scene_id = scene_id
        self.state_variant = state_variant
        self.base_revision = base_revision
        self.evidence: list[wr2a.EvidenceSpan] = []
        self.changes: list[wr2a.ProposedChange] = []

    def add_evidence(self, label: str, claim: str, span: tuple[int, int, str]) -> str:
        evidence_id = f"ev:auto:{self.sample_id.lower()}:{label}"
        start, end, excerpt = span
        self.evidence.append(
            wr2a.EvidenceSpan(
                evidence_id=evidence_id,
                claim=claim,
                start=start,
                end=end,
                excerpt=excerpt,
            )
        )
        return evidence_id

    def add_change(self, label: str, evidence_ids: tuple[str, ...], **values: Any) -> None:
        self.changes.append(
            wr2a.ProposedChange(
                change_id=f"change:auto:{self.sample_id.lower()}:{label}",
                sequence=len(self.changes) + 1,
                evidence_ids=evidence_ids,
                **values,
            )
        )

    def build(self) -> wr2a.ProposedTypedDelta:
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return wr2a.ProposedTypedDelta(
            delta_id=f"delta:auto:{self.sample_id.lower()}",
            sample_id=self.sample_id,
            scene_id=self.scene_id,
            project_id="project:saturday-bakery",
            state_variant=self.state_variant,
            base_revision=self.base_revision,
            output_hash=digest,
            evidence=tuple(self.evidence),
            changes=tuple(self.changes),
        )


def _extract_public_sale(builder: _DeltaBuilder) -> None:
    text = builder.text
    goods = _find_span(
        text,
        (
            r"(?:把|将)[^。！？\n]{0,18}(?:可颂|面包|纸袋)[^。！？\n]{0,12}(?:递出|递出去|递给|放在门外)",
            r"(?:递出|递出去|递给)[^。！？\n]{0,12}(?:可颂|面包|纸袋)",
        ),
    )
    money = _find_span(
        text,
        (
            r"(?:接过|收下)[^。！？\n]{0,12}(?:纸币|现金|钱)",
            r"(?:把|将)?(?:那几张)?(?:纸币|现金|钱)[^。！？\n]{0,12}(?:放进|放到|留下)",
            r"(?:纸币|现金|钱)[^。！？\n]{0,12}(?:被留下|留下)[^。！？\n]{0,8}(?:收银台|收银盒)?",
        ),
    )
    if not goods or not money:
        return
    if any(marker in money[2] for marker in ("退回", "推回", "没接", "不接")):
        return
    goods_id = builder.add_evidence("sale-goods", "goods pass to a public visitor", goods)
    money_id = builder.add_evidence("sale-money", "payment is retained", money)
    builder.add_change(
        "public-sale",
        (goods_id, money_id),
        change_type="storefront_public_sale",
        subject="bakery:wild-bread:storefront",
        predicate="public_sale_event",
        before_value=None,
        before_epistemic_status="unknown",
        after_value="occurred",
        actor="character:lin-wan",
        mechanism="cash_exchange",
        event_id="event:visitor-purchase",
    )


def _extract_knowledge(builder: _DeltaBuilder) -> None:
    text = builder.text
    transfer = _find_span(
        text,
        (
            r"(?:把|将)[^。！？\n]{0,18}(?:正文|草稿|全文|一段|其中一段)[^。！？\n]{0,16}(?:发到|贴到|粘贴到|发送到)[^。！？\n]{0,8}(?:工作群|公司群|群里)",
            r"(?:选中|复制)[^。！？\n]{0,24}(?:粘贴到|发到|发送到)[^。！？\n]{0,8}(?:工作群|公司群|群里)",
        ),
    )
    response = _find_span(
        text,
        (
            r"(?:同事|陈屿|陈姐)[^。！？\n]{0,18}(?:回复|回应|指出|说)[^。！？\n]{0,35}(?:第[一二三四五六七八九十0-9]+段|人物|细节|错字|结尾|阿姨)",
            r"(?:这个|那个)[^。！？\n]{0,12}(?:细节|人物|结尾)[^。！？\n]{0,24}(?:很好|真实|自然|不自然)",
            r"(?:第[一二三四五六七八九十0-9]+段|第[一二三四五六七八九十0-9]+个)[^。！？\n]{0,30}(?:人物|反应|细节|错字|注意)",
        ),
        start=transfer[1] if transfer else 0,
    )
    if transfer and response and "没有人回应正文" not in response[2]:
        transfer_id = builder.add_evidence("knowledge-transfer", "article body is sent to the work group", transfer)
        response_id = builder.add_evidence("knowledge-response", "coworker responds to body content", response)
        builder.add_change(
            "coworker-knowledge",
            (transfer_id, response_id),
            change_type="knowledge_state",
            subject="character:coworker",
            predicate="article_knowledge",
            before_value=None,
            before_epistemic_status="unknown",
            after_value="perceived",
            actor="character:lin-wan",
            mechanism="explicit_group_send_and_body_response",
            event_id="event:group-text-transfer",
        )
        return

    unsupported = _find_span(
        text,
        (
            r"(?:那篇文章|这篇文章|文章正文|正文|文章)[^。！？\n]{0,18}(?:我看了|我读了|看完了|读完了|很真实|写得很好)",
            r"(?:工作群)?同事[^。！？\n]{0,18}说[^。！？\n]{0,25}(?:文章里|正文里|那个阿姨)",
        ),
    )
    if unsupported and not transfer and not any(marker in unsupported[2] for marker in ("标题", "截图", "发我看看", "没看")):
        evidence_id = builder.add_evidence("knowledge-without-path", "coworker claims body perception without a shown path", unsupported)
        subject = "character:zhou-ye" if "周野" in text[max(0, unsupported[0] - 24):unsupported[1]] else "character:coworker"
        builder.add_change(
            "knowledge-without-path",
            (evidence_id,),
            change_type="knowledge_state",
            subject=subject,
            predicate="article_knowledge",
            before_value=None,
            before_epistemic_status="unknown",
            after_value="perceived",
            actor=subject,
            mechanism="missing_transmission_path",
            event_id="event:article-perception-claim",
        )


def _extract_acknowledgement(builder: _DeltaBuilder) -> None:
    span = _find_span(
        builder.text,
        (r"系统自动回复[^。！？\n]{0,36}(?:辞职信|辞职通知)[^。！？\n]{0,24}(?:进入人事流程|已收悉|收到)",),
    )
    if not span:
        return
    evidence_id = builder.add_evidence("resignation-ack", "company system acknowledges resignation receipt", span)
    builder.add_change(
        "resignation-ack",
        (evidence_id,),
        change_type="resignation_acknowledgement",
        subject="company:lin-wan",
        predicate="resignation_acknowledged",
        before_value=None,
        before_epistemic_status="unknown",
        after_value=True,
        actor="company:hr-system",
        mechanism="institutional_reply",
        event_id="event:resignation-acknowledged",
    )


def _extract_object_and_repeat(builder: _DeltaBuilder) -> None:
    repeated = _find_span(builder.text, (r"重新发布文章", r"再次发布文章", r"又发布(?:了)?文章"))
    if repeated:
        evidence_id = builder.add_evidence("repeated-publication", "completed publication event is repeated", repeated)
        builder.add_change(
            "repeated-publication",
            (evidence_id,),
            change_type="repeated_completed_event",
            subject="article:lin-wan",
            predicate="publication_event",
            before_value="completed",
            before_epistemic_status="confirmed_true",
            after_value="repeated",
            actor="character:lin-wan",
            mechanism="explicit_repeat_marker",
            event_id="event:article-published",
        )
    empty = _find_span(
        builder.text,
        (r"(?:无人回家|住处无人)[^。！？\n]{0,30}(?:汤没了|只剩空碗|碗空了)",),
    )
    if empty:
        evidence_id = builder.add_evidence("object-empty-without-actor", "bowl becomes empty while home is unoccupied", empty)
        builder.add_change(
            "bowl-empty",
            (evidence_id,),
            change_type="object_state",
            subject="object:green-bean-soup-bowl",
            predicate="content_state",
            before_value="contains_cold_soup",
            before_epistemic_status="confirmed_true",
            after_value="empty",
            actor="unknown",
            mechanism="missing_actor_or_event",
            event_id=None,
        )


def _extract_employment(builder: _DeltaBuilder) -> None:
    span = _find_span(
        builder.text,
        (r"(?:公司|人事)[^。！？\n]{0,18}(?:尚未确认|没有确认|没确认)[^。！？\n]{0,24}辞职[^。！？\n]{0,12}(?:已经生效|已生效)",),
    )
    if not span:
        return
    evidence_id = builder.add_evidence("employment-ended-without-ack", "employment is treated as ended before acknowledgement", span)
    builder.add_change(
        "employment-ended",
        (evidence_id,),
        change_type="employment_state",
        subject="employment:lin-wan",
        predicate="status",
        before_value="employed",
        before_epistemic_status="confirmed_true",
        after_value="ended",
        actor="character:lin-wan",
        mechanism="self_assumed_effective",
        event_id="event:employment-ended",
    )


def _extract_unsourced_facts(builder: _DeltaBuilder) -> None:
    text = builder.text
    candidates: list[tuple[int, int, str, str, str]] = []
    name_patterns = (
        r"(?P<prefix>是同事|同事|行政部的|隔壁组的)(?P<name>陈屿|陈姐|陈晨|小周)(?=[，：、“\s也发在])",
    )
    for pattern in name_patterns:
        for match in re.finditer(pattern, text):
            name = match.group("name")
            if name not in _KNOWN_PROJECT_NAMES:
                role = "administration_coworker" if match.group("prefix") == "行政部的" else "company_coworker"
                candidates.append((match.start(), match.end(), match.group(0), _ENTITY_IDS[name], role))
    owner = re.search(r"柜台后的老板娘", text)
    if owner:
        candidates.append((owner.start(), owner.end(), owner.group(0), "character:bakery-owner", "bakery_owner"))
    supervisor = re.search(r"抄送主管", text)
    if supervisor:
        candidates.append((supervisor.start(), supervisor.end(), supervisor.group(0), "company:lin-wan:supervisor", "resignation_cc_recipient"))
    for index, (start, end, excerpt, subject, after_value) in enumerate(sorted(candidates), 1):
        evidence_id = builder.add_evidence(
            f"unsourced-{index}",
            "persistent project fact is asserted without canonical source",
            (start, end, excerpt),
        )
        predicate = "communication_recipient" if after_value == "resignation_cc_recipient" else "identity_role"
        builder.add_change(
            f"unsourced-{index}",
            (evidence_id,),
            change_type="unsourced_project_fact",
            subject=subject,
            predicate=predicate,
            before_value=None,
            before_epistemic_status="unknown",
            after_value=after_value,
            actor="narrator",
            mechanism="text_assertion",
            event_id=None,
        )


def extract_typed_delta(
    *,
    text: str,
    sample_id: str,
    scene_id: str,
    state_variant: str,
    base_revision: int | None = None,
) -> wr2a.ProposedTypedDelta:
    """Extract a proposed delta without reading gold labels or committing state."""

    _, states, _ = wr1r._artifacts()
    state = states[state_variant]
    revision = state.revision if base_revision is None else base_revision
    builder = _DeltaBuilder(
        text=text,
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
        base_revision=revision,
    )
    if scene_id == "adversarial-storefront-hours":
        _extract_public_sale(builder)
    elif scene_id == "adversarial-unpublished-knowledge":
        _extract_knowledge(builder)
    elif scene_id == "adversarial-object-and-repeat":
        _extract_acknowledgement(builder)
        _extract_object_and_repeat(builder)
        _extract_knowledge(builder)
    elif scene_id == "adversarial-employment-transition":
        _extract_employment(builder)
    _extract_unsourced_facts(builder)
    return builder.build()


def _signature(change: wr2a.ProposedChange | dict[str, Any]) -> tuple[str, str, str, str, str]:
    if isinstance(change, wr2a.ProposedChange):
        payload = change.model_dump(mode="json")
    else:
        payload = change
    return (
        payload["change_type"],
        payload["subject"],
        payload["predicate"],
        json.dumps(payload.get("after_value"), ensure_ascii=False, sort_keys=True),
        payload["mechanism"],
    )


def _score(extracted: dict[str, wr2a.ProposedTypedDelta], expected: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    expected_total = extracted_total = matched_total = invalid_expected = invalid_matched = 0
    empty_expected = empty_correct = 0
    unsupported_accepted = []
    sample_results = []
    for sample_id, delta in extracted.items():
        expected_changes = expected[sample_id]
        expected_signatures = [_signature(item) for item in expected_changes]
        actual_signatures = [_signature(item) for item in delta.changes]
        remaining = list(actual_signatures)
        matched = []
        for index, signature in enumerate(expected_signatures):
            if signature in remaining:
                remaining.remove(signature)
                matched.append(index)
        validation = wr2a.validate_delta(delta)
        outcomes = {item.change_id: item.outcome for item in validation.items}
        for change in delta.changes:
            if _signature(change) in remaining and outcomes[change.change_id] == "valid":
                unsupported_accepted.append(change.change_id)
                remaining.remove(_signature(change))
        invalid_indices = [index for index, item in enumerate(expected_changes) if item.get("expected_validation") == "invalid"]
        expected_total += len(expected_changes)
        extracted_total += len(delta.changes)
        matched_total += len(matched)
        invalid_expected += len(invalid_indices)
        invalid_matched += len(set(matched) & set(invalid_indices))
        if not expected_changes:
            empty_expected += 1
            empty_correct += int(not delta.changes)
        sample_results.append(
            {
                "sample_id": sample_id,
                "expected_change_count": len(expected_changes),
                "extracted_change_count": len(delta.changes),
                "matched_change_count": len(matched),
                "validation_outcomes": outcomes,
            }
        )
    precision = matched_total / extracted_total if extracted_total else 1.0
    recall = matched_total / expected_total if expected_total else 1.0
    invalid_recall = invalid_matched / invalid_expected if invalid_expected else 1.0
    return {
        "expected_change_count": expected_total,
        "extracted_change_count": extracted_total,
        "matched_change_count": matched_total,
        "semantic_precision": precision,
        "semantic_recall": recall,
        "invalid_transition_recall": invalid_recall,
        "empty_delta_cases": empty_expected,
        "empty_delta_correct": empty_correct,
        "unsupported_accepted_change_ids": unsupported_accepted,
        "samples": sample_results,
    }


def _calibration_batch() -> tuple[dict[str, wr2a.ProposedTypedDelta], dict[str, list[dict[str, Any]]]]:
    gold = wr2a.load_gold_deltas()
    extracted = {}
    expected = {}
    for delta in gold:
        text = (wr2a.RUNTIME / "private/outputs" / f"{delta.sample_id}.txt").read_text(encoding="utf-8")
        extracted[delta.sample_id] = extract_typed_delta(
            text=text,
            sample_id=delta.sample_id,
            scene_id=delta.scene_id,
            state_variant=delta.state_variant,
            base_revision=delta.base_revision,
        )
        expected[delta.sample_id] = [change.model_dump(mode="json") for change in delta.changes]
    return extracted, expected


def _holdout_batch() -> tuple[dict[str, wr2a.ProposedTypedDelta], dict[str, list[dict[str, Any]]]]:
    source = _read(HOLDOUT_SOURCE)
    expected_payload = _read(HOLDOUT_EXPECTED)
    source_hash = hashlib.sha256(HOLDOUT_SOURCE.read_bytes()).hexdigest()
    if source_hash != expected_payload["source_fixture_sha256"]:
        raise ValueError("WR2-A extractor holdout source hash mismatch")
    expected_by_id = {item["case_id"]: item for item in expected_payload["cases"]}
    extracted = {}
    expected = {}
    _, states, _ = wr1r._artifacts()
    for case in source["cases"]:
        contract = expected_by_id[case["case_id"]]
        state_variant = contract["state_variant"]
        extracted[case["case_id"]] = extract_typed_delta(
            text=case["text"],
            sample_id=case["case_id"],
            scene_id=case["scene_id"],
            state_variant=state_variant,
            base_revision=states[state_variant].revision,
        )
        expected[case["case_id"]] = contract["changes"]
    if set(extracted) != set(expected_by_id):
        raise ValueError("WR2-A extractor holdout case set mismatch")
    return extracted, expected


def run_extractor_shadow(output_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    calibration_deltas, calibration_expected = _calibration_batch()
    holdout_deltas, holdout_expected = _holdout_batch()
    calibration = _score(calibration_deltas, calibration_expected)
    holdout = _score(holdout_deltas, holdout_expected)
    gates = {
        "visible_calibration_semantics_complete": calibration["semantic_precision"] == 1.0 and calibration["semantic_recall"] == 1.0,
        "holdout_semantic_precision_at_least_0_90": holdout["semantic_precision"] >= 0.90,
        "holdout_semantic_recall_at_least_0_90": holdout["semantic_recall"] >= 0.90,
        "holdout_invalid_transition_recall_complete": holdout["invalid_transition_recall"] == 1.0,
        "holdout_empty_deltas_preserved": holdout["empty_delta_correct"] == holdout["empty_delta_cases"],
        "unsupported_accepted_changes_forbidden": not holdout["unsupported_accepted_change_ids"],
        "state_commit_forbidden": True,
    }
    passed = all(gates.values())
    calibration_texts = {
        sample_id: (wr2a.RUNTIME / "private/outputs" / f"{sample_id}.txt").read_text(encoding="utf-8")
        for sample_id in calibration_deltas
    }
    holdout_texts = {item["case_id"]: item["text"] for item in _read(HOLDOUT_SOURCE)["cases"]}
    all_texts = {**calibration_texts, **holdout_texts}
    all_deltas = {**calibration_deltas, **holdout_deltas}
    traceability_complete = all(
        delta.output_hash == hashlib.sha256(all_texts[sample_id].encode("utf-8")).hexdigest()
        and all(all_texts[sample_id][evidence.start:evidence.end] == evidence.excerpt for evidence in delta.evidence)
        for sample_id, delta in all_deltas.items()
    )
    result = {
        "schema_version": "world-runtime-delta-extractor-shadow-audit-wr2a-v1",
        "status": "automatic_extractor_shadow_passed" if passed else "automatic_extractor_shadow_failed",
        "extractor_version": EXTRACTOR_VERSION,
        "extractor_scope": "saturday_bakery_canary_transition_families_only",
        "calibration_partition": "wr1p_manual_evidence_gold_visible",
        "holdout_partition": "preexisting_wr1e_holdout_texts_not_blind_to_current_implementation",
        "holdout_source_sha256": hashlib.sha256(HOLDOUT_SOURCE.read_bytes()).hexdigest(),
        "holdout_expectations_sha256": hashlib.sha256(HOLDOUT_EXPECTED.read_bytes()).hexdigest(),
        "calibration": calibration,
        "holdout": holdout,
        "gates": gates,
        "extractor_gate_passed": passed,
        "evidence_span_binding_complete": traceability_complete,
        "state_mutations": 0,
        "commits": 0,
        "model_calls": 0,
        "production_writer_changed": False,
        "production_promotion_eligible": False,
        "next_gate": "broader_independent_adversarial_extractor_partition",
        "extracted_calibration_deltas": [item.model_dump(mode="json") for item in calibration_deltas.values()],
        "extracted_holdout_deltas": [item.model_dump(mode="json") for item in holdout_deltas.values()],
    }
    _write(output_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = run_extractor_shadow(args.output)
    print(json.dumps({
        "status": result["status"],
        "calibration": {key: result["calibration"][key] for key in ("semantic_precision", "semantic_recall", "invalid_transition_recall")},
        "holdout": {key: result["holdout"][key] for key in ("semantic_precision", "semantic_recall", "invalid_transition_recall", "empty_delta_correct")},
        "gates": result["gates"],
        "state_mutations": result["state_mutations"],
        "commits": result["commits"],
        "model_calls": result["model_calls"],
        "next_gate": result["next_gate"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
