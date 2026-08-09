from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.writer_boundary_v12_r3.kernel import make_assignment
from experiments.writer_boundary_v12_r33.builder import (
    R3_MATRIX,
    R3_PROTOCOL,
    R3_REQUESTS,
    ROSTER,
    _audits_from_dispatch,
    _semantic_mock,
    _votes_from_dispatches,
    build,
    materialize,
)
from experiments.writer_boundary_v12_r33.kernel import (
    build_artifact_registry,
    create_role_separated_map,
    derive_and_aggregate,
    execution_dispatch,
    preference_dispatches,
    unblind_votes,
    validate_audits,
    validate_registry_bundle,
    validate_votes,
)
from experiments.writer_boundary_v12_r33.models import (
    ExecutionAudit,
    MandatoryEventEvidence,
    PreferenceVote,
    ViolationEvidence,
)


def fixture():
    _, protocol, matrix, _ = materialize()
    assignments = make_assignment(matrix, protocol)
    by_block = {item["block_id"]: item for item in assignments["assignments"]}
    texts = {}
    for block in matrix["blocks"]:
        scene = next(item for item in protocol["scenes"] if item["scene_id"] == block["scene_id"])
        for arm in ("A", "B", "C"):
            decision = (
                scene["decision_contract"]["allowed_values"][0]["value"]
                if arm == "A"
                else by_block[block["block_id"]]["selected_value"]
            )
            texts[block["text_ids"][arm]] = _semantic_mock(scene, decision)
    registry = build_artifact_registry(texts)
    private_map = create_role_separated_map(
        matrix,
        registry,
        ROSTER["preference_reviewers"],
        entropy=b"r33-tests" * 4,
    )
    execution, _ = execution_dispatch(
        reviewer_id=ROSTER["actors"]["execution_auditor"],
        private_map=private_map,
        texts=texts,
        protocol=protocol,
    )
    preferences, _ = preference_dispatches(
        private_map=private_map,
        texts=texts,
        reviewers=ROSTER["preference_reviewers"],
    )
    return protocol, matrix, assignments, texts, registry, private_map, execution, preferences


def test_r3_sources_and_request_corpus_remain_pinned():
    before = (R3_PROTOCOL.read_bytes(), R3_MATRIX.read_bytes(), R3_REQUESTS.read_bytes())
    materialize()
    assert (R3_PROTOCOL.read_bytes(), R3_MATRIX.read_bytes(), R3_REQUESTS.read_bytes()) == before


def test_artifact_registry_is_coherent_and_reviewer_cannot_author_missing():
    _, _, _, texts, registry, _, execution, _ = fixture()
    validate_registry_bundle(registry, texts)
    changed = dict(texts)
    changed[next(iter(changed))] = b"changed"
    with pytest.raises(ValueError):
        validate_registry_bundle(registry, changed)
    payload = _audits_from_dispatch(execution)[0].model_dump(mode="json")
    payload["artifact_status"] = "missing"
    with pytest.raises(ValidationError):
        ExecutionAudit.model_validate(payload)


def test_independence_and_locked_fields_are_required_not_defaulted():
    _, _, _, _, _, _, execution, preferences = fixture()
    audit_payload = _audits_from_dispatch(execution)[0].model_dump(mode="json")
    audit_payload.pop("public_material_only")
    with pytest.raises(ValidationError):
        ExecutionAudit.model_validate(audit_payload)
    vote_payload = _votes_from_dispatches(preferences)[0].model_dump(mode="json")
    vote_payload.pop("locked")
    with pytest.raises(ValidationError):
        PreferenceVote.model_validate(vote_payload)


def test_role_public_ids_are_disjoint_and_unique():
    _, _, _, _, _, private_map, execution, preferences = fixture()
    execution_ids = {item["public_text_id"] for item in execution["items"]}
    preference_ids = {
        candidate["public_text_id"]
        for package in preferences.values()
        for block in package["blocks"]
        for candidate in (block["candidate_1"], block["candidate_2"])
    }
    assert len(execution_ids) == 36
    assert len(preference_ids) == 72
    assert not execution_ids & preference_ids
    assert len({
        mapping["public_block_id"]
        for block in private_map["blocks"]
        for mapping in block["reviewer_maps"].values()
    }) == 36


def test_audit_requires_every_m_exactly_once_and_correct_dispatch():
    _, _, _, _, _, _, execution, _ = fixture()
    audits = _audits_from_dispatch(execution)
    validate_audits(audits, package=execution)
    missing = audits[0].model_copy(update={"mandatory_events": audits[0].mandatory_events[:-1]})
    with pytest.raises(ValueError):
        validate_audits([missing, *audits[1:]], package=execution)
    wrong_dispatch = audits[0].model_copy(update={"dispatch_sha256": "0" * 64})
    with pytest.raises(ValueError):
        validate_audits([wrong_dispatch, *audits[1:]], package=execution)


def test_violation_uses_typed_f_catalog_not_m_ids():
    _, _, _, _, _, _, execution, _ = fixture()
    audit = _audits_from_dispatch(execution)[0]
    rubric = execution["rubrics"][audit.scene_id]
    allowed = set(rubric["violation_catalog"][audit.violations[0].check_id])
    wrong_f = next(
        entry["id"] for entry in rubric["forbidden_catalog"] if entry["id"] not in allowed
    )
    violation = audit.violations[0].model_copy(update={"detected": True, "f_ids": [wrong_f]})
    changed = audit.model_copy(update={"violations": [violation, *audit.violations[1:]]})
    audits = [changed, *_audits_from_dispatch(execution)[1:]]
    with pytest.raises(ValueError):
        validate_audits(audits, package=execution)


def test_votes_are_bound_to_recipient_dispatch_and_exact_roster():
    _, _, _, _, _, _, _, preferences = fixture()
    votes = _votes_from_dispatches(preferences)
    validate_votes(votes, packages=preferences)
    foreign = votes[0].model_copy(update={"reviewer_id": "FOREIGN"})
    with pytest.raises(ValueError):
        validate_votes([foreign, *votes[1:]], packages=preferences)
    changed_dispatch = votes[0].model_copy(update={"dispatch_sha256": "0" * 64})
    with pytest.raises(ValueError):
        validate_votes([changed_dispatch, *votes[1:]], packages=preferences)


def test_unblind_and_aggregate_revalidate_exact_roster_product():
    _, matrix, assignments, _, registry, private_map, execution, preferences = fixture()
    audits = _audits_from_dispatch(execution)
    votes = _votes_from_dispatches(preferences)
    normalized = unblind_votes(votes, packages=preferences, private_map=private_map)
    result = derive_and_aggregate(
        matrix=matrix,
        assignments=assignments,
        registry=registry,
        private_map=private_map,
        audits=audits,
        normalized_votes=normalized,
        reviewer_roster=ROSTER["preference_reviewers"],
    )
    assert result["aggregate"]["conclusion"] == "do_not_expand"
    duplicate = [dict(item, reviewer_id="DUPLICATE") for item in normalized]
    with pytest.raises(ValueError):
        derive_and_aggregate(
            matrix=matrix,
            assignments=assignments,
            registry=registry,
            private_map=private_map,
            audits=audits,
            normalized_votes=duplicate,
            reviewer_roster=ROSTER["preference_reviewers"],
        )


def test_semantic_mocks_support_each_m_and_each_no_violation_claim():
    _, _, _, _, _, _, execution, _ = fixture()
    audits = _audits_from_dispatch(execution)
    validate_audits(audits, package=execution)
    for audit in audits:
        rubric = execution["rubrics"][audit.scene_id]
        assert len(audit.mandatory_events) == len(rubric["mandatory_catalog"])
        assert all(item.passed for item in audit.mandatory_events)
        assert not any(item.detected for item in audit.violations)


def test_full_r33_build_is_zero_call_and_emits_four_isolated_dispatches(tmp_path: Path):
    output = tmp_path / "output"
    result = build(output, tmp_path / "report.md")
    assert result["r3_3_static_pass"] is True
    assert result["model_calls"] == result["fiction_texts"] == 0
    assert result["execution_preference_public_id_overlap"] == 0
    assert result["recipient_specific_dispatches"] == 4
    manifests = list((output / "deliveries").glob("*/manifest.json"))
    packages = list((output / "deliveries").glob("*/package.json"))
    assert len(manifests) == len(packages) == 4
    top = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert top["next_stage_authorized"] == "external_fresh_chat_r3_3_three_party_review"
    assert top["generation_package_authorized"] is False


def test_r33_has_no_real_model_client():
    root = Path(__file__).resolve().parents[2]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "experiments/writer_boundary_v12_r33/kernel.py",
            "experiments/writer_boundary_v12_r33/builder.py",
        )
    )
    assert "get_llm_client" not in source
    assert "chat_completion" not in source
