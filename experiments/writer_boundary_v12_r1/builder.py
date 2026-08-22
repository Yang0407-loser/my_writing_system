from __future__ import annotations

import argparse
import copy
import json
import random
from pathlib import Path
from typing import Any

from .models import (
    R1PostWriteReview,
    StateRecord,
    WorkflowState,
)
from .prompts import arm_a_request, arm_b_request, arm_c_request
from .runtime import (
    STATE_ORDER,
    append_state,
    build_ticket,
    canonical_hash,
    file_hash,
    load_protocol,
    verify_state_chain,
    verify_ticket_consumption,
)


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (
    ROOT / "experiments/writer_boundary_v12_r1/fixtures/v1_2_r1_protocol.json"
)
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r1"
AUDIT_ONLY_FIELDS = {
    "reviewer_id",
    "audited_at",
    "route_identity_accessed",
    "preference_votes_accessed",
    "failed_event_ids",
    "unauthorized_solution_candidates",
    "observed_decision",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_matrix(protocol) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    triplets = []
    text_identity = []
    raw_text_ids = [f"T{i:03d}" for i in range(1, 37)]
    random.Random(protocol.base_seed).shuffle(raw_text_ids)
    text_index = 0
    for scene_index, scene in enumerate(protocol.scenes):
        for repeat in range(1, protocol.repeats_per_scene + 1):
            triplet_id = f"TRIPLET-{len(triplets) + 1:02d}"
            paired_seed = protocol.base_seed + (scene_index + 1) * 100 + repeat
            arm_ids = {}
            for arm in protocol.arms:
                text_id = raw_text_ids[text_index]
                text_index += 1
                arm_ids[arm] = text_id
                text_identity.append(
                    {
                        "text_id": text_id,
                        "triplet_id": triplet_id,
                        "scene_id": scene.scene_id,
                        "repeat": repeat,
                        "arm": arm,
                        "paired_seed": paired_seed,
                    }
                )
            triplets.append(
                {
                    "triplet_id": triplet_id,
                    "scene_id": scene.scene_id,
                    "repeat": repeat,
                    "paired_seed": paired_seed,
                    "arm_text_ids": arm_ids,
                    "anchor_policy": "A audit locks the whitelist decision consumed by B and C",
                }
            )
    reserve_seeds = {
        scene.scene_id: protocol.base_seed + (index + 1) * 100 + 99
        for index, scene in enumerate(protocol.scenes)
    }
    return triplets, [
        *text_identity,
        {
            "reserve_seeds": reserve_seeds,
            "silent_rerun_allowed": False,
            "reserve_use_requires_versioned_failure_record": True,
        },
    ]


def mock_hash(label: str) -> str:
    return canonical_hash({"mock_only": True, "label": label})


def mock_state_chain(triplet_id: str) -> list[StateRecord]:
    records: list[StateRecord] = []
    for index, state in enumerate(STATE_ORDER):
        input_hashes = (
            [records[-1].output_hash] if records else [mock_hash("r1-design")]
        )
        append_state(
            records,
            triplet_id=triplet_id,
            state=state,
            actor_id=f"mock-actor-{index + 1:02d}",
            input_hashes=input_hashes,
            output_hash=mock_hash(f"{triplet_id}:{state.value}"),
        )
    verify_state_chain(records)
    return records


def build(
    output_dir: Path = DEFAULT_OUTPUT,
    protocol_path: Path = PROTOCOL_PATH,
) -> dict[str, Any]:
    before_hash = file_hash(protocol_path)
    protocol = load_protocol(protocol_path)
    triplets, identity_rows = build_matrix(protocol)
    scene_by_id = {scene.scene_id: scene for scene in protocol.scenes}

    requests = []
    tickets = []
    binding_checks = []
    for triplet in triplets:
        scene = scene_by_id[triplet["scene_id"]]
        repeat = triplet["repeat"]
        selected = scene.decision_contract.allowed_values[(repeat - 1) % 2].value
        source_text_hash = mock_hash(f"{triplet['triplet_id']}:A-text")
        source_audit_hash = mock_hash(f"{triplet['triplet_id']}:A-audit")
        ticket, ticket_hash = build_ticket(
            protocol=protocol,
            triplet_id=triplet["triplet_id"],
            scene_id=scene.scene_id,
            selected_value=selected,
            source_a_text_sha256=source_text_hash,
            source_a_audit_sha256=source_audit_hash,
        )
        verify_ticket_consumption(ticket, ticket_hash, scene)
        a = arm_a_request(protocol, scene, triplet["paired_seed"])
        b = arm_b_request(
            protocol, scene, triplet["paired_seed"], ticket, ticket_hash
        )
        c = arm_c_request(
            protocol, scene, triplet["paired_seed"], ticket, ticket_hash
        )
        requests.append(
            {
                "triplet_id": triplet["triplet_id"],
                "scene_id": scene.scene_id,
                "repeat": repeat,
                "paired_seed": triplet["paired_seed"],
                "A": a,
                "B": b,
                "C": c,
            }
        )
        tickets.append(
            {
                "mock_only": True,
                "ticket": ticket.model_dump(mode="json"),
                "ticket_hash": ticket_hash,
            }
        )
        a_message = json.dumps(a["messages"], ensure_ascii=False)
        b_message = json.dumps(b["messages"], ensure_ascii=False)
        c_message = json.dumps(c["messages"], ensure_ascii=False)
        binding_checks.append(
            {
                "triplet_id": triplet["triplet_id"],
                "same_seed_all_arms": len(
                    {
                        a["request_config"]["seed"],
                        b["request_config"]["seed"],
                        c["request_config"]["seed"],
                    }
                )
                == 1,
                "same_model_config_all_arms": all(
                    {
                        key: request["request_config"][key]
                        for key in request["request_config"]
                        if key != "seed"
                    }
                    == {
                        key: a["request_config"][key]
                        for key in a["request_config"]
                        if key != "seed"
                    }
                    for request in (b, c)
                ),
                "a_b_contract_identical": (
                    a["messages"][0]["content"]["input"]["shared_decision_contract"]
                    == b["messages"][0]["content"]["input"][
                        "shared_decision_contract"
                    ]
                ),
                "a_has_no_locked_decision": "locked_decision" not in a_message,
                "b_has_locked_decision": ticket.selected_value in b_message,
                "c_has_selected_summary": ticket.selected_summary in c_message,
                "c_excludes_contract": "shared_decision_contract" not in c_message,
                "c_excludes_enum_value": ticket.selected_value not in c_message,
                "b_c_consume_same_ticket_hash": (
                    b["consumed_ticket_hash"]
                    == c["consumed_ticket_hash"]
                    == ticket_hash
                ),
                "private_route_metadata_absent_from_messages": all(
                    token not in rendered
                    for token in ("mock_selected_index", '"route"', "triplet_id")
                    for rendered in (a_message, b_message, c_message)
                ),
                "audit_only_fields_absent_from_messages": all(
                    field not in rendered
                    for field in AUDIT_ONLY_FIELDS
                    for rendered in (a_message, b_message, c_message)
                ),
            }
        )

    state_chains = {
        triplet["triplet_id"]: [
            record.model_dump(mode="json")
            for record in mock_state_chain(triplet["triplet_id"])
        ]
        for triplet in triplets
    }

    reviewer_semantics = {
        "schema_version": "1.2-r1-review-instructions",
        "true_semantics": {
            "mandatory_events_complete": "true 表示全部 mandatory events 已完成。",
            "unauthorized_new_character_detected": "true 表示发现未经授权的新人物，是硬失败。",
            "unauthorized_new_solution_detected": "true 表示发现 confirmed new solution，是硬失败。",
            "unauthorized_relationship_change_detected": "true 表示发现未经授权的关系变化，是硬失败。",
            "ending_remains_temporary": "true 表示结尾仍是临时状态，长期问题未解决。",
            "boundary_contract_satisfied": "true 表示正文遵守对应场景的锁定决策合同。",
        },
        "lock_order": [state.value for state in STATE_ORDER],
        "observed_decision_domain": {
            scene.scene_id: [
                *(option.value for option in scene.decision_contract.allowed_values),
                "unclear",
                "other",
            ]
            for scene in protocol.scenes
        },
    }

    public_shell = {
        "schema_version": "1.2-r1-public-shell",
        "contains_fiction": False,
        "identity_exposed": False,
        "triplets": [
            {
                "public_block_id": f"BLOCK-{index:02d}",
                "scene_id": triplet["scene_id"],
                "text_placeholders": [None, None, None],
            }
            for index, triplet in enumerate(triplets, start=1)
        ],
    }

    write_json(output_dir / "protocol.json", protocol.model_dump(mode="json"))
    write_json(
        output_dir / "private/triplet-matrix.private.json",
        {
            "schema_version": "1.2-r1",
            "triplets": triplets,
            "identity_rows": identity_rows,
        },
    )
    write_json(output_dir / "dry-run/final-request-snapshots.json", requests)
    write_json(output_dir / "dry-run/mock-decision-tickets.json", tickets)
    write_json(output_dir / "dry-run/state-chains.mock.json", state_chains)
    write_json(
        output_dir / "review/post-write-review-schema.json",
        R1PostWriteReview.model_json_schema(),
    )
    write_json(
        output_dir / "review/reviewer-semantics.json", reviewer_semantics
    )
    write_json(output_dir / "review/public-shell.json", public_shell)

    all_binding_checks = all(
        all(
            value
            for key, value in check.items()
            if key != "triplet_id"
        )
        for check in binding_checks
    )
    audit = {
        "schema_version": "1.2-r1",
        "scope": "three_arm_static_protocol_and_zero_call_dry_run",
        "enabled": protocol.enabled,
        "generation_authorized": protocol.generation_authorized,
        "model_calls": 0,
        "fiction_texts": 0,
        "blind_votes": 0,
        "scene_count": len(protocol.scenes),
        "topologies": [scene.topology for scene in protocol.scenes],
        "topologies_unique": len({scene.topology for scene in protocol.scenes}) == 4,
        "arms": protocol.arms,
        "planned_triplets": len(triplets),
        "planned_texts": len(triplets) * len(protocol.arms),
        "estimands": [
            {
                "id": item.estimand_id,
                "role": item.role,
                "comparison": item.comparison,
            }
            for item in protocol.estimands
        ],
        "validity_thresholds": protocol.triplet_validity.model_dump(),
        "binding_checks": binding_checks,
        "all_binding_checks_pass": all_binding_checks,
        "dynamic_validation": {
            "implemented_in": "validate_review_against_protocol",
            "scene_specific_domain_declared": True,
            "cross_scene_value_must_fail": True,
            "unclear_and_other_allowed_for_audit_but_block_ticket": True,
        },
        "state_machine": {
            "ordered_states": [state.value for state in STATE_ORDER],
            "triplet_chains": len(state_chains),
            "all_mock_chains_complete": all(
                len(records) == len(STATE_ORDER) for records in state_chains.values()
            ),
            "hash_linked": True,
            "mock_only": True,
        },
        "public_private_partition": {
            "private_identity_rows": 36,
            "public_blocks": len(public_shell["triplets"]),
            "public_contains_fiction": False,
            "public_identity_exposed": False,
        },
        "historical_v1_2_design_write_targets": [],
        "historical_v1_1_write_targets": [],
        "preflight_write_targets": [],
        "input_integrity": {
            "before": before_hash,
            "after": file_hash(protocol_path),
            "unchanged": before_hash == file_hash(protocol_path),
        },
        "r1_static_audit_pass": False,
    }
    audit["r1_static_audit_pass"] = all(
        [
            not audit["enabled"],
            not audit["generation_authorized"],
            audit["model_calls"] == 0,
            audit["fiction_texts"] == 0,
            audit["topologies_unique"],
            audit["arms"] == ["A", "B", "C"],
            audit["planned_triplets"] == 12,
            audit["planned_texts"] == 36,
            all_binding_checks,
            audit["state_machine"]["all_mock_chains_complete"],
            audit["input_integrity"]["unchanged"],
        ]
    )
    write_json(output_dir / "r1-static-audit.json", audit)
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": "1.2-r1",
            "stage": "static_protocol_and_zero_call_dry_run",
            "r1_static_audit_pass": audit["r1_static_audit_pass"],
            "next_stage_authorized": "independent_r1_design_red_team",
            "generation_authorized": False,
            "model_calls": 0,
            "fiction_texts": 0,
        },
    )
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output_dir)
    if args.action == "audit" and not result["r1_static_audit_pass"]:
        raise SystemExit("V1.2-R1 static audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
