import hashlib
import json

import pytest

from experiments.world_runtime_writer_canary import delta_shadow_wr2a as wr2a


def test_gold_loads_all_eight_outputs_with_exact_spans_and_no_commit_sink():
    deltas = wr2a.load_gold_deltas()

    assert len(deltas) == 8
    assert {item.sample_id for item in deltas} == {f"WR1P-{index:02d}" for index in range(1, 9)}
    for delta in deltas:
        text = (wr2a.RUNTIME / "private/outputs" / f"{delta.sample_id}.txt").read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == delta.output_hash
        assert delta.consumer == "transition_validator_shadow"
        assert delta.commit_sink == "forbidden"
        for evidence in delta.evidence:
            assert text[evidence.start:evidence.end] == evidence.excerpt


def test_shadow_validator_matches_gold_partitions_without_state_mutation():
    result = wr2a.run_shadow()

    assert result["sample_count"] == 8
    assert result["samples_with_changes"] == 6
    assert result["proposed_change_count"] == 8
    assert result["valid_change_count"] == 2
    assert result["invalid_change_count"] == 3
    assert result["unresolved_change_count"] == 3
    assert result["gold_validation_mismatches"] == []
    assert result["output_hash_binding_complete"] is True
    assert result["evidence_span_binding_complete"] is True
    assert result["state_mutations"] == 0
    assert result["commits"] == 0
    assert result["model_calls"] == 0
    assert result["next_gate"] == "automatic_extractor_shadow_not_started"
    for validation in result["validations"]:
        assert validation["would_commit"] is False
        assert validation["state_mutated"] is False


def test_storefront_sale_is_rejected_even_when_writer_received_runtime_frame():
    deltas = {item.sample_id: item for item in wr2a.load_gold_deltas()}
    validation = wr2a.validate_delta(deltas["WR1P-02"])

    assert validation.rejected_change_ids == ("change:02:public-sale",)
    assert "bakery.explicit.storefront-schedule" in validation.items[0].rule_ids


def test_explicit_group_transfer_is_valid_but_named_character_stays_unresolved():
    deltas = {item.sample_id: item for item in wr2a.load_gold_deltas()}
    validation = wr2a.validate_delta(deltas["WR1P-03"])

    assert validation.accepted_change_ids == ("change:03:coworker-knowledge",)
    assert validation.unresolved_change_ids == ("change:03:named-coworker",)
    assert validation.would_commit is False


def test_base_revision_drift_fails_closed():
    delta = wr2a.load_gold_deltas()[0]
    payload = delta.model_dump()
    payload["base_revision"] = delta.base_revision + 1
    drifted = wr2a.ProposedTypedDelta(**payload)

    with pytest.raises(ValueError, match="base revision mismatch"):
        wr2a.validate_delta(drifted)


def test_gold_fixture_declares_manual_source_not_automatic_extraction():
    payload = json.loads(wr2a.GOLD.read_text(encoding="utf-8"))
    assert payload["status"] == "manual_evidence_gold_not_extractor_output"
