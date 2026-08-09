from __future__ import annotations

import json
from pathlib import Path

from experiments.writer_boundary_v12_design.builder import (
    AUDIT_ONLY_FIELDS,
    DESIGN_PATH,
    boundary_maker_snapshot,
    build,
    make_matrix,
    read_design,
    realizer_snapshot,
    w0_snapshot,
)


def design():
    return read_design()


def test_design_is_disabled_and_contains_four_distinct_structural_axes():
    value = design()
    assert value.enabled is False
    assert value.model_calls_allowed is False
    assert len(value.scenes) == 4
    assert len({scene.structural_axis for scene in value.scenes}) == 4


def test_each_scene_has_balanced_shape_and_two_option_contract():
    for scene in design().scenes:
        assert len(scene.characters) == 2
        assert len(scene.mandatory_events) == 6
        assert len(scene.forbidden_events) == 8
        assert len(scene.decision_contract.allowed_values) == 2
        assert (
            scene.decision_contract.solution_boundary_policy
            .confirmed_new_solution_min_signals
            == 2
        )


def test_w0_and_maker_share_exact_contract_and_hash_for_every_scene_repeat():
    for scene in design().scenes:
        for repeat in range(1, 4):
            w0 = w0_snapshot(scene, repeat)
            maker = boundary_maker_snapshot(scene, repeat)
            assert (
                w0["payload"]["shared_decision_contract"]
                == maker["payload"]["shared_decision_contract"]
            )
            assert (
                w0["payload"]["decision_contract_hash"]
                == maker["payload"]["decision_contract_hash"]
            )


def test_boundary_maker_declares_exact_scalar_output_contract():
    for scene in design().scenes:
        maker = boundary_maker_snapshot(scene, 1)
        assert '"selected_temporary_solution"' in maker["instruction"]
        assert "字符串" in maker["instruction"]
        assert "禁止输出数组" in maker["instruction"]
        assert len(maker["legal_outputs"]) == 2
        assert all(
            set(item) == {"selected_temporary_solution"}
            and isinstance(item["selected_temporary_solution"], str)
            for item in maker["legal_outputs"]
        )


def test_realizer_sees_selected_definition_but_not_unselected_value_or_contract():
    for scene in design().scenes:
        options = scene.decision_contract.allowed_values
        for index in (0, 1):
            snapshot = realizer_snapshot(scene, 1, index)
            rendered = json.dumps(snapshot, ensure_ascii=False)
            assert options[index].definition in rendered
            assert options[1 - index].value not in rendered
            assert "shared_decision_contract" not in rendered


def test_execution_audit_fields_are_not_in_writer_snapshots():
    for scene in design().scenes:
        snapshots = [
            w0_snapshot(scene, 1),
            realizer_snapshot(scene, 1, 0),
            realizer_snapshot(scene, 1, 1),
        ]
        for snapshot in snapshots:
            rendered = json.dumps(snapshot, ensure_ascii=False)
            assert all(field not in rendered for field in AUDIT_ONLY_FIELDS)


def test_matrix_has_24_unique_anonymous_rows_and_balanced_pairs():
    private_rows, public_rows = make_matrix(design())
    assert len(private_rows) == len(public_rows) == 24
    assert len({row["text_id"] for row in private_rows}) == 24
    assert {row["text_id"] for row in private_rows} == {
        row["text_id"] for row in public_rows
    }
    for scene_id in ("SC5", "SC6", "SC7", "SC8"):
        for repeat in (1, 2, 3):
            rows = [
                row
                for row in private_rows
                if row["scene_id"] == scene_id and row["repeat"] == repeat
            ]
            assert {row["route"] for row in rows} == {"W0", "W1"}


def test_public_shell_has_no_route_identity_or_fiction(tmp_path: Path):
    build(tmp_path)
    public = json.loads(
        (tmp_path / "review/reviewer-public-shell.json").read_text(encoding="utf-8")
    )
    assert public["route_identity_exposed"] is False
    assert public["contains_fiction"] is False
    assert all("route" not in row for row in public["rows"])
    assert all(row["text_placeholder"] is None for row in public["rows"])
    schema = json.loads(
        (tmp_path / "review/execution-audit-schema.json").read_text(encoding="utf-8")
    )
    assert schema["additionalProperties"] is False
    assert "hard_checks" in schema["properties"]
    assert "execution_audit" in schema["properties"]


def test_primary_pairing_excludes_solution_mismatch_from_preference_denominator(
    tmp_path: Path,
):
    build(tmp_path)
    policy = json.loads(
        (tmp_path / "review/pairing-policy.json").read_text(encoding="utf-8")
    )
    assert policy["primary_pair_rule"] == "same_scene_repeat_and_observed_solution"
    assert (
        policy["unmatched_pair_policy"]
        == "retain_as_route_diagnostic_exclude_from_preference_denominator"
    )


def test_build_is_static_and_passes_design_audit(tmp_path: Path):
    audit = build(tmp_path)
    assert audit["design_audit_pass"] is True
    assert audit["model_calls"] == 0
    assert audit["fiction_generated"] is False
    assert audit["blind_reviews_created"] is False
    assert audit["planned_text_count"] == 24
    manifest = json.loads(
        (tmp_path / "design-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["generation_authorized"] is False
    assert manifest["next_stage_authorized"] == "independent_design_review"


def test_build_preserves_design_fixture_and_writes_no_historical_targets(
    tmp_path: Path,
):
    before = DESIGN_PATH.read_bytes()
    audit = build(tmp_path)
    assert DESIGN_PATH.read_bytes() == before
    assert audit["input_integrity"]["unchanged"] is True
    assert audit["historical_v1_1_write_targets"] == []
    assert audit["preflight_layer_write_targets"] == []


def test_builder_has_no_model_client_or_generation_call():
    source = (
        Path(__file__).resolve().parents[2]
        / "experiments/writer_boundary_v12_design/builder.py"
    ).read_text(encoding="utf-8")
    assert "get_llm_client" not in source
    assert "chat_completion" not in source
    assert "generate_text" not in source
