from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_preflight.contract import canonical_hash
from .models import DesignScene, ExperimentDesign, V12PostWriteReviewRecord


ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = ROOT / "experiments/writer_boundary_v12_design/fixtures/v1_2_multiscene_design.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-design"
AUDIT_ONLY_FIELDS = {
    "evidence_paragraphs",
    "additional_solution_candidates",
    "resource_constraint_preserved",
    "long_term_problem_unresolved",
    "observed_temporary_solution",
}


def read_design(path: Path = DESIGN_PATH) -> ExperimentDesign:
    return ExperimentDesign.model_validate_json(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_payload(scene: DesignScene) -> dict[str, Any]:
    return scene.decision_contract.model_dump()


def common_scene_payload(scene: DesignScene) -> dict[str, Any]:
    return {
        "scene_id": scene.scene_id,
        "scene": scene.scene,
        "characters": scene.characters,
        "world_facts": scene.world_facts,
        "primary_obligation": scene.primary_obligation,
        "resource_constraint": scene.resource_constraint,
        "long_term_problem": scene.long_term_problem,
        "mandatory_events": scene.mandatory_events,
        "forbidden_events": scene.forbidden_events,
    }


def w0_snapshot(scene: DesignScene, repeat: int) -> dict[str, Any]:
    contract = contract_payload(scene)
    return {
        "route": "W0",
        "scene_id": scene.scene_id,
        "repeat": repeat,
        "instruction": (
            "从 shared_decision_contract 的两个合法方案中自行选择且只选择一个，"
            "并在同一次调用中写成自然小说正文。不得输出枚举名、分析或检查清单。"
        ),
        "payload": {
            **common_scene_payload(scene),
            "shared_decision_contract": contract,
            "decision_contract_hash": canonical_hash(contract),
            "style_signature": scene.style_signature.model_dump(),
            "target_chars": scene.target_chars,
        },
    }


def boundary_maker_snapshot(scene: DesignScene, repeat: int) -> dict[str, Any]:
    contract = contract_payload(scene)
    legal_outputs = [
        {"selected_temporary_solution": item.value}
        for item in scene.decision_contract.allowed_values
    ]
    return {
        "route": "BOUNDARY_MAKER",
        "scene_id": scene.scene_id,
        "repeat": repeat,
        "instruction": (
            '只输出一个 JSON 对象；唯一键为 "selected_temporary_solution"，'
            "值必须是字符串并取自白名单。禁止输出数组、理由、分析或其他键。"
        ),
        "legal_outputs": legal_outputs,
        "payload": {
            **common_scene_payload(scene),
            "shared_decision_contract": contract,
            "decision_contract_hash": canonical_hash(contract),
        },
    }


def realizer_snapshot(scene: DesignScene, repeat: int, selected_index: int) -> dict[str, Any]:
    selected = scene.decision_contract.allowed_values[selected_index]
    return {
        "route": "W1_REALIZER",
        "scene_id": scene.scene_id,
        "repeat": repeat,
        "mock_selected_index": selected_index,
        "instruction": (
            "在不改变已确定内容边界的前提下，自主组织并写成自然小说正文；"
            "不必逐句重述边界，不得输出分析或检查清单。"
        ),
        "payload": {
            **common_scene_payload(scene),
            "style_signature": scene.style_signature.model_dump(),
            "content_boundary": {
                "decision_id": scene.decision_contract.decision_id,
                "selected_definition": selected.definition,
            },
            "target_chars": scene.target_chars,
        },
    }


def make_matrix(design: ExperimentDesign) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [
        {
            "scene_id": scene.scene_id,
            "repeat": repeat,
            "route": route,
            "seed": design.base_seed + scene_index * 100 + repeat * 10 + route_index,
        }
        for scene_index, scene in enumerate(design.scenes, start=1)
        for repeat in range(1, design.repeats_per_route + 1)
        for route_index, route in enumerate(design.routes)
    ]
    anonymous_ids = [f"T{i:02d}" for i in range(1, len(rows) + 1)]
    random.Random(design.base_seed).shuffle(anonymous_ids)
    private_rows = []
    public_rows = []
    for row, text_id in zip(rows, anonymous_ids, strict=True):
        private_rows.append({**row, "text_id": text_id})
        public_rows.append(
            {
                "text_id": text_id,
                "scene_id": row["scene_id"],
                "repeat_block": row["repeat"],
                "text_placeholder": None,
            }
        )
    return private_rows, sorted(public_rows, key=lambda item: item["text_id"])


def build(output_dir: Path = DEFAULT_OUTPUT, design_path: Path = DESIGN_PATH) -> dict[str, Any]:
    before_hash = file_hash(design_path)
    design = read_design(design_path)
    private_matrix, reviewer_shell = make_matrix(design)

    generation_scenes = []
    snapshots: list[dict[str, Any]] = []
    for scene in design.scenes:
        generation_scenes.append(
            {
                "scene": common_scene_payload(scene),
                "shared_decision_contract": contract_payload(scene),
                "decision_contract_hash": canonical_hash(contract_payload(scene)),
            }
        )
        for repeat in range(1, design.repeats_per_route + 1):
            snapshots.extend(
                [
                    w0_snapshot(scene, repeat),
                    boundary_maker_snapshot(scene, repeat),
                    realizer_snapshot(scene, repeat, selected_index=0),
                    realizer_snapshot(scene, repeat, selected_index=1),
                ]
            )

    write_json(output_dir / "generation/scene-contract-pack.json", generation_scenes)
    write_json(output_dir / "generation/prompt-snapshots.json", snapshots)
    write_json(
        output_dir / "private/experiment-matrix.private.json",
        {
            "schema_version": "1.2-design",
            "identity_sensitive": True,
            "rows": private_matrix,
        },
    )
    write_json(
        output_dir / "review/reviewer-public-shell.json",
        {
            "schema_version": "1.2-design",
            "contains_fiction": False,
            "route_identity_exposed": False,
            "rows": reviewer_shell,
        },
    )
    write_json(
        output_dir / "review/execution-audit-schema.json",
        V12PostWriteReviewRecord.model_json_schema(),
    )
    write_json(
        output_dir / "review/pairing-policy.json",
        {
            "primary_pair_rule": design.primary_pair_rule,
            "unmatched_pair_policy": design.unmatched_pair_policy,
            "reason": (
                "Only compare prose preferences when scene, repeat, and observed temporary "
                "solution match; otherwise solution choice is a competing explanation."
            ),
        },
    )

    snapshot_checks = []
    for scene in design.scenes:
        for repeat in range(1, design.repeats_per_route + 1):
            w0 = w0_snapshot(scene, repeat)
            maker = boundary_maker_snapshot(scene, repeat)
            contract = contract_payload(scene)
            options = [item.value for item in scene.decision_contract.allowed_values]
            realizers = [realizer_snapshot(scene, repeat, index) for index in (0, 1)]
            snapshot_checks.append(
                {
                    "scene_id": scene.scene_id,
                    "repeat": repeat,
                    "shared_payload_identical": (
                        w0["payload"]["shared_decision_contract"]
                        == maker["payload"]["shared_decision_contract"]
                    ),
                    "shared_hash_identical": (
                        w0["payload"]["decision_contract_hash"]
                        == maker["payload"]["decision_contract_hash"]
                        == canonical_hash(contract)
                    ),
                    "maker_scalar_key_exact": (
                        set(maker["legal_outputs"][0]) == {"selected_temporary_solution"}
                        and all(
                            isinstance(item["selected_temporary_solution"], str)
                            for item in maker["legal_outputs"]
                        )
                    ),
                    "realizer_excludes_unselected_value": all(
                        options[1 - index]
                        not in json.dumps(realizers[index], ensure_ascii=False)
                        for index in (0, 1)
                    ),
                    "audit_fields_absent_from_writer": all(
                        field not in json.dumps(snapshot, ensure_ascii=False)
                        for field in AUDIT_ONLY_FIELDS
                        for snapshot in [w0, *realizers]
                    ),
                }
            )

    private_ids = {row["text_id"] for row in private_matrix}
    public_ids = {row["text_id"] for row in reviewer_shell}
    audit = {
        "schema_version": "1.2-design",
        "scope": "multiscene_static_design_only",
        "design_enabled": design.enabled,
        "scene_count": len(design.scenes),
        "structural_axes": [scene.structural_axis for scene in design.scenes],
        "structural_axes_unique": len({scene.structural_axis for scene in design.scenes}) == 4,
        "routes": design.routes,
        "repeats_per_route": design.repeats_per_route,
        "planned_text_count": len(private_matrix),
        "model_calls": 0,
        "fiction_generated": False,
        "blind_reviews_created": False,
        "historical_v1_1_write_targets": [],
        "preflight_layer_write_targets": [],
        "scene_contract_checks": {
            "two_options_each": all(
                len(scene.decision_contract.allowed_values) == 2 for scene in design.scenes
            ),
            "six_mandatory_each": all(
                len(scene.mandatory_events) == 6 for scene in design.scenes
            ),
            "eight_forbidden_each": all(
                len(scene.forbidden_events) == 8 for scene in design.scenes
            ),
            "two_characters_each": all(len(scene.characters) == 2 for scene in design.scenes),
            "confirmed_solution_threshold_two": all(
                scene.decision_contract.solution_boundary_policy.confirmed_new_solution_min_signals
                == 2
                for scene in design.scenes
            ),
        },
        "snapshot_checks": snapshot_checks,
        "identity_partition": {
            "private_public_ids_match": private_ids == public_ids,
            "public_route_identity_exposed": any(
                "route" in row for row in reviewer_shell
            ),
            "public_fiction_present": any(
                row["text_placeholder"] is not None for row in reviewer_shell
            ),
        },
        "pairing_rule": design.primary_pair_rule,
        "unmatched_pair_policy": design.unmatched_pair_policy,
        "input_integrity": {
            "before": before_hash,
            "after": file_hash(design_path),
            "unchanged": before_hash == file_hash(design_path),
        },
        "design_audit_pass": False,
    }
    flat_snapshot_pass = all(
        all(
            check[key]
            for key in (
                "shared_payload_identical",
                "shared_hash_identical",
                "maker_scalar_key_exact",
                "realizer_excludes_unselected_value",
                "audit_fields_absent_from_writer",
            )
        )
        for check in snapshot_checks
    )
    audit["design_audit_pass"] = all(
        [
            not audit["design_enabled"],
            audit["scene_count"] == 4,
            audit["structural_axes_unique"],
            audit["planned_text_count"] == 24,
            audit["model_calls"] == 0,
            not audit["fiction_generated"],
            not audit["blind_reviews_created"],
            all(audit["scene_contract_checks"].values()),
            flat_snapshot_pass,
            audit["identity_partition"]["private_public_ids_match"],
            not audit["identity_partition"]["public_route_identity_exposed"],
            not audit["identity_partition"]["public_fiction_present"],
            audit["input_integrity"]["unchanged"],
        ]
    )
    write_json(output_dir / "static-design-audit.json", audit)
    write_json(
        output_dir / "design-manifest.json",
        {
            "schema_version": "1.2-design",
            "stage": "multiscene_design",
            "design_audit_pass": audit["design_audit_pass"],
            "next_stage_authorized": "independent_design_review",
            "generation_authorized": False,
            "model_calls": 0,
            "fiction_texts": 0,
            "blind_review_results": 0,
        },
    )
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output_dir)
    if args.action == "audit" and not result["design_audit_pass"]:
        raise SystemExit("V1.2 static design audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
