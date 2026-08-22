"""WR3.0 offline shadow audit: WR committed chain vs legacy StateFrame V1.

Zero LLM, zero production writes.  Audits the WR chain artifacts (WR0-E gold
closed chain and C2.1-R4 canary commits) for internal consistency (ledger
replay -> after, state_frame <-> ledger, revision chain, idempotency) and
produces the legacy StateFrame V1 coverage projection baseline for the WR3.1+
downstream migration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.writing.state_frame_v1 import task_id_hash
from app.writing.world_runtime_bakery_gold import (
    build_saturday_bakery_gold_fixture,
)
from app.writing.world_runtime_checkpoint_shadow import (
    CHECKPOINT_SHADOW_KEY,
    build_shadow_payload,
    verify_shadow_payload,
)
from app.writing.world_runtime_character_projection import project_characters
from app.writing.world_runtime_handover_projection import project_handover
from app.writing.world_runtime_legacy_projection import (
    legacy_fact_mapping,
    project_state_frame,
)
from app.writing.world_runtime_metadata_projection import (
    project_rag_metadata,
    project_world_state_facts,
)
from app.writing.world_runtime_reviewer_projection import project_reviewer_context
from app.writing.world_runtime_state_committer import (
    CommittedWorldState,
    CommittableChange,
    CommittableDelta,
    CommittableValidation,
    CommittableValidationItem,
    WorldRuntimeStateCommitter,
)


ROOT = Path(__file__).resolve().parents[2]
CANARY_RUNTIME = ROOT / ".world_runtime_state_commit_canary_runtime" / "c21r4"
GOLD_OUTPUT = ROOT / "reports" / "wr3-shadow-audit-gold-2026-08-06.json"
CANARY_OUTPUT = ROOT / "reports" / "wr3-shadow-audit-c21r4-2026-08-06.json"

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def normalize_wr_facts(state) -> list[dict[str, Any]]:
    return [
        {
            "fact_id": fact.fact_id,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "epistemic_status": fact.epistemic_status,
            "revision": fact.revision,
        }
        for fact in state.facts
    ]


def legacy_coverage_projection(facts: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for fact in facts:
        mapping = legacy_fact_mapping(fact["subject"], fact["predicate"])
        rows.append({
            "fact_id": fact["fact_id"],
            "subject": fact["subject"],
            "predicate": fact["predicate"],
            "value": fact["value"],
            "mapping_status": "covered" if mapping else "no_legacy_equivalent",
            "legacy_fact_type": mapping[0] if mapping else None,
            "legacy_predicate": mapping[1] if mapping else None,
            "mapping_kind": mapping[2] if mapping else None,
        })
    return {
        "fact_count": len(rows),
        "covered_count": sum(1 for row in rows if row["mapping_status"] == "covered"),
        "no_legacy_equivalent_count": sum(
            1 for row in rows if row["mapping_status"] == "no_legacy_equivalent"
        ),
        "exact_mapping_count": sum(
            1 for row in rows if row.get("mapping_kind") == "exact"
        ),
        "approximate_mapping_count": sum(
            1 for row in rows if row.get("mapping_kind") == "approximate"
        ),
        "rows": rows,
    }


def _ledger_entries(committed) -> list[dict[str, Any]]:
    return [
        {
            "revision": entry.revision,
            "change_type": entry.change_type,
            "subject": entry.subject,
            "predicate": entry.predicate,
            "after_value": entry.after_value,
            "fact_id": entry.fact_id,
            "evidence_ids": list(entry.evidence_ids),
        }
        for entry in committed.ledger.entries
    ]


def _frame_assertions(frame) -> list[Any]:
    return [
        assertion
        for group in (
            frame.temporal_state,
            frame.location_state,
            frame.character_presence,
            frame.persistent_state,
            frame.relationship_state,
            frame.open_loops,
            frame.unknowns_and_conflicts,
        )
        for assertion in group
    ]


def _replay_ledger(before, entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    facts = {
        fact.fact_id: {
            "value": fact.value,
            "epistemic_status": fact.epistemic_status,
            "revision": fact.revision,
        }
        for fact in before.facts
    }
    for entry in entries:
        if entry["fact_id"] is None:
            continue
        facts[entry["fact_id"]] = {
            "value": entry["after_value"],
            "epistemic_status": "confirmed_true",
            "revision": entry["revision"],
        }
    return facts


def audit_committed(committed) -> dict[str, Any]:
    """Audit one committed world state for internal consistency."""
    entries = _ledger_entries(committed)
    issues: list[str] = []
    observations: list[str] = []
    replayed = _replay_ledger(committed.before, entries)
    after = {fact.fact_id: fact for fact in committed.after.facts}
    for fact_id, expected in replayed.items():
        actual = after.get(fact_id)
        if actual is None:
            issues.append(f"ledger_replay_missing_fact:{fact_id}")
            continue
        if actual.value != expected["value"]:
            issues.append(f"ledger_replay_value_mismatch:{fact_id}")
        if actual.revision != expected["revision"]:
            issues.append(f"ledger_replay_revision_mismatch:{fact_id}")
    for fact_id in after:
        if fact_id not in replayed:
            issues.append(f"after_fact_not_in_replay:{fact_id}")

    assertions = _frame_assertions(committed.state_frame)
    excluded_count = len(committed.state_frame.excluded_assertion_ids)
    if excluded_count and not assertions:
        observations.append(
            "state_frame_all_assertions_excluded: WR predicates are not in the "
            "legacy StateFrame category vocabulary"
        )
    ledger_by_key = {
        (entry.subject, entry.predicate): entry.after_value
        for entry in committed.ledger.entries
        if entry.fact_id is not None
    }
    assertion_mismatch = 0
    for assertion in assertions:
        expected_value = ledger_by_key.get((assertion.subject, assertion.predicate))
        if expected_value is None:
            assertion_mismatch += 1
            issues.append(
                f"state_frame_assertion_without_ledger:"
                f"{assertion.subject}|{assertion.predicate}"
            )
            continue
        if json.dumps(assertion.value, ensure_ascii=False, sort_keys=True) != json.dumps(
            expected_value, ensure_ascii=False, sort_keys=True
        ):
            assertion_mismatch += 1
            issues.append(
                f"state_frame_assertion_value_mismatch:"
                f"{assertion.subject}|{assertion.predicate}"
            )
    missing_evidence = [
        entry for entry in committed.ledger.entries if not entry.evidence_ids
    ]
    if missing_evidence:
        issues.append(f"ledger_entry_without_evidence:{len(missing_evidence)}")
    return {
        "commit_id": committed.commit_id,
        "revision": committed.after.revision,
        "ledger_entries": len(entries),
        "state_frame_assertions": len(assertions),
        "state_frame_excluded_assertions": excluded_count,
        "assertion_mismatch_count": assertion_mismatch,
        "missing_evidence_entries": len(missing_evidence),
        "issues": issues,
        "observations": observations,
        "consistent": not issues,
    }


def _gold_committable(gold):
    """Convert the WR0-E gold committed delta into committer inputs."""
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
        delta_id="delta:wr3-gold",
        project_id=gold.state_before.project_id,
        base_revision=gold.state_before.revision,
        output_hash=gold.output_hash,
        evidence_ids=tuple(sorted(evidence_ids)),
        changes=tuple(changes),
    )
    validation = CommittableValidation(
        validation_id="validation:wr3-gold",
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
    return delta, validation


def audit_gold() -> dict[str, Any]:
    gold = build_saturday_bakery_gold_fixture()
    delta, validation = _gold_committable(gold)
    committer = WorldRuntimeStateCommitter()
    committed = committer.commit(
        idempotency_key="wr3:gold",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    replay = committer.commit(
        idempotency_key="wr3:gold",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )
    audit = audit_committed(committed)
    legacy_frame = project_state_frame(committed, task_id="wr0e-gold")
    handover_projection = project_handover(committed)
    character_projection = project_characters(committed)
    rag_metadata = project_rag_metadata(committed)
    world_state_facts = project_world_state_facts(committed)
    reviewer_context = project_reviewer_context(committed)
    shadow_payload = build_shadow_payload(committed)
    shadow_verified, shadow_issues = verify_shadow_payload(shadow_payload)
    actual_facts = normalize_wr_facts(committed.after)
    expected_facts = normalize_wr_facts(gold.state_after)
    by_key = {(f["subject"], f["predicate"]): f for f in actual_facts}
    divergences = []
    for fact in expected_facts:
        actual = by_key.get((fact["subject"], fact["predicate"]))
        if actual is None:
            divergences.append({"kind": "expected_only", **fact})
        elif json.dumps(actual["value"], ensure_ascii=False, sort_keys=True) != json.dumps(
            fact["value"], ensure_ascii=False, sort_keys=True
        ):
            divergences.append({
                "kind": "value_mismatch",
                "subject": fact["subject"],
                "predicate": fact["predicate"],
                "expected": fact["value"],
                "actual": actual["value"],
            })
    return {
        "fixture": "wr0e-saturday-bakery",
        "audit": audit,
        "idempotent_replay": {
            "skipped_as_duplicate": replay.skipped_as_duplicate is True,
            "after_hash_matches": replay.after.artifact_hash == committed.after.artifact_hash,
        },
        "after_facts": actual_facts,
        "legacy_coverage": legacy_coverage_projection(actual_facts),
        "legacy_state_frame": {
            "frame_id": legacy_frame.frame_id,
            "frame_hash": legacy_frame.frame_hash,
            "facts_count": len(legacy_frame.facts),
            "source_manifest_count": len(legacy_frame.source_manifest),
            "frame_status": legacy_frame.frame_status,
        },
        "handover_projection": handover_projection,
        "character_projection": character_projection,
        "rag_metadata": rag_metadata,
        "world_state_facts": world_state_facts,
        "reviewer_context": reviewer_context,
        "checkpoint_shadow": {
            "key": CHECKPOINT_SHADOW_KEY,
            "payload_hash": shadow_payload["payload_hash"],
            "verified": shadow_verified,
            "issues": shadow_issues,
        },
        "gold_state_after_divergences": divergences,
    }


def audit_canary() -> dict[str, Any]:
    commits_dir = CANARY_RUNTIME / "private" / "commits"
    if not commits_dir.exists():
        raise FileNotFoundError(f"missing c21r4 commits: {commits_dir}")
    reports = []
    previous_revision = None
    for subsection in range(1, 4):
        path = commits_dir / f"S{subsection}.json"
        payload = _read_json(path)
        before_revision = payload["before"]["revision"]
        after_revision = payload["after"]["revision"]
        if previous_revision is not None and before_revision != previous_revision:
            reports.append({
                "subsection": subsection,
                "issue": f"revision_chain_break:expected_before_{previous_revision}",
            })
            continue
        ledger = payload["ledger"]["entries"]
        frame = payload.get("state_frame", {})
        committed = CommittedWorldState.model_validate(payload)
        legacy_frame = project_state_frame(
            committed,
            task_id="c21r4-saturday-bakery",
            section=1,
            subsection=subsection,
        )
        handover_projection = project_handover(committed)
        character_projection = project_characters(committed)
        rag_metadata = project_rag_metadata(committed, subsection=subsection)
        world_state_facts = project_world_state_facts(committed, subsection=subsection)
        reviewer_context = project_reviewer_context(committed)
        shadow_payload = build_shadow_payload(committed)
        shadow_verified, shadow_issues = verify_shadow_payload(shadow_payload)
        assertions = [
            assertion
            for group in (
                "temporal_state", "location_state", "character_presence",
                "persistent_state", "relationship_state", "open_loops",
                "unknowns_and_conflicts",
            )
            for assertion in frame.get(group, [])
        ]
        excluded_count = len(frame.get("excluded_assertion_ids", []))
        observations = []
        if excluded_count and not assertions:
            observations.append(
                "state_frame_all_assertions_excluded: WR predicates are not in the "
                "legacy StateFrame category vocabulary"
            )
        replayed = _replay_ledger_dict(payload["before"], ledger)
        issues = []
        after_by_id = {fact["fact_id"]: fact for fact in payload["after"]["facts"]}
        for fact_id, expected in replayed.items():
            actual = after_by_id.get(fact_id)
            if actual is None:
                issues.append(f"ledger_replay_missing_fact:{fact_id}")
            elif actual["value"] != expected["value"]:
                issues.append(f"ledger_replay_value_mismatch:{fact_id}")
        for fact_id in after_by_id:
            if fact_id not in replayed:
                issues.append(f"after_fact_not_in_replay:{fact_id}")
        ledger_by_key = {
            (entry["subject"], entry["predicate"]): entry["after_value"]
            for entry in ledger
            if entry.get("fact_id") is not None
        }
        for assertion in assertions:
            expected_value = ledger_by_key.get((assertion["subject"], assertion["predicate"]))
            if expected_value is None or json.dumps(
                assertion.get("value"), ensure_ascii=False, sort_keys=True
            ) != json.dumps(expected_value, ensure_ascii=False, sort_keys=True):
                issues.append(
                    f"state_frame_assertion_mismatch:"
                    f"{assertion['subject']}|{assertion['predicate']}"
                )
        missing_evidence = [e for e in ledger if not e.get("evidence_ids")]
        if missing_evidence:
            issues.append(f"ledger_entry_without_evidence:{len(missing_evidence)}")
        reports.append({
            "subsection": subsection,
            "before_revision": before_revision,
            "after_revision": after_revision,
            "ledger_entries": len(ledger),
            "state_frame_assertions": len(assertions),
            "state_frame_excluded_assertions": excluded_count,
            "consistent": not issues,
            "issues": issues,
            "observations": observations,
            "legacy_coverage": legacy_coverage_projection(payload["after"]["facts"]),
            "legacy_state_frame": {
                "frame_id": legacy_frame.frame_id,
                "frame_hash": legacy_frame.frame_hash,
                "facts_count": len(legacy_frame.facts),
                "source_manifest_count": len(legacy_frame.source_manifest),
            },
            "handover_projection": handover_projection,
            "character_projection": character_projection,
            "rag_metadata": rag_metadata,
            "world_state_facts": world_state_facts,
            "reviewer_context": reviewer_context,
            "checkpoint_shadow": {
                "key": CHECKPOINT_SHADOW_KEY,
                "payload_hash": shadow_payload["payload_hash"],
                "verified": shadow_verified,
                "issues": shadow_issues,
            },
        })
        previous_revision = after_revision
    return {
        "source": str(CANARY_RUNTIME),
        "subsections": reports,
        "all_consistent": all(item.get("consistent", False) for item in reports),
    }


def _replay_ledger_dict(before: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    facts = {
        fact["fact_id"]: {
            "value": fact["value"],
            "epistemic_status": fact["epistemic_status"],
            "revision": fact["revision"],
        }
        for fact in before["facts"]
    }
    for entry in entries:
        fact_id = entry.get("fact_id")
        if fact_id is None:
            continue
        facts[fact_id] = {
            "value": entry["after_value"],
            "epistemic_status": "confirmed_true",
            "revision": entry["revision"],
        }
    return facts


def main() -> None:
    parser = argparse.ArgumentParser(description="WR3.0 offline shadow audit")
    parser.add_argument("command", choices=("gold", "canary", "all"))
    args = parser.parse_args()
    results: dict[str, Any] = {}
    if args.command in ("gold", "all"):
        result = audit_gold()
        _write_json(GOLD_OUTPUT, result)
        results["gold"] = result
    if args.command in ("canary", "all"):
        result = audit_canary()
        _write_json(CANARY_OUTPUT, result)
        results["canary"] = result
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
