import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c import (
    build_messages,
    parse_semantic_response,
)


def _response(events):
    return json.dumps({"events": events}, ensure_ascii=False)


def test_prompt_contains_state_and_text_but_no_expected_answer_payload():
    text = "林晚提交终稿，页面随后显示已发布。"
    messages = build_messages(text=text, state_variant="before")

    assert len(messages) == 2
    assert text in messages[1]["content"]
    assert '"revision":7' in messages[1]["content"]
    assert "semantic_recall" not in messages[1]["content"]
    assert "expected_validation\":" not in messages[1]["content"]


def test_actual_publication_projects_with_exact_evidence_and_no_commit():
    text = "林晚提交终稿，后台审核通过，页面状态随即变成已发布。"
    artifact = parse_semantic_response(
        text=text,
        response_text=_response([{
            "change_type": "publication_state",
            "subject": "article:lin-wan",
            "predicate": "publication_state",
            "after_value": "published",
            "actor": "character:lin-wan",
            "mechanism": "submit_and_platform_publish",
            "event_id": "event:article-published",
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [{"excerpt": "林晚提交终稿，后台审核通过，页面状态随即变成已发布", "occurrence": 1}],
        }]),
        sample_id="WR2C-P-001",
        scene_id="adversarial-unpublished-knowledge",
        state_variant="before",
    )

    assert artifact.projected_event_count == 1
    assert artifact.delta.changes[0].before_value == "draft"
    assert artifact.delta.commit_sink == "forbidden"
    assert artifact.state_mutated is False
    evidence = artifact.delta.evidence[0]
    assert text[evidence.start:evidence.end] == evidence.excerpt
    assert artifact.output_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_planned_negated_and_unknown_events_are_dropped_before_delta():
    text = "她打算明天把全文发给同事，但现在没有发送。"
    events = []
    for mode, epistemic in (("planned", "asserted"), ("negated", "asserted"), ("actual", "unknown")):
        events.append({
            "change_type": "knowledge_state",
            "subject": "character:coworker",
            "predicate": "article_knowledge",
            "after_value": "perceived",
            "actor": "character:lin-wan",
            "mechanism": "missing_transmission_path",
            "event_id": None,
            "mode": mode,
            "epistemic": epistemic,
            "evidence": [{"excerpt": "她打算明天把全文发给同事", "occurrence": 1}],
        })
    artifact = parse_semantic_response(
        text=text, response_text=_response(events), sample_id="WR2C-K-NEG",
        scene_id="adversarial-unpublished-knowledge", state_variant="before",
    )

    assert artifact.raw_event_count == 3
    assert artifact.projected_event_count == 0
    assert len(artifact.dropped_events) == 3
    assert artifact.delta.changes == ()


def test_ungrounded_evidence_and_wrong_ontology_shape_fail_closed_per_event():
    text = "门仍锁着。"
    events = [
        {
            "change_type": "clock_state", "subject": "world_clock", "predicate": "time",
            "after_value": "05:10", "actor": "world_clock", "mechanism": "explicit_time_progression",
            "event_id": None, "mode": "actual", "epistemic": "asserted",
            "evidence": [{"excerpt": "墙上的钟到了五点十分", "occurrence": 1}],
        },
        {
            "change_type": "clock_state", "subject": "character:lin-wan", "predicate": "time",
            "after_value": "05:10", "actor": "world_clock", "mechanism": "explicit_time_progression",
            "event_id": None, "mode": "actual", "epistemic": "asserted",
            "evidence": [{"excerpt": "门仍锁着", "occurrence": 1}],
        },
    ]
    artifact = parse_semantic_response(
        text=text, response_text=_response(events), sample_id="WR2C-T-BAD",
        scene_id="adversarial-storefront-hours", state_variant="before",
    )

    assert artifact.delta.changes == ()
    assert {item.reason for item in artifact.dropped_events} == {"evidence_not_found", "ontology_shape_mismatch"}


def test_cross_sentence_knowledge_evidence_reaches_validator():
    text = "她把全文作为附件传进工作群。过了一会儿。小吴准确复述了第二节末句。"
    artifact = parse_semantic_response(
        text=text,
        response_text=_response([{
            "change_type": "knowledge_state", "subject": "character:coworker",
            "predicate": "article_knowledge", "after_value": "perceived",
            "actor": "character:lin-wan", "mechanism": "group_file_send_and_body_response",
            "event_id": "event:group-file-perception", "mode": "actual", "epistemic": "asserted",
            "evidence": [
                {"excerpt": "她把全文作为附件传进工作群", "occurrence": 1},
                {"excerpt": "小吴准确复述了第二节末句", "occurrence": 1},
            ],
        }]),
        sample_id="WR2C-K-001", scene_id="adversarial-unpublished-knowledge", state_variant="before",
    )
    validation = validate_delta_v2(artifact.delta)

    assert len(artifact.delta.evidence) == 2
    assert validation.accepted_change_ids == (artifact.delta.changes[0].change_id,)
    assert validation.would_commit is False


def test_illegal_actual_event_is_not_suppressed_by_semantic_parser():
    text = "四点四十，林晚收下现金，把面包递给门外来客。"
    artifact = parse_semantic_response(
        text=text,
        response_text=_response([{
            "change_type": "storefront_public_sale", "subject": "bakery:wild-bread:storefront",
            "predicate": "public_sale_event", "after_value": "occurred",
            "actor": "character:lin-wan", "mechanism": "cash_exchange", "event_id": "event:early-sale",
            "mode": "actual", "epistemic": "asserted",
            "evidence": [{"excerpt": "林晚收下现金，把面包递给门外来客", "occurrence": 1}],
        }]),
        sample_id="WR2C-S-001", scene_id="adversarial-storefront-hours", state_variant="before",
    )
    validation = validate_delta_v2(artifact.delta)

    assert artifact.projected_event_count == 1
    assert validation.rejected_change_ids == (artifact.delta.changes[0].change_id,)


def test_extra_fields_or_non_json_response_are_rejected_as_a_whole():
    with pytest.raises(ValueError, match="invalid_semantic_response"):
        parse_semantic_response(
            text="没有变化。", response_text='{"events":[],"expected_validation":"valid"}',
            sample_id="WR2C-BAD-001", scene_id="adversarial-object-and-repeat", state_variant="after_augmented",
        )
    with pytest.raises(ValueError, match="invalid_semantic_response"):
        parse_semantic_response(
            text="没有变化。", response_text='```json\n{"events":[]}\n```',
            sample_id="WR2C-BAD-002", scene_id="adversarial-object-and-repeat", state_variant="after_augmented",
        )

