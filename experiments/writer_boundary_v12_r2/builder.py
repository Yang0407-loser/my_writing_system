from __future__ import annotations

import argparse
import copy
import json
import random
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r1.models import R1Protocol, R1Scene

from .models import (
    AssignmentTicket,
    LedgerRecord,
    PrivateJoinRow,
    R2Protocol,
)
from .prompts import BC_INSTRUCTION, build_envelope
from .runtime import (
    STATE_ARTIFACT,
    STATE_ORDER,
    STATE_ROLE,
    canonical_hash,
    file_hash,
    make_ledger_record,
    validate_join,
    validate_public_shell,
    verify_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = ROOT / "experiments/writer_boundary_v12_r2/fixtures/v1_2_r2_design.json"
R1_PATH = ROOT / "experiments/writer_boundary_v12_r1/fixtures/v1_2_r1_protocol.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r2"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def materialize_protocol(
    design_path: Path = DESIGN_PATH, r1_path: Path = R1_PATH
) -> R2Protocol:
    design = read_json(design_path)
    if file_hash(r1_path) != design["base_r1_protocol_sha256"]:
        raise ValueError("R1 source hash drift")
    r1 = R1Protocol.model_validate_json(r1_path.read_text(encoding="utf-8"))
    scenes = [scene.model_dump() for scene in r1.scenes]
    overrides = design.pop("scene_overrides")
    for raw in scenes:
        scene_id = raw["scene_id"]
        if scene_id == "SC9":
            override = overrides[scene_id]
            raw["world_facts"][2] = override["world_fact_replacement"]
            values = raw["decision_contract"]["allowed_values"]
            index = next(
                i
                for i, value in enumerate(values)
                if value["value"] == override["replace_option_value"]
            )
            values[index] = override["replacement_option"]
        if scene_id == "SC11":
            override = overrides[scene_id]
            raw["scene"] = override["scene_replacement"]
            raw["world_facts"] = override["world_fact_replacements"]
            raw["decision_contract"]["allowed_values"] = override["replace_options"]
    return R2Protocol.model_validate({**design, "scenes": scenes})


def build_matrix(protocol: R2Protocol) -> dict[str, Any]:
    rng = random.Random(20261001)
    text_ids = [f"T{i:03d}" for i in range(1, 37)]
    rng.shuffle(text_ids)
    index = 0
    blocks = []
    for scene_index, scene in enumerate(protocol.scenes):
        for repeat in range(1, 4):
            block_id = f"BLOCK-{len(blocks) + 1:02d}"
            arms = {}
            for arm in protocol.arms:
                arms[arm] = text_ids[index]
                index += 1
            order = list(protocol.arms)
            rng.shuffle(order)
            selected_option_index = (scene_index + repeat - 1) % 2
            blocks.append(
                {
                    "block_id": block_id,
                    "scene_id": scene.scene_id,
                    "repeat": repeat,
                    "text_ids": arms,
                    "generation_order": order,
                    "assigned_option_index_for_b_c": selected_option_index,
                    "request_nonces": {
                        arm: canonical_hash(
                            {
                                "experiment": protocol.experiment_id,
                                "block": block_id,
                                "arm": arm,
                                "nonce_version": 1,
                            }
                        )[:24]
                        for arm in protocol.arms
                    },
                }
            )
    return {
        "schema_version": "1.2-r2-matrix",
        "experiment_id": protocol.experiment_id,
        "blocks": blocks,
        "fixed_denominator": 12,
        "reruns_allowed": False,
    }


def build_assignments(
    protocol: R2Protocol, matrix: dict[str, Any]
) -> list[tuple[AssignmentTicket, str]]:
    scenes = {scene.scene_id: scene for scene in protocol.scenes}
    matrix_hash = canonical_hash(matrix)
    result = []
    for index, block in enumerate(matrix["blocks"], start=1):
        scene = scenes[block["scene_id"]]
        option = scene.decision_contract.allowed_values[
            block["assigned_option_index_for_b_c"]
        ]
        ticket = AssignmentTicket(
            assignment_id=f"ASSIGN-{index:02d}",
            block_id=block["block_id"],
            scene_id=scene.scene_id,
            decision_id=scene.decision_contract.decision_id,
            selected_value=option.value,
            selected_definition=option.definition,
            selected_summary=option.selected_summary,
            matrix_sha256=matrix_hash,
        )
        result.append((ticket, canonical_hash(ticket.model_dump(mode="json"))))
    return result


def build_join(matrix: dict[str, Any]) -> list[PrivateJoinRow]:
    rng = random.Random(20261002)
    public_ids = [f"PUB-{i:03d}" for i in range(1, 37)]
    rng.shuffle(public_ids)
    rows = []
    index = 0
    for block in matrix["blocks"]:
        positions = [1, 2, 3]
        rng.shuffle(positions)
        for position, arm in zip(positions, ("A", "B", "C"), strict=True):
            rows.append(
                PrivateJoinRow(
                    block_id=block["block_id"],
                    public_block_id=f"PUBLIC-{len(rows) // 3 + 1:02d}",
                    private_text_id=block["text_ids"][arm],
                    arm=arm,
                    public_text_id=public_ids[index],
                    public_position=position,
                    content_sha256=None,
                )
            )
            index += 1
    validate_join(rows, require_content=False)
    return rows


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    input_before = {
        str(DESIGN_PATH): file_hash(DESIGN_PATH),
        str(R1_PATH): file_hash(R1_PATH),
    }
    protocol = materialize_protocol()
    protocol_payload = protocol.model_dump(mode="json")
    protocol_hash = canonical_hash(protocol_payload)
    matrix = build_matrix(protocol)
    matrix_hash = canonical_hash(matrix)
    assignments = build_assignments(protocol, matrix)
    assignment_by_block = {
        ticket.block_id: (ticket, ticket_hash)
        for ticket, ticket_hash in assignments
    }
    scenes = {scene.scene_id: scene for scene in protocol.scenes}

    requests = []
    checks = []
    for block in matrix["blocks"]:
        scene = scenes[block["scene_id"]]
        ticket, ticket_hash = assignment_by_block[block["block_id"]]
        envelopes = {}
        hashes = {}
        for arm in protocol.arms:
            envelope, digest = build_envelope(
                protocol=protocol,
                protocol_hash=protocol_hash,
                block=block,
                arm=arm,
                text_id=block["text_ids"][arm],
                scene=scene,
                ticket=None if arm == "A" else ticket,
                assignment_hash=None if arm == "A" else ticket_hash,
                matrix_hash=matrix_hash,
            )
            envelopes[arm] = envelope.model_dump(mode="json")
            hashes[arm] = digest
        b_instruction = envelopes["B"]["messages"][0]["content"]["instruction"]
        c_instruction = envelopes["C"]["messages"][0]["content"]["instruction"]
        rendered_a = json.dumps(envelopes["A"]["messages"], ensure_ascii=False)
        rendered_c = json.dumps(envelopes["C"]["messages"], ensure_ascii=False)
        checks.append(
            {
                "block_id": block["block_id"],
                "b_c_instruction_byte_identical": (
                    b_instruction.encode("utf-8") == c_instruction.encode("utf-8")
                    and b_instruction == BC_INSTRUCTION
                ),
                "a_has_no_assignment": (
                    envelopes["A"]["assignment_sha256"] is None
                    and "locked_assignment" not in rendered_a
                ),
                "b_c_same_assignment_hash": (
                    envelopes["B"]["assignment_sha256"]
                    == envelopes["C"]["assignment_sha256"]
                    == ticket_hash
                ),
                "c_excludes_contract_and_enum": (
                    "shared_decision_contract" not in rendered_c
                    and ticket.selected_value not in rendered_c
                ),
                "full_envelope_hashes_present": all(
                    len(value) == 64 for value in hashes.values()
                ),
            }
        )
        requests.append(
            {
                "block_id": block["block_id"],
                "envelopes": envelopes,
                "full_envelope_sha256": hashes,
            }
        )

    join_rows = build_join(matrix)
    synthetic_join = [
        row.model_copy(
            update={
                "content_sha256": canonical_hash(
                    {"synthetic_text": row.private_text_id, "fiction": False}
                )
            }
        )
        for row in join_rows
    ]
    validate_join(synthetic_join, require_content=True)
    public_shell = {
        "schema_version": "1.2-r2-public-shell",
        "contains_fiction": False,
        "blocks": [
            {
                "public_block_id": f"PUBLIC-{block_index:02d}",
                "scene_id": block["scene_id"],
                "texts": [
                    {
                        "public_text_id": row.public_text_id,
                        "position": row.public_position,
                        "text": None,
                    }
                    for row in sorted(
                        (
                            value
                            for value in join_rows
                            if value.block_id == block["block_id"]
                        ),
                        key=lambda item: item.public_position,
                    )
                ],
            }
            for block_index, block in enumerate(matrix["blocks"], start=1)
        ],
    }
    validate_public_shell(join_rows, public_shell)

    ledger_records: list[LedgerRecord] = []
    for state in STATE_ORDER:
        make_ledger_record(
            ledger_records,
            state=state,
            actor_role=STATE_ROLE[state],
            artifact_hashes={
                STATE_ARTIFACT[state]: canonical_hash(
                    {"synthetic_only": True, "state": state.value}
                )
            },
        )
    verify_ledger(ledger_records)

    assigned_indices = [
        block["assigned_option_index_for_b_c"] for block in matrix["blocks"]
    ]
    option_index_counts = {
        str(index): assigned_indices.count(index) for index in (0, 1)
    }
    audit = {
        "schema_version": "1.2-r2",
        "scope": "randomized_assignment_fixed_denominator_static_design",
        "enabled": False,
        "generation_authorized": False,
        "model_calls": 0,
        "fiction_texts": 0,
        "preference_votes": 0,
        "blocks": len(matrix["blocks"]),
        "planned_texts": 36,
        "fixed_denominator": protocol.pilot_rule.fixed_denominator,
        "a_controls_b_c_eligibility": False,
        "reruns_allowed": False,
        "assignment_option_index_counts": option_index_counts,
        "assignment_balanced_overall": sorted(option_index_counts.values()) == [6, 6],
        "all_request_checks_pass": all(
            all(value for key, value in item.items() if key != "block_id")
            for item in checks
        ),
        "request_checks": checks,
        "provider_seed_capability": protocol.provider_config.seed_capability,
        "ledger": {
            "states": [record.state.value for record in ledger_records],
            "production_record_model": True,
            "write_once_commit_api_present": True,
            "synthetic_proof_only": True,
            "chain_valid": True,
        },
        "sealed_join": {
            "private_rows": len(join_rows),
            "public_text_ids_unique": len(
                {row.public_text_id for row in join_rows}
            )
            == 36,
            "randomized_positions": True,
            "synthetic_content_hash_proof": True,
            "contains_real_fiction": False,
        },
        "input_integrity": {
            "before": input_before,
            "after": {
                str(DESIGN_PATH): file_hash(DESIGN_PATH),
                str(R1_PATH): file_hash(R1_PATH),
            },
            "unchanged": input_before
            == {
                str(DESIGN_PATH): file_hash(DESIGN_PATH),
                str(R1_PATH): file_hash(R1_PATH),
            },
        },
        "historical_r1_write_targets": [],
        "historical_v1_2_write_targets": [],
        "r2_static_audit_pass": False,
    }
    audit["r2_static_audit_pass"] = all(
        [
            audit["blocks"] == 12,
            audit["planned_texts"] == 36,
            audit["fixed_denominator"] == 12,
            not audit["a_controls_b_c_eligibility"],
            not audit["reruns_allowed"],
            audit["assignment_balanced_overall"],
            audit["all_request_checks_pass"],
            audit["ledger"]["chain_valid"],
            audit["sealed_join"]["public_text_ids_unique"],
            audit["input_integrity"]["unchanged"],
            audit["model_calls"] == 0,
            not audit["generation_authorized"],
        ]
    )

    write_json(output_dir / "protocol.materialized.json", protocol_payload)
    write_json(output_dir / "private/experiment-matrix.private.json", matrix)
    write_json(
        output_dir / "private/assignment-ledger.private.json",
        {
            "matrix_sha256": matrix_hash,
            "assignments": [
                {
                    "ticket": ticket.model_dump(mode="json"),
                    "assignment_sha256": ticket_hash,
                }
                for ticket, ticket_hash in assignments
            ],
        },
    )
    write_json(output_dir / "requests/full-request-envelopes.json", requests)
    write_json(
        output_dir / "ledger/state-chain.synthetic.json",
        [record.model_dump(mode="json") for record in ledger_records],
    )
    write_json(
        output_dir / "blind/private-join-template.private.json",
        [row.model_dump(mode="json") for row in join_rows],
    )
    write_json(
        output_dir / "blind/private-join-synthetic-proof.private.json",
        [row.model_dump(mode="json") for row in synthetic_join],
    )
    write_json(output_dir / "blind/public-shell.json", public_shell)
    write_json(
        output_dir / "analysis/pilot-aggregation-policy.json",
        protocol.pilot_rule.model_dump(),
    )
    write_json(output_dir / "r2-static-audit.json", audit)
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": "1.2-r2",
            "stage": "static_revision_and_synthetic_integrity_proof",
            "r2_static_audit_pass": audit["r2_static_audit_pass"],
            "next_stage_authorized": "independent_r2_three_party_review",
            "generation_package_authorized": False,
            "model_generation_authorized": False,
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
    audit = build(args.output_dir)
    if args.action == "audit" and not audit["r2_static_audit_pass"]:
        raise SystemExit("R2 static audit failed")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
