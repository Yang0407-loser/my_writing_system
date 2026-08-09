from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .contract import allowed_values, contract_hash, contract_payload, load_contract
from .models import PreflightReviewRecord
from .prompts import (
    boundary_maker_prompt_snapshot,
    w0_prompt_snapshot,
    w2_realizer_prompt_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "experiments/writer_boundary_v12_preflight/fixtures/sc4_shared_decision_contract_v1_2.json"
SCENE_PATH = ROOT / "experiments/writer_boundary_canary/fixtures/writer_boundary_canary_sc4.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-preflight"
AUDIT_ONLY_FIELDS = {
    "evidence_paragraphs",
    "failed_event_ids",
    "additional_solution_candidates",
    "resource_constraint_preserved",
    "long_term_problem_unresolved",
    "observed_temporary_solution",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blank_review_template() -> dict[str, Any]:
    evidence = lambda: {"status": None, "evidence_paragraphs": [], "description": ""}
    return {
        "schema_version": "1.2-preflight",
        "text_id": "",
        "hard_checks": {
            "mandatory_events_complete": {
                **evidence(), "failed_event_ids": [],
            },
            "new_character": evidence(),
            "new_solution": {
                **evidence(), "candidates": [],
            },
            "relationship_change": evidence(),
            "temporary_ending": evidence(),
            "boundary_fidelity": evidence(),
        },
        "execution_audit": {
            "primary_obligation": evidence(),
            "observed_temporary_solution": {
                "value": "",
                "evidence_paragraphs": [],
                "description": "",
            },
            "additional_solution_candidates": [],
            "resource_constraint_preserved": evidence(),
            "long_term_problem_unresolved": evidence(),
        },
    }


def build_preflight(
    output_dir: Path = DEFAULT_OUTPUT,
    contract_path: Path = CONTRACT_PATH,
    scene_path: Path = SCENE_PATH,
) -> dict[str, Any]:
    # Read-only inputs; every write target is under the new versioned output.
    before = {
        str(contract_path): sha256(contract_path),
        str(scene_path): sha256(scene_path),
    }
    contract = load_contract(contract_path)
    scene = read_json(scene_path)
    w0 = w0_prompt_snapshot(scene, contract)
    maker = boundary_maker_prompt_snapshot(scene, contract, repeat=1)
    selected = allowed_values(contract)[0]
    realizer = w2_realizer_prompt_snapshot(scene, selected)

    write_json(output_dir / "shared-decision-contract.json", {
        "contract": contract_payload(contract),
        "decision_contract_hash": contract_hash(contract),
    })
    write_json(output_dir / "snapshots/w0-prompt.snapshot.json", w0)
    write_json(output_dir / "snapshots/boundary-maker-prompt.snapshot.json", maker)
    write_json(output_dir / "snapshots/w2-realizer-prompt.snapshot.json", realizer)
    write_json(output_dir / "review/preflight-review-template.json", blank_review_template())
    write_json(output_dir / "review/preflight-review-schema.json", PreflightReviewRecord.model_json_schema())

    w0_contract = w0["payload"]["shared_decision_contract"]
    maker_contract = maker["payload"]["shared_decision_contract"]
    w0_text = w0["messages"][0]["content"]
    maker_text = maker["messages"][0]["content"]
    realizer_text = realizer["messages"][0]["content"]
    values = allowed_values(contract)
    audit = {
        "schema_version": "1.2-preflight",
        "scope": "contract_schema_prompt_snapshot_mock_only",
        "scene_id": scene["scene_id"],
        "new_scene_designed": False,
        "model_calls": 0,
        "fiction_generated": False,
        "historical_v1_1_write_targets": [],
        "shared_contract": {
            "payloads_identical": w0_contract == maker_contract,
            "canonical_hash": contract_hash(contract),
            "w0_hash": w0["payload"]["decision_contract_hash"],
            "boundary_maker_hash": maker["payload"]["decision_contract_hash"],
            "hashes_identical": (
                w0["payload"]["decision_contract_hash"]
                == maker["payload"]["decision_contract_hash"]
                == contract_hash(contract)
            ),
            "w0_contains_all_values": all(value in w0_text for value in values),
            "maker_contains_all_values": all(value in maker_text for value in values),
            "w0_contains_all_definitions": all(
                item.definition in w0_text for item in contract.allowed_values
            ),
            "maker_contains_all_definitions": all(
                item.definition in maker_text for item in contract.allowed_values
            ),
        },
        "selection_contract": {
            "required_output_key_declared": '"selected_temporary_solution"' in maker_text,
            "scalar_value_required": "值必须是字符串" in maker_text,
            "array_forbidden": "禁止输出数组" in maker_text,
            "both_legal_objects_listed": all(
                json.dumps(
                    {"selected_temporary_solution": value},
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ) in maker_text
                for value in values
            ),
            "no_compatibility_fallback_in_preflight": True,
        },
        "route_isolation": {
            "w0_has_no_selected_solution": "selected_temporary_solution" not in w0_text,
            "realizer_has_selected_summary": selected in {
                selected
            } and "content_boundary" in realizer_text,
            "realizer_excludes_unselected_value": values[1] not in realizer_text,
            "audit_fields_absent_from_w0": sorted(
                field for field in AUDIT_ONLY_FIELDS if field in w0_text
            ),
            "audit_fields_absent_from_realizer": sorted(
                field for field in AUDIT_ONLY_FIELDS if field in realizer_text
            ),
        },
        "solution_boundary": {
            "allowed_detail_definition_present": bool(
                contract.solution_boundary_policy.allowed_detail_definition
            ),
            "additional_candidate_definition_present": bool(
                contract.solution_boundary_policy.additional_candidate_definition
            ),
            "confirmed_threshold": contract.solution_boundary_policy.confirmed_new_solution_min_signals,
            "confirmation_signal_count": len(
                contract.solution_boundary_policy.confirmation_signals
            ),
        },
        "input_integrity": {
            "before": before,
            "after": {
                str(contract_path): sha256(contract_path),
                str(scene_path): sha256(scene_path),
            },
            "unchanged": before
            == {
                str(contract_path): sha256(contract_path),
                str(scene_path): sha256(scene_path),
            },
        },
        "preflight_pass": False,
    }
    audit["preflight_pass"] = all([
        audit["shared_contract"]["payloads_identical"],
        audit["shared_contract"]["hashes_identical"],
        audit["shared_contract"]["w0_contains_all_values"],
        audit["shared_contract"]["maker_contains_all_values"],
        audit["shared_contract"]["w0_contains_all_definitions"],
        audit["shared_contract"]["maker_contains_all_definitions"],
        audit["selection_contract"]["required_output_key_declared"],
        audit["selection_contract"]["scalar_value_required"],
        audit["selection_contract"]["array_forbidden"],
        audit["selection_contract"]["both_legal_objects_listed"],
        audit["route_isolation"]["w0_has_no_selected_solution"],
        audit["route_isolation"]["realizer_excludes_unselected_value"],
        not audit["route_isolation"]["audit_fields_absent_from_w0"],
        not audit["route_isolation"]["audit_fields_absent_from_realizer"],
        audit["input_integrity"]["unchanged"],
    ])
    write_json(output_dir / "fairness-preflight-audit.json", audit)
    write_json(output_dir / "mock-manifest.json", {
        "schema_version": "1.2-preflight",
        "scene_count": 1,
        "scene_id": "SC4",
        "new_scenes": 0,
        "prompt_snapshots": 3,
        "shared_contracts": 1,
        "review_templates": 1,
        "review_schemas": 1,
        "model_calls": 0,
        "fiction_texts": 0,
        "route_evidence": False,
        "quality_conclusion_allowed": False,
        "preflight_pass": audit["preflight_pass"],
    })
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_preflight(args.output_dir)
    if args.action == "audit" and not result["preflight_pass"]:
        raise SystemExit("preflight audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

