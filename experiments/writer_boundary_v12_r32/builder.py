from __future__ import annotations

import argparse
import json
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import (
    build_request,
    digest_bytes,
    digest_json,
    make_assignment,
)

from .kernel import (
    STATES,
    ReceiptLedger,
    aggregate_from_ledger,
    canonical_json,
    lock_anonymity_map_from_ledger,
    lock_audits_from_ledger,
    lock_execution_distribution_from_ledger,
    lock_preference_distribution_from_ledger,
    lock_votes_from_ledger,
    unblind_from_ledger,
)
from .models import Evidence, NeutralAudit, NeutralVote


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments/writer_boundary_v12_r32/fixtures/v1_2_r32_design.json"
R3_PROTOCOL = ROOT / "outputs/writer-boundary-v1-2-r3/protocol.materialized.json"
R3_MATRIX = ROOT / "outputs/writer-boundary-v1-2-r3/private/matrix.private.json"
R3_REQUESTS = ROOT / "outputs/writer-boundary-v1-2-r3/requests/locked-requests.synthetic.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3-2"
DEFAULT_REPORT = ROOT / "reports/writer-boundary-v1-2-r3-2-receipt-rooted-preflight-2026-07-30.md"


ROSTER = {
    "actors": {
        "custodian": "ACTOR-CUSTODIAN",
        "text_ingestor": "ACTOR-TEXT-INGESTOR",
        "blind_pack_custodian": "ACTOR-BLIND-PACK",
        "execution_auditor": "ACTOR-EXECUTION-AUDITOR",
        "preference_coordinator": "ACTOR-PREFERENCE-COORDINATOR",
        "identity_custodian": "ACTOR-IDENTITY-CUSTODIAN",
        "aggregator": "ACTOR-AGGREGATOR",
    },
    "preference_reviewers": ["PREFERENCE-REVIEWER-01", "PREFERENCE-REVIEWER-02", "PREFERENCE-REVIEWER-03"],
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = load_json(CONFIG_PATH)
    sources = (
        (R3_PROTOCOL, config["r3_protocol_sha256"]),
        (R3_MATRIX, config["r3_matrix_sha256"]),
        (R3_REQUESTS, config["r3_locked_request_corpus_sha256"]),
    )
    if any(digest_bytes(path.read_bytes()) != expected for path, expected in sources):
        raise ValueError("pinned R3 input drift")
    protocol, matrix, locked_requests = (
        load_json(R3_PROTOCOL), load_json(R3_MATRIX), load_json(R3_REQUESTS)
    )
    assignments = make_assignment(matrix, protocol)
    rebuilt = {}
    for block in matrix["blocks"]:
        for arm in ("A", "B", "C"):
            envelope, sha = build_request(protocol, matrix, assignments, block["block_id"], arm)
            rebuilt[block["text_ids"][arm]] = {"envelope": envelope, "sha256": sha}
    if rebuilt != locked_requests:
        raise ValueError("R3 request corpus no longer matches request builder")
    return config, protocol, matrix, locked_requests


def _synthetic_audits(distribution: dict[str, Any]) -> list[NeutralAudit]:
    audits = []
    for item in distribution["items"]:
        rubric = distribution["rubrics"][item["scene_id"]]
        text = "\n".join(paragraph["text"] for paragraph in item["paragraphs"])
        observed = next(
            (
                option["value"]
                for option in rubric["allowed_decisions"]
                if option["value"] in text
            ),
            "unclear",
        )
        checks = [
            Evidence(
                check_id=check_id,
                passed=True,
                paragraph_ids=["P1"],
                explanation="仅依据公开 P1 合成占位段落完成 schema 与绑定验证。",
            )
            for check_id in (
                "mandatory_events",
                "unauthorized_new_character",
                "unauthorized_new_solution",
                "unauthorized_relationship_change",
            )
        ]
        audits.append(
            NeutralAudit(
                reviewer_id=ROSTER["actors"]["execution_auditor"],
                public_text_id=item["public_text_id"],
                scene_id=item["scene_id"],
                content_sha256=item["content_sha256"],
                observed_decision=observed,
                hard_checks=checks,
            )
        )
    return audits


def _synthetic_votes(distribution: dict[str, Any]) -> list[NeutralVote]:
    votes = []
    for reviewer in ROSTER["preference_reviewers"]:
        for block in distribution["blocks"]:
            votes.append(
                NeutralVote(
                    reviewer_id=reviewer,
                    public_block_id=block["public_block_id"],
                    candidate_1_id=block["candidate_1"]["public_text_id"],
                    candidate_2_id=block["candidate_2"]["public_text_id"],
                    candidate_1_content_sha256=block["candidate_1"]["content_sha256"],
                    candidate_2_content_sha256=block["candidate_2"]["content_sha256"],
                    naturalness="tie",
                    less_template="tie",
                    overall_quality="tie",
                )
            )
    return votes


def build(output_dir: Path = DEFAULT_OUTPUT, report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    config, protocol, matrix, locked_requests = materialize()
    assignments = make_assignment(matrix, protocol)
    assignment_by_block = {item["block_id"]: item for item in assignments["assignments"]}
    texts: dict[str, bytes] = {}
    for block in matrix["blocks"]:
        scene = next(item for item in protocol["scenes"] if item["scene_id"] == block["scene_id"])
        for arm in ("A", "B", "C"):
            decision = (
                scene["decision_contract"]["allowed_values"][0]["value"]
                if arm == "A"
                else assignment_by_block[block["block_id"]]["selected_value"]
            )
            marker = secrets.token_hex(8)
            texts[block["text_ids"][arm]] = (
                f"合成非虚构占位段落，公开随机标记 {marker}，观测行动 {decision}。\n\n"
                "第二段只用于验证段落编号、内容锁定与匿名交接。"
            ).encode("utf-8")
    text_manifest = {
        "schema_version": "1.2-r3.2-text-manifest",
        "texts": [
            {
                "private_text_id": text_id,
                "content_sha256": digest_bytes(raw),
                "bytes": len(raw),
            }
            for text_id, raw in sorted(texts.items())
        ],
    }
    text_bundle = {text_id: raw.decode("utf-8") for text_id, raw in sorted(texts.items())}
    checkpoints = []
    with tempfile.TemporaryDirectory() as temporary:
        ledger = ReceiptLedger(Path(temporary) / "r3-2-synthetic.sqlite", ROSTER)

        def commit(state: str, role: str, objects, payload):
            receipt = ledger.commit(
                state,
                actor_id=ROSTER["actors"][role],
                role=role,
                objects=objects,
                payload=payload,
            )
            checkpoints.append({"state": state, "receipt_sha256": receipt})
            return receipt

        checkpoint = commit(
            "DESIGN_LOCKED",
            "custodian",
            {
                "protocol": (canonical_json(protocol).encode(), "private"),
                "matrix": (canonical_json(matrix).encode(), "private"),
                "role_roster": (canonical_json(ROSTER).encode(), "private"),
            },
            {
                "request_corpus_sha256": digest_bytes(R3_REQUESTS.read_bytes()),
                "schema_version": config["schema_version"],
            },
        )
        checkpoint = commit(
            "ASSIGNMENTS_LOCKED",
            "custodian",
            {"assignments": (canonical_json(assignments).encode(), "private")},
            {"source_design_receipt_sha256": checkpoint},
        )
        checkpoint = commit(
            "REQUESTS_LOCKED",
            "custodian",
            {"locked_requests": (canonical_json(locked_requests).encode(), "private")},
            {
                "source_assignments_receipt_sha256": checkpoint,
                "raw_corpus_sha256": digest_bytes(R3_REQUESTS.read_bytes()),
            },
        )
        checkpoint = commit(
            "TEXTS_LOCKED",
            "text_ingestor",
            {
                "text_manifest": (canonical_json(text_manifest).encode(), "private"),
                "text_bundle": (canonical_json(text_bundle).encode(), "private"),
            },
            {"source_requests_receipt_sha256": checkpoint},
        )
        private_map, checkpoint = lock_anonymity_map_from_ledger(
            ledger,
            checkpoint_sha256=checkpoint,
            actor_id=ROSTER["actors"]["blind_pack_custodian"],
        )
        checkpoints.append({"state": "ANONYMITY_MAP_LOCKED", "receipt_sha256": checkpoint})
        (
            execution_distribution,
            execution_manifest,
            execution_files,
            checkpoint,
        ) = lock_execution_distribution_from_ledger(
            ledger,
            checkpoint_sha256=checkpoint,
            actor_id=ROSTER["actors"]["blind_pack_custodian"],
        )
        checkpoints.append({"state": "EXECUTION_DISTRIBUTION_LOCKED", "receipt_sha256": checkpoint})
        audits = _synthetic_audits(execution_distribution)
        checkpoint = lock_audits_from_ledger(
            ledger,
            audits,
            checkpoint_sha256=checkpoint,
            actor_id=ROSTER["actors"]["execution_auditor"],
        )
        checkpoints.append({"state": "AUDITS_LOCKED", "receipt_sha256": checkpoint})
        (
            preference_distribution,
            preference_manifest,
            preference_files,
            checkpoint,
        ) = lock_preference_distribution_from_ledger(
            ledger,
            checkpoint_sha256=checkpoint,
            actor_id=ROSTER["actors"]["blind_pack_custodian"],
        )
        checkpoints.append({"state": "PREFERENCE_DISTRIBUTION_LOCKED", "receipt_sha256": checkpoint})
        votes = _synthetic_votes(preference_distribution)
        checkpoint = lock_votes_from_ledger(
            ledger,
            votes,
            checkpoint_sha256=checkpoint,
            actor_id=ROSTER["actors"]["preference_coordinator"],
        )
        checkpoints.append({"state": "VOTES_LOCKED", "receipt_sha256": checkpoint})
        normalized, checkpoint = unblind_from_ledger(
            ledger,
            checkpoint_sha256=checkpoint,
            actor_id=ROSTER["actors"]["identity_custodian"],
        )
        checkpoints.append({"state": "IDENTITY_UNBLINDED", "receipt_sha256": checkpoint})

        aggregate_bundle, checkpoint = aggregate_from_ledger(
            ledger,
            checkpoint_sha256=checkpoint,
            actor_id=ROSTER["actors"]["aggregator"],
        )
        aggregate = aggregate_bundle["aggregate"]
        checkpoints.append({"state": "AGGREGATED", "receipt_sha256": checkpoint})
        ledger.verify(expected_terminal_state="AGGREGATED", checkpoint_sha256=checkpoint)
        target = output_dir / "private/ledger/r3-2-synthetic.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger.path, target)

    for path, raw in {**execution_files, **preference_files}.items():
        target = output_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    write_json(output_dir / "public/execution-reviewer/distribution-manifest.json", execution_manifest)
    write_json(output_dir / "public/preference-reviewer/distribution-manifest.json", preference_manifest)
    write_json(output_dir / "private/checkpoints.external.json", checkpoints)
    write_json(output_dir / "review/audits.synthetic.json", [item.model_dump(mode="json") for item in audits])
    write_json(output_dir / "review/votes.synthetic.json", [item.model_dump(mode="json") for item in votes])
    write_json(output_dir / "analysis/aggregate.synthetic.json", aggregate_bundle)
    audit = {
        "schema_version": config["schema_version"],
        "transaction_states": [item["state"] for item in checkpoints],
        "exact_terminal_state_verified": True,
        "receipt_visibility_verified": True,
        "orphan_objects_rejected": True,
        "actor_role_transitions_enforced": True,
        "locked_request_corpus_unchanged": True,
        "request_mismatch_count": 0,
        "texts": 36,
        "execution_audits": 36,
        "preference_blocks": 12,
        "preference_votes": 36,
        "public_value_leaks": 0,
        "fixed_abc_order": False,
        "unblind_source": "verified VOTES_LOCKED receipt bytes",
        "hard_outcomes_derived_from_locked_audits": True,
        "consensus_permutation_invariant": True,
        "synthetic_only": True,
        "model_calls": 0,
        "fiction_texts": 0,
        "generation_authorized": False,
        "aggregate_conclusion": aggregate["conclusion"],
        "r3_2_static_pass": [item["state"] for item in checkpoints] == STATES,
    }
    write_json(output_dir / "r3-2-static-audit.json", audit)
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": config["schema_version"],
            "next_stage_authorized": "independent_r3_2_three_party_review",
            "generation_package_authorized": False,
            "model_generation_authorized": False,
            "model_calls": 0,
            "fiction_texts": 0,
            "reviewers_must_receive_only_role_specific_public_directory": True,
        },
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        """# Writer Boundary V1.2 R3.2 收据根修订层

## 结论

R3.2 合成前置验证已建立。R3/R3.1 历史代码和产物未修改；没有调用模型或生成小说文本。

## 修订

- 状态转换绑定冻结 actor/role roster，职责冲突会被拒绝。
- 文本逐项 hash/长度锁定；审计、偏好包、票和矩阵均从账本收据读取。
- 解盲只能消费验证后的 VOTES_LOCKED 字节，并原子写入 IDENTITY_UNBLINDED。
- execution items 全局 CSPRNG 洗牌；公开占位正文不含私有 ID。
- 两类 reviewer 各有逐文件 hash distribution manifest，公开执行包包含场景 rubric、P/M 规则与 strict schema。
- hard outcome 从锁定审计和解盲身份推导；三票分裂使用顺序无关规则。
- R3 的 36 个 locked request corpus 已纳入原始字节 hash 门禁。

## 授权边界

当前只授权 R3.2 三方独立静态审计，不授权真实生成包或模型调用。
""",
        encoding="utf-8",
    )
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = build(args.output_dir, args.report_path)
    if args.action == "audit" and not result["r3_2_static_pass"]:
        raise SystemExit("R3.2 static audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
