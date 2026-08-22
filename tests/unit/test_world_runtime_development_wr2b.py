import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import delta_shadow_wr2b as delta_v2
from experiments.world_runtime_writer_canary import development_wr2b as development
from experiments.world_runtime_writer_canary import extractor_adversarial_wr2a as frozen_wr2a
from experiments.world_runtime_writer_canary import layered_extractor_wr2b as layered


def test_wr2a_locked_sources_remain_unchanged_after_v2_is_added():
    preflight = frozen_wr2a.verify_lock()
    assert preflight["ready"] is True
    assert preflight["hashes_matched"] is True


def test_wr2b_sources_are_frozen_before_holdout_authoring():
    lock_path = development.ROOT / "experiments/world_runtime_writer_canary/fixtures/wr2b_v2_source_lock_v1.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    assert lock["status"] == "implementation_frozen_before_unseen_holdout_authoring"
    assert lock["holdout"]["created"] is False
    assert lock["holdout"]["read_by_implementer"] is False
    for item in lock["sources"].values():
        assert hashlib.sha256((development.ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]


def test_v2_ontology_adds_all_adversarial_gap_types_without_commit_sink():
    assert {
        "storefront_public_handoff",
        "publication_state",
        "resignation_delivery",
        "resignation_personal_record",
        "clock_state",
        "location_state",
    }.issubset(set(delta_v2.ChangeTypeV2.__args__))

    delta, _, _ = layered.extract_typed_delta_v2(
        text="他们继续揉面，直到墙上的钟越过五点十分。",
        sample_id="ONTOLOGY-T01",
        scene_id="adversarial-storefront-hours",
        state_variant="before",
    )
    assert delta.commit_sink == "forbidden"
    assert delta.consumer == "transition_validator_shadow_v2"
    assert delta.changes[0].change_type == "clock_state"


def test_planned_and_negated_events_do_not_project_to_state_changes():
    text = "来客把钞票伸进门缝，林晚原样推了回去。她打算六点后再把面包卖给他。"
    delta, clauses, candidates = layered.extract_typed_delta_v2(
        text=text,
        sample_id="MODALITY-S01",
        scene_id="adversarial-storefront-hours",
        state_variant="before",
    )

    assert len(clauses) == 2
    assert any(item.polarity == "negated" for item in candidates)
    assert any(item.modality == "planned" for item in candidates)
    assert delta.changes == ()


def test_cross_sentence_transmission_and_perception_project_together():
    text = "她把全文作为文件丢进工作群。过了一会儿，同事发来语音，准确复述了第二节的最后一句。"
    delta, _, candidates = layered.extract_typed_delta_v2(
        text=text,
        sample_id="CROSS-K01",
        scene_id="adversarial-unpublished-knowledge",
        state_variant="before",
    )
    validation = delta_v2.validate_delta_v2(delta)

    assert {item.event_type for item in candidates} == {"body_transmission", "body_perception"}
    assert len(delta.changes) == 1
    assert delta.changes[0].mechanism == "group_file_send_and_body_response"
    assert validation.accepted_change_ids == (delta.changes[0].change_id,)


def test_acknowledgement_can_satisfy_later_employment_change_in_same_delta():
    text = "人事发来正式确认：辞职今天生效。林晚完成交接后，劳动关系于下午结束。"
    delta, _, _ = layered.extract_typed_delta_v2(
        text=text,
        sample_id="SEQUENCE-E01",
        scene_id="adversarial-employment-transition",
        state_variant="after",
    )
    validation = delta_v2.validate_delta_v2(delta)

    assert [item.change_type for item in delta.changes] == ["resignation_acknowledgement", "employment_state"]
    assert validation.rejected_change_ids == ()
    assert validation.accepted_change_ids == tuple(item.change_id for item in delta.changes)
    assert validation.would_commit is False


def test_v2_base_revision_drift_fails_closed():
    delta, _, _ = layered.extract_typed_delta_v2(
        text="林晚提交终稿，后台审核通过，页面状态随即变成已发布。",
        sample_id="REVISION-P01",
        scene_id="adversarial-unpublished-knowledge",
        state_variant="before",
    )
    drifted = delta_v2.ProposedTypedDeltaV2(**{**delta.model_dump(), "base_revision": delta.base_revision + 1})

    with pytest.raises(ValueError, match="base revision mismatch"):
        delta_v2.validate_delta_v2(drifted)


def test_visible_development_fit_is_complete_but_never_promotion_evidence():
    result = development.run()

    assert result["partition_role"] == "visible_development_not_holdout"
    assert result["expected_change_count"] == result["extracted_change_count"] == result["matched_change_count"] == 20
    assert result["semantic_precision"] == result["semantic_recall"] == 1.0
    assert result["invalid_transition_recall"] == 1.0
    assert result["empty_delta_correct"] == result["empty_delta_cases"] == 8
    assert result["development_gate_passed"] is True
    assert result["production_promotion_eligible"] is False
    assert result["next_gate"] == "sealed_unseen_holdout_not_created"
    assert result["state_mutations"] == result["commits"] == result["model_calls"] == 0


def test_development_evidence_and_output_hashes_are_exact():
    fixture = json.loads(development.DEVELOPMENT_FIXTURE.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        delta, _, _ = layered.extract_typed_delta_v2(
            text=case["text"], sample_id=case["case_id"], scene_id=case["scene_id"],
            state_variant=case["state_variant"],
        )
        assert delta.output_hash == hashlib.sha256(case["text"].encode("utf-8")).hexdigest()
        assert all(case["text"][item.start:item.end] == item.excerpt for item in delta.evidence)
