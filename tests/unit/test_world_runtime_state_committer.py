import pytest
from pydantic import ValidationError

from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_contracts import CanonicalWorldState, WorldFact
from app.writing.world_runtime_state_committer import (
    CommittableChange,
    CommittableDelta,
    CommittableValidation,
    CommittableValidationItem,
    WorldRuntimeStateCommitter,
)


def _gold_inputs():
    gold = build_saturday_bakery_gold_fixture()
    by_fact = {change.fact_id: change for change in gold.committed_delta.changes}
    before_by_key = {
        (fact.subject, fact.predicate): fact
        for fact in gold.state_before.facts
    }
    spec = [
        ("article-publish", "fact:article:status", "publication_state", "submit_and_platform_publish"),
        ("jiqing-link-perceived", "fact:jiqing:article-knowledge", "knowledge_state", "private_link_send_and_body_response"),
        ("resignation-delivered", "fact:resignation:state", "resignation_delivery", "institutional_email_delivery"),
    ]
    changes = []
    evidence_ids = set()
    for sequence, (change_id, fact_id, change_type, mechanism) in enumerate(spec, 1):
        raw = by_fact[fact_id]
        prior = before_by_key[(raw.subject, raw.predicate)]
        evidence_ids.update(raw.evidence_ids)
        changes.append(CommittableChange(
            change_id=change_id,
            sequence=sequence,
            change_type=change_type,
            subject=raw.subject,
            predicate=raw.predicate,
            before_value=prior.value,
            before_epistemic_status=prior.epistemic_status,
            after_value=raw.after_value,
            actor=raw.actor,
            mechanism=mechanism,
            evidence_ids=raw.evidence_ids,
        ))
    delta = CommittableDelta(
        delta_id="delta:gold-c0",
        project_id=gold.state_before.project_id,
        base_revision=gold.state_before.revision,
        output_hash=gold.output_hash,
        evidence_ids=tuple(sorted(evidence_ids)),
        changes=tuple(changes),
    )
    validation = CommittableValidation(
        validation_id="validation:gold-c0",
        delta_id=delta.delta_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=tuple(
            CommittableValidationItem(
                change_id=change.change_id,
                outcome="valid",
                evidence_ids=change.evidence_ids,
            )
            for change in changes
        ),
        accepted_change_ids=tuple(change.change_id for change in changes),
    )
    return gold, delta, validation


def test_gold_fixture_reproduces_covered_state_facts():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    result = committer.commit(
        idempotency_key="c0:gold:1",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )

    assert result.after.revision == gold.state_after.revision == 8
    after_by_fact = {fact.fact_id: fact for fact in result.after.facts}
    gold_after = {fact.fact_id: fact for fact in gold.state_after.facts}
    for fact_id in ("fact:article:status", "fact:jiqing:article-knowledge", "fact:resignation:state"):
        actual = after_by_fact[fact_id]
        expected = gold_after[fact_id]
        assert (actual.value, actual.epistemic_status, actual.revision) == (
            expected.value, expected.epistemic_status, expected.revision,
        )
        assert actual.provenance.source_type == "accepted_state_delta"
        assert actual.provenance.source_id.startswith(result.commit_id)
    for fact_id, fact in after_by_fact.items():
        if fact_id in {"fact:article:status", "fact:jiqing:article-knowledge", "fact:resignation:state"}:
            continue
        # fact:bakery:workshop-access is outside the WR2-C4 ontology (documented C0 scope).
        if fact_id == "fact:bakery:workshop-access":
            continue
        assert fact.value == next(item.value for item in gold.state_before.facts if item.fact_id == fact_id)

    assert len(result.ledger.entries) == 3
    assert all(entry.output_hash == gold.output_hash for entry in result.ledger.entries)
    assert all(entry.idempotency_key == "c0:gold:1" for entry in result.ledger.entries)
    assert len(result.state_frame.excluded_assertion_ids) == 3


def test_idempotent_replay_returns_same_artifact_without_second_effect():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    first = committer.commit(
        idempotency_key="c0:gold:replay",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    second = committer.commit(
        idempotency_key="c0:gold:replay",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    assert second.skipped_as_duplicate is True
    assert second.after.artifact_hash == first.after.artifact_hash
    assert second.ledger.artifact_hash == first.ledger.artifact_hash
    assert second.state_frame.frame_hash == first.state_frame.frame_hash
    assert second.after.revision == 8
    assert len(second.ledger.entries) == 3


def test_revision_mismatch_is_rejected():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    bad_validation = validation.model_copy(update={"base_revision": delta.base_revision + 1})
    with pytest.raises(ValueError, match="base revision mismatch"):
        committer.commit(
            idempotency_key="c0:bad-rev",
            before=gold.state_before,
            delta=delta,
            validation=bad_validation,
            final_text_hash=gold.output_hash,
        )


def test_output_hash_mismatch_is_rejected():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    with pytest.raises(ValueError, match="output hash mismatch"):
        committer.commit(
            idempotency_key="c0:bad-hash",
            before=gold.state_before,
            delta=delta,
            validation=validation,
            final_text_hash="0" * 64,
        )


def test_partition_mismatch_is_rejected_by_contract():
    gold, delta, validation = _gold_inputs()
    first = delta.changes[0].change_id
    with pytest.raises(ValidationError):
        CommittableValidation(
            validation_id=validation.validation_id,
            delta_id=validation.delta_id,
            base_revision=validation.base_revision,
            output_hash=validation.output_hash,
            items=tuple(
                CommittableValidationItem(
                    change_id=item.change_id,
                    outcome="invalid" if item.change_id == first else "valid",
                    rule_ids=("rule:x",) if item.change_id == first else (),
                    evidence_ids=item.evidence_ids,
                )
                for item in validation.items
            ),
            accepted_change_ids=tuple(item.change_id for item in validation.items),
            rejected_change_ids=(first,),
        )


def test_unknown_evidence_is_rejected():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    change = delta.changes[0]
    broken = change.model_copy(
        update={"evidence_ids": (*change.evidence_ids, "ev:unknown")}
    )
    broken_delta = delta.model_copy(update={
        "changes": tuple((broken if item.change_id == change.change_id else item) for item in delta.changes),
    })
    with pytest.raises(ValueError, match="references unknown evidence"):
        committer.commit(
            idempotency_key="c0:bad-evidence",
            before=gold.state_before,
            delta=broken_delta,
            validation=validation,
            final_text_hash=gold.output_hash,
        )


def test_before_value_mismatch_is_rejected():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    change = delta.changes[0]
    broken = change.model_copy(
        update={"before_value": "never-was"}
    )
    broken_delta = delta.model_copy(update={
        "changes": tuple((broken if item.change_id == change.change_id else item) for item in delta.changes),
    })
    with pytest.raises(ValueError, match="before value mismatch"):
        committer.commit(
            idempotency_key="c0:bad-before",
            before=gold.state_before,
            delta=broken_delta,
            validation=validation,
            final_text_hash=gold.output_hash,
        )


def test_unresolvable_fact_is_rejected_without_whitelist():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    change = delta.changes[0]
    broken = change.model_copy(
        update={
            "subject": "character:unknown-person",
            "predicate": "unknown_predicate",
        }
    )
    broken_delta = delta.model_copy(update={
        "changes": tuple((broken if item.change_id == change.change_id else item) for item in delta.changes),
    })
    with pytest.raises(ValueError, match="not in creatable whitelist"):
        committer.commit(
            idempotency_key="c0:no-fact",
            before=gold.state_before,
            delta=broken_delta,
            validation=validation,
            final_text_hash=gold.output_hash,
        )


def test_empty_accepted_set_is_rejected():
    gold, delta, validation = _gold_inputs()
    committer = WorldRuntimeStateCommitter()
    empty = CommittableValidation(
        validation_id=validation.validation_id,
        delta_id=validation.delta_id,
        base_revision=validation.base_revision,
        output_hash=validation.output_hash,
        items=tuple(
            CommittableValidationItem(
                change_id=item.change_id,
                outcome="invalid",
                rule_ids=("rule:x",),
                evidence_ids=item.evidence_ids,
            )
            for item in validation.items
        ),
        accepted_change_ids=(),
        rejected_change_ids=tuple(item.change_id for item in validation.items),
    )
    with pytest.raises(ValueError, match="at least one accepted change"):
        committer.commit(
            idempotency_key="c0:empty",
            before=gold.state_before,
            delta=delta,
            validation=empty,
            final_text_hash=gold.output_hash,
        )


def test_partition_overlap_is_rejected_by_contract():
    gold, delta, validation = _gold_inputs()
    first = delta.changes[0].change_id
    with pytest.raises(ValidationError):
        CommittableValidation(
            validation_id=validation.validation_id,
            delta_id=validation.delta_id,
            base_revision=validation.base_revision,
            output_hash=validation.output_hash,
            items=tuple(
                CommittableValidationItem(
                    change_id=item.change_id,
                    outcome="valid",
                    evidence_ids=item.evidence_ids,
                )
                for item in validation.items
            ),
            accepted_change_ids=(first,),
            rejected_change_ids=(first,),
        )


def test_event_only_change_writes_ledger_without_fact_projection():
    gold, _, _ = _gold_inputs()
    change = CommittableChange(
        change_id="change:sale:1",
        sequence=1,
        change_type="storefront_public_sale",
        subject="bakery:wild-bread:storefront",
        predicate="public_sale_event",
        before_value=None,
        before_epistemic_status="unknown",
        after_value="occurred",
        actor="customer",
        mechanism="digital_payment_exchange",
        evidence_ids=("ev:text:sale",),
    )
    delta = CommittableDelta(
        delta_id="delta:event-only",
        project_id=gold.state_before.project_id,
        base_revision=gold.state_before.revision,
        output_hash=gold.output_hash,
        evidence_ids=("ev:text:sale",),
        changes=(change,),
    )
    validation = CommittableValidation(
        validation_id="validation:event-only",
        delta_id=delta.delta_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=(CommittableValidationItem(
            change_id=change.change_id,
            outcome="valid",
            rule_ids=("rule:x",),
            evidence_ids=change.evidence_ids,
        ),),
        accepted_change_ids=(change.change_id,),
    )
    committer = WorldRuntimeStateCommitter()
    result = committer.commit(
        idempotency_key="c0:event-only",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    assert result.after.revision == gold.state_before.revision + 1
    assert {
        fact.fact_id: fact.value
        for fact in result.after.facts
    } == {
        fact.fact_id: fact.value
        for fact in gold.state_before.facts
    }
    assert len(result.ledger.entries) == 1
    assert result.ledger.entries[0].change_type == "storefront_public_sale"
    assert result.ledger.entries[0].after_value == "occurred"


def _single_change(gold, change, *, rule_id="rule:c2"):
    delta = CommittableDelta(
        delta_id="delta:c2",
        project_id=gold.state_before.project_id,
        base_revision=gold.state_before.revision,
        output_hash=gold.output_hash,
        evidence_ids=change.evidence_ids,
        changes=(change,),
    )
    validation = CommittableValidation(
        validation_id="validation:c2",
        delta_id=delta.delta_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=(CommittableValidationItem(
            change_id=change.change_id,
            outcome="valid",
            rule_ids=(rule_id,),
            evidence_ids=change.evidence_ids,
        ),),
        accepted_change_ids=(change.change_id,),
    )
    return delta, validation


def _new_fact_change(**updates):
    value = {
        "change_id": "change:c2:1",
        "sequence": 1,
        "change_type": "resignation_personal_record",
        "subject": "resignation:lin-wan",
        "predicate": "personal_record_state",
        "before_value": None,
        "before_epistemic_status": "unknown",
        "after_value": "saved",
        "actor": "character:lin-wan",
        "mechanism": "private_email_copy",
        "evidence_ids": ("ev:c2",),
    }
    value.update(updates)
    return CommittableChange(**value)


def test_creates_personal_record_fact_deterministically():
    gold, _, _ = _gold_inputs()
    change = _new_fact_change()
    delta, validation = _single_change(gold, change)
    committer = WorldRuntimeStateCommitter()
    result = committer.commit(
        idempotency_key="c2:personal-record",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    fact = next(item for item in result.after.facts if item.fact_id == "fact:resignation-lin-wan:personal_record_state")
    assert (fact.value, fact.authority, fact.revision) == ("saved", "text_extracted", 8)
    assert fact.provenance.source_id.startswith(result.commit_id)
    assert result.ledger.entries[0].fact_id == fact.fact_id


def test_creates_character_location_fact():
    gold, _, _ = _gold_inputs()
    change = _new_fact_change(
        change_type="location_state",
        subject="character:lin-wan",
        predicate="location",
        after_value="bakery:wild-bread:workshop",
        mechanism="explicit_entry",
    )
    delta, validation = _single_change(gold, change)
    committer = WorldRuntimeStateCommitter()
    result = committer.commit(
        idempotency_key="c2:location",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    fact = next(item for item in result.after.facts if item.fact_id == "fact:character-lin-wan:location")
    assert fact.value == "bakery:wild-bread:workshop"


def test_creates_object_content_fact_when_slot_missing():
    gold, _, _ = _gold_inputs()
    change = _new_fact_change(
        change_type="object_state",
        subject="object:green-bean-soup-bowl",
        predicate="content_state",
        after_value="empty",
        mechanism="actor_pours_out",
    )
    delta, validation = _single_change(gold, change)
    committer = WorldRuntimeStateCommitter()
    result = committer.commit(
        idempotency_key="c2:object",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    fact = next(item for item in result.after.facts if item.fact_id == "fact:object-green-bean-soup-bowl:content_state")
    assert fact.value == "empty"


def test_whitelist_rejects_non_creatable_accepted_change():
    gold, _, _ = _gold_inputs()
    change = _new_fact_change(
        change_type="unsourced_project_fact",
        subject="character:zhao-min",
        predicate="identity_role",
        after_value="shop_manager",
        mechanism="text_assertion",
    )
    delta, validation = _single_change(gold, change)
    committer = WorldRuntimeStateCommitter()
    with pytest.raises(ValueError, match="not in creatable whitelist"):
        committer.commit(
            idempotency_key="c2:no-whitelist",
            before=gold.state_before,
            delta=delta,
            validation=validation,
            final_text_hash=gold.output_hash,
        )


def test_multiple_fact_candidates_are_rejected():
    gold, _, _ = _gold_inputs()
    base = next(item for item in gold.state_before.facts if item.fact_id == "fact:article:status")
    duplicate = WorldFact(
        fact_id="fact:article:status-dup",
        subject=base.subject,
        predicate=base.predicate,
        value=base.value,
        epistemic_status=base.epistemic_status,
        authority=base.authority,
        provenance=base.provenance,
        revision=base.revision,
    )
    ambiguous = CanonicalWorldState(
        project_id=gold.state_before.project_id,
        revision=gold.state_before.revision,
        facts=gold.state_before.facts + (duplicate,),
    )
    change = _new_fact_change(
        change_type="publication_state",
        subject="article:lin-wan",
        predicate="publication_state",
        before_value="draft",
        before_epistemic_status="confirmed_true",
        after_value="published",
        mechanism="submit_and_platform_publish",
    )
    delta, validation = _single_change(gold, change)
    committer = WorldRuntimeStateCommitter()
    with pytest.raises(ValueError, match="multiple facts"):
        committer.commit(
            idempotency_key="c2:ambiguous",
            before=ambiguous,
            delta=delta,
            validation=validation,
            final_text_hash=gold.output_hash,
        )


def test_same_commit_creates_then_updates_new_slot():
    gold, _, _ = _gold_inputs()
    first = _new_fact_change(
        change_id="change:c2:1",
        sequence=1,
        change_type="object_state",
        subject="object:green-bean-soup-bowl",
        predicate="content_state",
        after_value="empty",
        mechanism="actor_pours_out",
    )
    second = CommittableChange(
        change_id="change:c2:2",
        sequence=2,
        change_type="object_state",
        subject="object:green-bean-soup-bowl",
        predicate="content_state",
        before_value="empty",
        before_epistemic_status="confirmed_true",
        after_value="clean",
        actor="character:lin-wan",
        mechanism="actor_pours_out",
        evidence_ids=("ev:c2",),
    )
    delta = CommittableDelta(
        delta_id="delta:c2-chain",
        project_id=gold.state_before.project_id,
        base_revision=gold.state_before.revision,
        output_hash=gold.output_hash,
        evidence_ids=("ev:c2",),
        changes=(first, second),
    )
    validation = CommittableValidation(
        validation_id="validation:c2-chain",
        delta_id=delta.delta_id,
        base_revision=delta.base_revision,
        output_hash=delta.output_hash,
        items=tuple(
            CommittableValidationItem(
                change_id=change.change_id,
                outcome="valid",
                rule_ids=("rule:c2",),
                evidence_ids=("ev:c2",),
            )
            for change in (first, second)
        ),
        accepted_change_ids=(first.change_id, second.change_id),
    )
    committer = WorldRuntimeStateCommitter()
    result = committer.commit(
        idempotency_key="c2:chain",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    fact = next(item for item in result.after.facts if item.fact_id == "fact:object-green-bean-soup-bowl:content_state")
    assert fact.value == "clean"
    assert len(result.ledger.entries) == 2
    assert all(entry.fact_id == fact.fact_id for entry in result.ledger.entries)
