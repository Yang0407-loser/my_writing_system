from __future__ import annotations

import argparse
import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .kernel import (
    FakeProviderGateway,
    STATES,
    TransactionLedger,
    aggregate_primary,
    bind_audit,
    bind_vote,
    build_request,
    canonical_json,
    create_private_join,
    digest_bytes,
    digest_json,
    make_assignment,
)
from .models import ExecutionAudit, HardOutcome, PreferenceVote


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments/writer_boundary_v12_r3/fixtures/v1_2_r3_design.json"
R2_PROTOCOL = ROOT / "outputs/writer-boundary-v1-2-r2/protocol.materialized.json"
R2_MATRIX = ROOT / "outputs/writer-boundary-v1-2-r2/private/experiment-matrix.private.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def materialize() -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_json(CONFIG_PATH)
    if file_hash(R2_PROTOCOL) != config["r2_protocol_sha256"]:
        raise ValueError("R2 protocol drift")
    if file_hash(R2_MATRIX) != config["r2_matrix_sha256"]:
        raise ValueError("R2 matrix drift")
    protocol = copy.deepcopy(load_json(R2_PROTOCOL))
    matrix = copy.deepcopy(load_json(R2_MATRIX))
    protocol["schema_version"] = "1.2-r3"
    protocol["experiment_id"] = config["experiment_id"]
    protocol["enabled"] = False
    protocol["generation_authorized"] = False
    protocol["provider_config"]["seed_capability"] = "unverified_dependency"
    for scene in protocol["scenes"]:
        if scene["scene_id"] == "SC11":
            scene["scene"] = (
                "闭馆后借展密封柜持续报警。两名夜班人员无权开柜或改参数，"
                "只能在两个等价入口中关闭一个，并从对应观察窗完成同样十分钟、"
                "起止两次记录。专业检查留到天亮。"
            )
            scene["world_facts"] = [
                "两人无权开柜、移动借展品或改变恒湿参数。",
                "南北两个入口与观察窗条件等价，关闭任一入口都在夜班权限内。",
                "两种响应均为十分钟并在起点和终点各记录一次。"
            ]
            scene["decision_contract"]["allowed_values"] = [
                {
                    "value": "close_north_entry_two_point_log",
                    "definition": "关闭北侧入口，从北侧观察窗完成十分钟起止两次记录。",
                    "selected_summary": "关闭北侧入口，并从对应观察窗完成十分钟起止两次记录；不得开柜或改参数。",
                    "allowed_implementation_details": ["确认北门闭合。", "两次记录使用同一时钟。"]
                },
                {
                    "value": "close_south_entry_two_point_log",
                    "definition": "关闭南侧入口，从南侧观察窗完成十分钟起止两次记录。",
                    "selected_summary": "关闭南侧入口，并从对应观察窗完成十分钟起止两次记录；不得开柜或改参数。",
                    "allowed_implementation_details": ["确认南门闭合。", "两次记录使用同一时钟。"]
                }
            ]
        if scene["scene_id"] == "SC12":
            scene["scene"] = (
                "无完整标签的旧录音带出现无法确认性质的白点。两名整理员不能播放、"
                "清洁或鉴定，只能把磁带放入同一种透明密封观察盒，并在两个中性记录"
                "顺序中选择一种。鉴定与修复留到天亮。"
            )
            scene["world_facts"] = [
                "现场无法判断白点性质。",
                "两种方案都使用同一个规格的透明密封观察盒。",
                "夜班只能记录可见事实，不得播放、清洁、取样或鉴定。"
            ]
            scene["decision_contract"]["allowed_values"] = [
                {
                    "value": "position_then_condition_record",
                    "definition": "放入透明密封观察盒，先记录白点位置再记录其他可见状态。",
                    "selected_summary": "磁带放入透明密封观察盒，先记录白点位置、再记录其他可见状态；不判断性质。",
                    "allowed_implementation_details": ["使用现有位置网格。", "只记录可见事实。"]
                },
                {
                    "value": "condition_then_position_record",
                    "definition": "放入透明密封观察盒，先记录其他可见状态再记录白点位置。",
                    "selected_summary": "磁带放入透明密封观察盒，先记录其他可见状态、再记录白点位置；不判断性质。",
                    "allowed_implementation_details": ["使用现有位置网格。", "只记录可见事实。"]
                }
            ]
    matrix["schema_version"] = "1.2-r3-matrix"
    matrix["experiment_id"] = config["experiment_id"]
    return protocol, matrix


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    protocol, matrix = materialize()
    assignments = make_assignment(matrix, protocol)
    requests, receipts, text_bytes, audits = {}, {}, {}, {}
    gateway = FakeProviderGateway()
    for block in matrix["blocks"]:
        for arm in ("A", "B", "C"):
            envelope, envelope_hash = build_request(
                protocol, matrix, assignments, block["block_id"], arm
            )
            receipt = gateway.consume(envelope, envelope_hash)
            text_id = block["text_ids"][arm]
            raw = f"SYNTHETIC NONFICTION PLACEHOLDER {text_id}".encode()
            scene = next(item for item in protocol["scenes"] if item["scene_id"] == block["scene_id"])
            assignment = next(
                item for item in assignments["assignments"] if item["block_id"] == block["block_id"]
            )
            observed = (
                scene["decision_contract"]["allowed_values"][0]["value"]
                if arm == "A" else assignment["selected_value"]
            )
            audit = ExecutionAudit(
                reviewer_id="SYNTHETIC-AUDITOR",
                block_id=block["block_id"],
                scene_id=block["scene_id"],
                text_id=text_id,
                arm=arm,
                request_sha256=envelope_hash,
                content_sha256=digest_bytes(raw),
                observed_decision=observed,
                hard=HardOutcome(
                    artifact_status="present",
                    mandatory_events_complete=True,
                    unauthorized_new_character_detected=False,
                    unauthorized_new_solution_detected=False,
                    unauthorized_relationship_change_detected=False,
                ),
            )
            audit_hash = bind_audit(
                audit,
                text_bytes=raw,
                envelope=envelope,
                matrix=matrix,
                allowed_values={
                    item["value"] for item in scene["decision_contract"]["allowed_values"]
                },
            )
            requests[text_id] = {"envelope": envelope, "sha256": envelope_hash}
            receipts[text_id] = receipt.model_dump(mode="json")
            text_bytes[text_id] = raw
            audits[text_id] = {"audit": audit.model_dump(mode="json"), "sha256": audit_hash}

    join_rows, join_commitment = create_private_join(
        matrix, entropy=digest_bytes(b"R3 SYNTHETIC ENTROPY").encode()
    )
    public_contents = {
        row["public_text_id"]: text_bytes[row["private_text_id"]] for row in join_rows
    }
    votes = []
    for reviewer_index in range(1, 4):
        for block in matrix["blocks"]:
            rows = [row for row in join_rows if row["block_id"] == block["block_id"]]
            a = next(row for row in rows if row["arm"] == "A")
            c = next(row for row in rows if row["arm"] == "C")
            vote = PreferenceVote(
                reviewer_id=f"SYNTHETIC-REVIEWER-{reviewer_index}",
                public_block_id=block["block_id"],
                public_a_id=a["public_text_id"],
                public_c_id=c["public_text_id"],
                public_a_content_sha256=digest_bytes(public_contents[a["public_text_id"]]),
                public_c_content_sha256=digest_bytes(public_contents[c["public_text_id"]]),
                naturalness="tie",
                less_template="tie",
                overall_quality="tie",
            )
            votes.append({"vote": vote.model_dump(mode="json"), "sha256": bind_vote(vote, public_contents)})

    outcomes = [
        {
            "block_id": block["block_id"],
            "scene_id": block["scene_id"],
            "a_status": "present",
            "c_status": "present",
            "naturalness": "tie",
            "less_template": "tie",
            "overall_quality": "tie",
            "hard_non_degradation": True,
        }
        for block in matrix["blocks"]
    ]
    aggregate = aggregate_primary(
        matrix=matrix, locked_matrix_hash=digest_json(matrix), outcomes=outcomes
    )

    with tempfile.TemporaryDirectory() as temp:
        ledger_path = Path(temp) / "r3-synthetic.sqlite"
        ledger = TransactionLedger(ledger_path)
        state_objects = [
            {"protocol": canonical_json(protocol).encode(), "matrix": canonical_json(matrix).encode()},
            {"assignments": canonical_json(assignments).encode()},
            {"requests": canonical_json(requests).encode()},
            {"texts": b"".join(text_bytes[key] for key in sorted(text_bytes))},
            {"audits": canonical_json(audits).encode()},
            {"join": canonical_json(join_rows).encode(), "commitment": join_commitment.encode()},
            {"votes": canonical_json(votes).encode()},
            {"identity": canonical_json(join_rows).encode()},
            {"aggregate": canonical_json(aggregate).encode()},
        ]
        for state, objects in zip(STATES, state_objects, strict=True):
            ledger.commit(state, objects, {"synthetic_only": True})
        ledger.verify()
        output_db = output_dir / "ledger/r3-synthetic.sqlite"
        output_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger_path, output_db)
        states = ledger.states()

    write_json(output_dir / "protocol.materialized.json", protocol)
    write_json(output_dir / "private/matrix.private.json", matrix)
    write_json(output_dir / "private/assignments.private.json", assignments)
    write_json(output_dir / "requests/locked-requests.synthetic.json", requests)
    write_json(output_dir / "provider/receipts.synthetic.json", receipts)
    write_json(output_dir / "audits/audits.synthetic.json", audits)
    write_json(output_dir / "blind/join.private.synthetic.json", {"commitment": join_commitment, "rows": join_rows})
    write_json(output_dir / "votes/votes.synthetic.json", votes)
    write_json(output_dir / "analysis/aggregate.synthetic.json", aggregate)
    audit = {
        "schema_version": "1.2-r3",
        "transaction_states": states,
        "matrix_blocks": 12,
        "requests": 36,
        "provider_receipts": 36,
        "audits": 36,
        "votes": 36,
        "a_controls_bc": False,
        "synthetic_only": True,
        "model_calls": 0,
        "fiction_texts": 0,
        "generation_authorized": False,
        "aggregate_conclusion": aggregate["conclusion"],
        "r3_static_pass": states == STATES and aggregate["conclusion"] == "do_not_expand",
    }
    write_json(output_dir / "r3-static-audit.json", audit)
    write_json(output_dir / "manifest.json", {
        "schema_version": "1.2-r3",
        "next_stage_authorized": "independent_r3_three_party_review",
        "generation_package_authorized": False,
        "model_generation_authorized": False,
        "model_calls": 0,
        "fiction_texts": 0
    })
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    audit = build(args.output_dir)
    if args.action == "audit" and not audit["r3_static_pass"]:
        raise SystemExit("R3 static audit failed")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
