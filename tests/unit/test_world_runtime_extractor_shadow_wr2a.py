import hashlib
import inspect
import json

from experiments.world_runtime_writer_canary import delta_shadow_wr2a as wr2a
from experiments.world_runtime_writer_canary import extractor_shadow_wr2a as extractor


def test_extractor_boundary_does_not_read_gold_or_commit(monkeypatch):
    monkeypatch.setattr(wr2a, "load_gold_deltas", lambda: (_ for _ in ()).throw(AssertionError("gold read")))
    source = inspect.getsource(extractor.extract_typed_delta)

    delta = extractor.extract_typed_delta(
        text="林晚走进操作间。钱被留下放到收银台，周野递出一袋面包，此时才四点四十。",
        sample_id="BOUNDARY-S01",
        scene_id="adversarial-storefront-hours",
        state_variant="before",
    )

    assert "load_gold" not in source
    assert delta.commit_sink == "forbidden"
    assert delta.consumer == "transition_validator_shadow"
    assert len(delta.changes) == 1


def test_preexisting_holdout_source_is_hash_locked():
    expected = json.loads(extractor.HOLDOUT_EXPECTED.read_text(encoding="utf-8"))
    assert hashlib.sha256(extractor.HOLDOUT_SOURCE.read_bytes()).hexdigest() == expected["source_fixture_sha256"]
    assert expected["status"] == "preexisting_text_partition_semantic_expectations"


def test_automatic_extractor_matches_visible_calibration_and_holdout_contracts():
    result = extractor.run_extractor_shadow()

    assert result["calibration"]["semantic_precision"] == 1.0
    assert result["calibration"]["semantic_recall"] == 1.0
    assert result["calibration"]["invalid_transition_recall"] == 1.0
    assert result["holdout"]["semantic_precision"] == 1.0
    assert result["holdout"]["semantic_recall"] == 1.0
    assert result["holdout"]["invalid_transition_recall"] == 1.0
    assert result["holdout"]["empty_delta_correct"] == result["holdout"]["empty_delta_cases"] == 4
    assert result["holdout"]["unsupported_accepted_change_ids"] == []
    assert result["extractor_gate_passed"] is True


def test_all_extracted_evidence_is_exact_and_no_sink_is_writable():
    calibration, _ = extractor._calibration_batch()
    holdout, _ = extractor._holdout_batch()
    holdout_texts = {
        item["case_id"]: item["text"]
        for item in json.loads(extractor.HOLDOUT_SOURCE.read_text(encoding="utf-8"))["cases"]
    }
    for sample_id, delta in calibration.items():
        text = (wr2a.RUNTIME / "private/outputs" / f"{sample_id}.txt").read_text(encoding="utf-8")
        assert delta.output_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert all(text[item.start:item.end] == item.excerpt for item in delta.evidence)
        assert delta.commit_sink == "forbidden"
    for sample_id, delta in holdout.items():
        text = holdout_texts[sample_id]
        assert delta.output_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert all(text[item.start:item.end] == item.excerpt for item in delta.evidence)
        assert delta.commit_sink == "forbidden"


def test_holdout_invalid_families_reach_validator_and_remain_uncommitted():
    deltas, _ = extractor._holdout_batch()
    invalid_samples = {
        "HLD-S02": "storefront_public_sale",
        "HLD-K02": "knowledge_state",
        "HLD-O02": "object_state",
        "HLD-E02": "employment_state",
    }
    for sample_id, change_type in invalid_samples.items():
        delta = deltas[sample_id]
        validation = wr2a.validate_delta(delta)
        assert any(item.change_type == change_type for item in delta.changes)
        assert validation.rejected_change_ids
        assert validation.would_commit is False
        assert validation.state_mutated is False


def test_quoted_coworker_premise_does_not_become_employment_state():
    delta = extractor.extract_typed_delta(
        text="同事问：你都离职了，今天还来吗？林晚说人事尚未确认，她照常去公司。",
        sample_id="NEGATIVE-E01",
        scene_id="adversarial-employment-transition",
        state_variant="after",
    )

    assert not any(item.change_type == "employment_state" for item in delta.changes)

