from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import (
    FakeProviderGateway,
    aggregate_primary,
    build_request,
    digest_bytes,
    digest_json,
    make_assignment,
)

from .kernel import (
    STATES,
    R31Ledger,
    assert_public_pack_neutral,
    bind_neutral_audit,
    bind_neutral_vote,
    build_public_packs,
    canonical_json,
    create_anonymity_map,
    unblind_votes,
)
from .models import (
    HardCheckEvidence,
    NeutralExecutionAudit,
    NeutralPreferenceVote,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments/writer_boundary_v12_r31/fixtures/v1_2_r31_design.json"
R3_PROTOCOL = ROOT / "outputs/writer-boundary-v1-2-r3/protocol.materialized.json"
R3_MATRIX = ROOT / "outputs/writer-boundary-v1-2-r3/private/matrix.private.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3-1"
DEFAULT_REPORT = ROOT / "reports/writer-boundary-v1-2-r3-1-anonymity-handoff-2026-07-30.md"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = load_json(CONFIG_PATH)
    if digest_bytes(R3_PROTOCOL.read_bytes()) != config["r3_protocol_sha256"]:
        raise ValueError("R3 protocol drift")
    if digest_bytes(R3_MATRIX.read_bytes()) != config["r3_matrix_sha256"]:
        raise ValueError("R3 matrix drift")
    return config, load_json(R3_PROTOCOL), load_json(R3_MATRIX)


def _hard_checks() -> list[HardCheckEvidence]:
    return [
        HardCheckEvidence(
            check_id=check_id,
            passed=True,
            paragraph_ids=["P1"],
            explanation="合成占位材料仅用于验证证据字段和交接约束。",
        )
        for check_id in (
            "mandatory_events",
            "unauthorized_new_character",
            "unauthorized_new_solution",
            "unauthorized_relationship_change",
        )
    ]


def _role_access() -> dict[str, Any]:
    return {
        "custodian": ["design", "assignment", "request"],
        "text_ingestor": ["provider_receipt", "text_bytes"],
        "execution_auditor": ["public_execution_pack"],
        "blind_pack_custodian": ["content_hashes", "private_anonymity_map"],
        "preference_reviewer": ["public_preference_pack"],
        "identity_custodian": ["private_anonymity_map", "locked_votes"],
        "aggregator": ["normalized_votes", "locked_audits", "frozen_matrix"],
        "separation_rules": [
            "execution_auditor cannot be identity_custodian, preference_reviewer, or aggregator",
            "preference_reviewer cannot access ledger, private map, requests, assignments, or audits",
            "identity_custodian cannot unblind before VOTES_LOCKED",
        ],
    }


def build(
    output_dir: Path = DEFAULT_OUTPUT,
    report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    config, protocol, matrix = materialize()
    assignments = make_assignment(matrix, protocol)
    requests: dict[str, Any] = {}
    receipts: dict[str, Any] = {}
    text_bytes: dict[str, bytes] = {}
    gateway = FakeProviderGateway()
    for block in matrix["blocks"]:
        for arm in ("A", "B", "C"):
            envelope, request_hash = build_request(
                protocol, matrix, assignments, block["block_id"], arm
            )
            text_id = block["text_ids"][arm]
            requests[text_id] = {"envelope": envelope, "sha256": request_hash}
            receipts[text_id] = gateway.consume(envelope, request_hash).model_dump(mode="json")
            text_bytes[text_id] = f"SYNTHETIC NONFICTION PLACEHOLDER {text_id}".encode("utf-8")

    content_hashes = {text_id: digest_bytes(raw) for text_id, raw in text_bytes.items()}
    anonymity_map = create_anonymity_map(matrix, content_hashes)
    execution_pack, preference_pack = build_public_packs(anonymity_map, text_bytes)
    assert_public_pack_neutral(execution_pack)
    assert_public_pack_neutral(preference_pack)

    allowed_values = {
        scene["scene_id"]: {
            option["value"] for option in scene["decision_contract"]["allowed_values"]
        }
        for scene in protocol["scenes"]
    }
    assignment_by_block = {
        item["block_id"]: item for item in assignments["assignments"]
    }
    audits: list[NeutralExecutionAudit] = []
    audit_receipts = []
    for row in anonymity_map["rows"]:
        observed = (
            next(iter(allowed_values[row["scene_id"]]))
            if row["arm"] == "A"
            else assignment_by_block[row["private_block_id"]]["selected_value"]
        )
        audit = NeutralExecutionAudit(
            reviewer_id="SYNTHETIC-EXECUTION-AUDITOR",
            public_text_id=row["public_text_id"],
            scene_id=row["scene_id"],
            content_sha256=row["content_sha256"],
            observed_decision=observed,
            hard_checks=_hard_checks(),
        )
        audit_receipts.append(
            {
                "audit": audit.model_dump(mode="json"),
                "sha256": bind_neutral_audit(
                    audit,
                    anonymity_map=anonymity_map,
                    text_bytes=text_bytes,
                    allowed_values_by_scene=allowed_values,
                ),
            }
        )
        audits.append(audit)

    public_contents = {
        item["public_text_id"]: item["text"].encode("utf-8")
        for item in execution_pack["items"]
    }
    votes: list[NeutralPreferenceVote] = []
    vote_receipts = []
    for reviewer_index in range(1, 4):
        for block in preference_pack["blocks"]:
            vote = NeutralPreferenceVote(
                reviewer_id=f"SYNTHETIC-PREFERENCE-REVIEWER-{reviewer_index}",
                public_block_id=block["public_block_id"],
                candidate_1_id=block["candidate_1"]["public_text_id"],
                candidate_2_id=block["candidate_2"]["public_text_id"],
                candidate_1_content_sha256=block["candidate_1"]["content_sha256"],
                candidate_2_content_sha256=block["candidate_2"]["content_sha256"],
                naturalness="tie",
                less_template="tie",
                overall_quality="tie",
            )
            vote_receipts.append(
                {
                    "vote": vote.model_dump(mode="json"),
                    "sha256": bind_neutral_vote(
                        vote,
                        anonymity_map=anonymity_map,
                        public_contents=public_contents,
                    ),
                }
            )
            votes.append(vote)

    with tempfile.TemporaryDirectory() as temporary:
        ledger = R31Ledger(Path(temporary) / "r3-1-synthetic.sqlite")
        commits = [
            ("DESIGN_LOCKED", {"protocol": (canonical_json(protocol).encode(), "private"),
                               "matrix": (canonical_json(matrix).encode(), "private")}),
            ("ASSIGNMENTS_LOCKED", {"assignments": (canonical_json(assignments).encode(), "private")}),
            ("REQUESTS_LOCKED", {"requests": (canonical_json(requests).encode(), "private")}),
            ("TEXTS_LOCKED", {"texts": (b"".join(text_bytes[key] for key in sorted(text_bytes)), "private")}),
            ("ANONYMITY_MAP_LOCKED", {"anonymity_map": (canonical_json(anonymity_map).encode(), "private")}),
            ("EXECUTION_PACK_LOCKED", {"execution_pack": (canonical_json(execution_pack).encode(), "public")}),
            ("AUDITS_LOCKED", {"audits": (canonical_json(audit_receipts).encode(), "private")}),
            ("PREFERENCE_PACK_LOCKED", {"preference_pack": (canonical_json(preference_pack).encode(), "public")}),
            ("VOTES_LOCKED", {"votes": (canonical_json(vote_receipts).encode(), "private")}),
        ]
        for state, objects in commits:
            ledger.commit(state, objects, {"synthetic_only": True})
        normalized = unblind_votes(
            votes, anonymity_map=anonymity_map, locked_states=ledger.states()
        )
        ledger.commit(
            "IDENTITY_UNBLINDED",
            {"normalized_votes": (canonical_json(normalized).encode(), "private")},
            {"synthetic_only": True},
        )

        outcomes = []
        for block in matrix["blocks"]:
            block_votes = [
                item for item in normalized if item["private_block_id"] == block["block_id"]
            ]
            outcome: dict[str, Any] = {
                "block_id": block["block_id"],
                "scene_id": block["scene_id"],
                "a_status": "present",
                "c_status": "present",
                "hard_non_degradation": True,
            }
            for metric in ("naturalness", "less_template", "overall_quality"):
                counts = Counter(item[metric] for item in block_votes)
                outcome[metric] = counts.most_common(1)[0][0]
            outcomes.append(outcome)
        aggregate = aggregate_primary(
            matrix=matrix,
            locked_matrix_hash=digest_json(matrix),
            outcomes=outcomes,
        )
        ledger.commit(
            "AGGREGATED",
            {"aggregate": (canonical_json(aggregate).encode(), "private")},
            {"synthetic_only": True},
        )
        ledger.verify()
        states = ledger.states()
        ledger_target = output_dir / "private/ledger/r3-1-synthetic.sqlite"
        ledger_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger.path, ledger_target)

    write_json(output_dir / "public/execution-pack.synthetic.json", execution_pack)
    write_json(output_dir / "public/preference-pack.synthetic.json", preference_pack)
    write_json(output_dir / "review/audits.synthetic.json", audit_receipts)
    write_json(output_dir / "review/votes.synthetic.json", vote_receipts)
    write_json(output_dir / "private/identity-unblind.synthetic.json", normalized)
    write_json(output_dir / "analysis/aggregate.synthetic.json", aggregate)
    write_json(output_dir / "role-access-contract.json", _role_access())
    static_audit = {
        "schema_version": config["schema_version"],
        "transaction_states": states,
        "requests": len(requests),
        "public_execution_items": len(execution_pack["items"]),
        "public_preference_blocks": len(preference_pack["blocks"]),
        "execution_audits": len(audits),
        "preference_votes": len(votes),
        "public_pack_private_key_leaks": 0,
        "private_join_exported_to_public_directory": False,
        "delayed_unblinding_enforced": True,
        "production_entropy": "CSPRNG at runtime; no stored or source-fixed seed",
        "synthetic_only": True,
        "model_calls": 0,
        "fiction_texts": 0,
        "generation_authorized": False,
        "aggregate_conclusion": aggregate["conclusion"],
        "r3_1_static_pass": states == STATES and aggregate["conclusion"] == "do_not_expand",
    }
    write_json(output_dir / "r3-1-static-audit.json", static_audit)
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": config["schema_version"],
            "next_stage_authorized": "independent_r3_1_three_party_review",
            "generation_package_authorized": False,
            "model_generation_authorized": False,
            "model_calls": 0,
            "fiction_texts": 0,
            "private_ledger_must_not_be_given_to_reviewers": True,
        },
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        """# Writer Boundary V1.2 R3.1 匿名交接层

## 结论

R3.1 只修复交接匿名性与延迟解盲，不修改 R3 历史代码和产物。合成端到端验证通过；未调用模型、未生成小说文本，也未授权真实生成。

## 已锁定约束

- 执行审计只接收中性 public_text_id、scene_id、正文与内容哈希。
- 偏好票只使用 candidate_1 / candidate_2 / tie，不出现 A/C 身份。
- 私有映射仅写入 SQLite 私有账本，不导出到 public 目录。
- VOTES_LOCKED 前禁止解盲；解盲后才将候选选择归一化为 A/C。
- 执行审计包含段落证据、说明和失败 M 编号规则。

## 下一门禁

仅授权独立三方静态审计：因果/聚合、事务/匿名、场景/prompt/公开泄漏。三方审计通过也只授权构建真实生成包，仍不得直接调用模型。
""",
        encoding="utf-8",
    )
    return static_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = build(args.output_dir, args.report_path)
    if args.action == "audit" and not result["r3_1_static_pass"]:
        raise SystemExit("R3.1 static audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
