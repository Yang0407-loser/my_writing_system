from pathlib import Path

import pytest
from pydantic import ValidationError

from app.writing.world_runtime_contracts import (
    CandidatePack,
    ProjectWorldConstitution,
    ProvenanceRef,
    RuleScope,
    WorldRule,
    canonical_hash,
)
from app.writing.world_runtime_kernel import (
    REQUIRED_KERNEL_RULE_IDS,
    build_minimal_universal_kernel,
)
from app.writing.world_runtime_pack_modern_urban import (
    MODERN_URBAN_CN_2020S_LIFECYCLE_IDS,
    MODERN_URBAN_CN_2020S_PACK_ID,
    MODERN_URBAN_CN_2020S_PACK_REF,
    MODERN_URBAN_CN_2020S_PACK_VERSION,
    MODERN_URBAN_CN_2020S_RULE_IDS,
    build_modern_urban_cn_2020s_candidate_pack,
)
from app.writing.world_runtime_resolver import UserOverrideSet, WorldRuntimeResolver


ROOT = Path(__file__).resolve().parents[2]


def test_pack_identity_and_content_ids_are_frozen():
    pack = build_modern_urban_cn_2020s_candidate_pack()

    assert pack.pack_id == MODERN_URBAN_CN_2020S_PACK_ID
    assert pack.version == MODERN_URBAN_CN_2020S_PACK_VERSION
    assert f"{pack.pack_id}@{pack.version}" == MODERN_URBAN_CN_2020S_PACK_REF
    assert tuple(rule.rule_id for rule in pack.rules) == MODERN_URBAN_CN_2020S_RULE_IDS
    assert (
        tuple(lifecycle.lifecycle_id for lifecycle in pack.lifecycles)
        == MODERN_URBAN_CN_2020S_LIFECYCLE_IDS
    )


def test_every_pack_item_is_inactive_with_an_explicit_activation_recommendation():
    pack = build_modern_urban_cn_2020s_candidate_pack()

    for item in (*pack.rules, *pack.lifecycles):
        assert item.authority == "pack_candidate"
        assert item.enforcement == "inactive"
        assert item.activation_enforcement in {"block", "warn", "suggest"}


def test_candidate_contract_rejects_missing_activation_recommendation():
    candidate = build_modern_urban_cn_2020s_candidate_pack().rules[0]
    payload = candidate.model_dump()
    payload["activation_enforcement"] = None

    with pytest.raises(ValidationError, match="activation enforcement recommendation"):
        WorldRule(**payload)


def test_pack_is_thin_and_does_not_smuggle_project_or_unselected_domains():
    pack = build_modern_urban_cn_2020s_candidate_pack()
    body = pack.model_dump_json().lower()

    # These belong to project facts or future packs, not this thin candidate set.
    forbidden_terms = {
        "bakery",
        "linwan",
        "zhouye",
        "saturday",
        "06:00",
        "traffic",
        "finance",
        "medical",
        "education",
        "court",
        "cultivation",
        "magic",
        "teleport",
    }
    assert not {term for term in forbidden_terms if term in body}
    assert len(pack.rules) == 7
    assert len(pack.lifecycles) == 4
    assert pack.narrative_preferences == ()


def test_lifecycle_shapes_preserve_the_required_intermediate_states():
    pack = build_modern_urban_cn_2020s_candidate_pack()
    by_id = {item.lifecycle_id: item for item in pack.lifecycles}

    assert by_id["modern-urban.lifecycle.storefront-operation"].states == (
        "closed",
        "internal_activity",
        "open_to_public",
    )
    assert by_id["modern-urban.lifecycle.publication"].states == (
        "draft",
        "submitted",
        "published",
        "distributed",
    )
    assert by_id["modern-urban.lifecycle.knowledge-transmission"].states == (
        "unknown",
        "available",
        "reached",
        "perceived",
        "understood",
    )
    assert by_id["modern-urban.lifecycle.resignation"].states == (
        "private_draft",
        "delivered",
        "acknowledged",
        "notice_period",
        "terminated",
    )
    assert all(
        transition.guards and transition.effects
        for lifecycle in pack.lifecycles
        for transition in lifecycle.transitions
    )


def test_binding_pack_keeps_candidates_out_of_active_resolution():
    pack = build_modern_urban_cn_2020s_candidate_pack()
    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            bound_candidate_packs=(MODERN_URBAN_CN_2020S_PACK_REF,),
        ),
        candidate_packs=(pack,),
        kernel=build_minimal_universal_kernel(),
    )

    assert {rule.rule_id for rule in result.active_rules} == set(
        REQUIRED_KERNEL_RULE_IDS
    )
    assert result.active_lifecycles == ()
    assert {rule.rule_id for rule in result.inactive_candidate_rules} == set(
        MODERN_URBAN_CN_2020S_RULE_IDS
    )
    assert {
        lifecycle.lifecycle_id
        for lifecycle in result.inactive_candidate_lifecycles
    } == set(MODERN_URBAN_CN_2020S_LIFECYCLE_IDS)
    assert not result.conflict_report.has_blocking


def test_explicit_confirmation_of_one_candidate_activates_only_that_rule():
    pack = build_modern_urban_cn_2020s_candidate_pack()
    candidate = next(
        rule
        for rule in pack.rules
        if rule.rule_id
        == "modern-urban.publication.public-visibility-requires-publication"
    )
    confirmation = {
        "candidate_rule_id": candidate.rule_id,
        "candidate_hash": canonical_hash(candidate),
        "project_id": "project-1",
        "decision": "confirmed",
    }
    payload = candidate.model_dump()
    payload.update(
        rule_id="project-1.confirmed.public-visibility-requires-publication",
        authority="user_override",
        enforcement=candidate.activation_enforcement,
        activation_enforcement=None,
        scope=RuleScope(project_id="project-1"),
        provenance=ProvenanceRef(
            source_id="confirmation:project-1:publication-visibility",
            source_type="user_confirmation",
            source_hash=canonical_hash(confirmation),
            producer="wr0d_confirmation_fixture",
        ),
        version="1",
    )
    confirmed = WorldRule(**payload)

    result = WorldRuntimeResolver().resolve(
        constitution=ProjectWorldConstitution(
            project_id="project-1",
            version="1",
            bound_candidate_packs=(MODERN_URBAN_CN_2020S_PACK_REF,),
        ),
        candidate_packs=(pack,),
        kernel=build_minimal_universal_kernel(),
        user_overrides=UserOverrideSet(
            project_id="project-1",
            version="1",
            rules=(confirmed,),
        ),
    )

    active_non_kernel = [
        rule for rule in result.active_rules if rule.authority != "kernel"
    ]
    assert active_non_kernel == [confirmed]
    assert len(result.inactive_candidate_rules) == len(pack.rules)


def test_pack_hash_is_deterministic_and_order_insensitive():
    pack = build_modern_urban_cn_2020s_candidate_pack()
    reordered = CandidatePack(
        pack_id=pack.pack_id,
        version=pack.version,
        rules=tuple(reversed(pack.rules)),
        lifecycles=tuple(reversed(pack.lifecycles)),
    )

    assert build_modern_urban_cn_2020s_candidate_pack().artifact_hash == pack.artifact_hash
    assert reordered.artifact_hash == pack.artifact_hash


def test_pack_has_no_production_writer_or_package_facade_import():
    writing_init = (ROOT / "app" / "writing" / "__init__.py").read_text(
        encoding="utf-8"
    )
    writer = (ROOT / "app" / "agents" / "writer.py").read_text(encoding="utf-8")

    assert "world_runtime_pack_modern_urban" not in writing_init
    assert "world_runtime_pack_modern_urban" not in writer
