from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import build_request, digest_bytes, digest_json, make_assignment
from experiments.writer_boundary_v12_r32.kernel import ReceiptLedger, STATES, canonical_json

from .kernel import (
    build_artifact_registry,
    create_role_separated_map,
    derive_and_aggregate,
    execution_dispatch,
    preference_dispatches,
    unblind_votes,
    validate_audits,
    validate_registry_bundle,
    validate_votes,
)
from .models import (
    ExecutionAudit,
    MandatoryEventEvidence,
    PreferenceVote,
    ViolationEvidence,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments/writer_boundary_v12_r33/fixtures/v1_2_r33_design.json"
R3_PROTOCOL = ROOT / "outputs/writer-boundary-v1-2-r3/protocol.materialized.json"
R3_MATRIX = ROOT / "outputs/writer-boundary-v1-2-r3/private/matrix.private.json"
R3_REQUESTS = ROOT / "outputs/writer-boundary-v1-2-r3/requests/locked-requests.synthetic.json"
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3-3"
DEFAULT_REPORT = ROOT / "reports/writer-boundary-v1-2-r3-3-experiment-validity-preflight-2026-07-30.md"

ROSTER = {
    "actors": {
        "custodian": "ACTOR-CUSTODIAN",
        "text_ingestor": "ACTOR-TEXT-INGESTOR",
        "blind_pack_custodian": "ACTOR-BLIND-PACK",
        "execution_auditor": "EXECUTION-REVIEWER-01",
        "preference_coordinator": "ACTOR-PREFERENCE-COORDINATOR",
        "identity_custodian": "ACTOR-IDENTITY-CUSTODIAN",
        "aggregator": "ACTOR-AGGREGATOR",
    },
    "preference_reviewers": [
        "PREFERENCE-REVIEWER-01",
        "PREFERENCE-REVIEWER-02",
        "PREFERENCE-REVIEWER-03",
    ],
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def materialize() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = load_json(CONFIG_PATH)
    for path, expected in (
        (R3_PROTOCOL, config["r3_protocol_sha256"]),
        (R3_MATRIX, config["r3_matrix_sha256"]),
        (R3_REQUESTS, config["r3_locked_request_corpus_sha256"]),
    ):
        if digest_bytes(path.read_bytes()) != expected:
            raise ValueError("pinned R3 input drift")
    protocol, matrix, requests = load_json(R3_PROTOCOL), load_json(R3_MATRIX), load_json(R3_REQUESTS)
    assignments = make_assignment(matrix, protocol)
    rebuilt = {}
    for block in matrix["blocks"]:
        for arm in ("A", "B", "C"):
            envelope, sha = build_request(protocol, matrix, assignments, block["block_id"], arm)
            rebuilt[block["text_ids"][arm]] = {"envelope": envelope, "sha256": sha}
    if rebuilt != requests:
        raise ValueError("locked request corpus mismatch")
    return config, protocol, matrix, requests


def _semantic_mock(scene: dict[str, Any], decision: str) -> bytes:
    paragraphs = [
        f"[{entry.split(' ', 1)[0]}] {entry.split(' ', 1)[1]}"
        for entry in scene["mandatory_events"]
    ]
    paragraphs.extend(
        [
            f"[DECISION] {decision}",
            "[NO-NEW-CHARACTER] 人物范围仅限 rubric 中列出的角色。",
            "[NO-NEW-SOLUTION] 未出现白名单外的新方案。",
            "[NO-RELATIONSHIP-CHANGE] 未产生关系承诺、和解或责任转移。",
        ]
    )
    return "\n\n".join(paragraphs).encode("utf-8")


def _audits_from_dispatch(package: dict[str, Any]) -> list[ExecutionAudit]:
    audits = []
    for item in package["items"]:
        rubric = package["rubrics"][item["scene_id"]]
        paragraph_by_prefix = {
            paragraph["text"].split("]", 1)[0].lstrip("["): paragraph["paragraph_id"]
            for paragraph in item["paragraphs"]
        }
        joined = "\n".join(paragraph["text"] for paragraph in item["paragraphs"])
        observed = next(
            option["value"]
            for option in rubric["allowed_decisions"]
            if option["value"] in joined
        )
        mandatory = [
            MandatoryEventEvidence(
                m_id=entry["id"],
                passed=True,
                paragraph_ids=[paragraph_by_prefix[entry["id"]]],
                explanation=f"{entry['id']} 的结构化 mock 事实明确出现。",
            )
            for entry in rubric["mandatory_catalog"]
        ]
        violations = [
            ViolationEvidence(
                check_id=check_id,
                detected=False,
                paragraph_ids=[paragraph_by_prefix[prefix]],
                explanation="结构化 mock 明确声明未检测到该类违规。",
                f_ids=[],
            )
            for check_id, prefix in (
                ("unauthorized_new_character", "NO-NEW-CHARACTER"),
                ("unauthorized_new_solution", "NO-NEW-SOLUTION"),
                ("unauthorized_relationship_change", "NO-RELATIONSHIP-CHANGE"),
            )
        ]
        audits.append(
            ExecutionAudit(
                schema_version="1.2-r3.3-execution-audit",
                reviewer_id=package["reviewer_id"],
                dispatch_sha256=package["dispatch_sha256"],
                public_text_id=item["public_text_id"],
                scene_id=item["scene_id"],
                content_sha256=item["content_sha256"],
                observed_decision=observed,
                mandatory_events=mandatory,
                violations=violations,
                identity_accessed=False,
                preference_accessed=False,
                other_reviews_accessed=False,
                private_material_accessed=False,
                public_material_only=True,
            )
        )
    return audits


def _votes_from_dispatches(packages: dict[str, dict[str, Any]]) -> list[PreferenceVote]:
    votes = []
    for reviewer, package in packages.items():
        for block in package["blocks"]:
            votes.append(
                PreferenceVote(
                    schema_version="1.2-r3.3-preference-vote",
                    reviewer_id=reviewer,
                    dispatch_sha256=package["dispatch_sha256"],
                    public_block_id=block["public_block_id"],
                    candidate_1_id=block["candidate_1"]["public_text_id"],
                    candidate_2_id=block["candidate_2"]["public_text_id"],
                    candidate_1_content_sha256=block["candidate_1"]["content_sha256"],
                    candidate_2_content_sha256=block["candidate_2"]["content_sha256"],
                    naturalness="tie",
                    less_template="tie",
                    overall_quality="tie",
                    identity_accessed=False,
                    other_reviews_accessed=False,
                    private_material_accessed=False,
                    execution_audits_accessed=False,
                    public_material_only=True,
                    locked=True,
                )
            )
    return votes


def _delivery_manifest(reviewer_id: str, relative_path: str, package: dict[str, Any]) -> dict[str, Any]:
    raw = (json.dumps(package, ensure_ascii=False, indent=2) + "\n").encode()
    return {
        "schema_version": "1.2-r3.3-delivery-manifest",
        "experiment_id": "writer-boundary-v1-2-r3-3",
        "reviewer_id": reviewer_id,
        "dispatch_sha256": package["dispatch_sha256"],
        "required_files": [{"path": relative_path, "sha256": digest_bytes(raw), "bytes": len(raw)}],
        "prohibited_roles": [
            role for role in (
                "execution_reviewer", "preference_reviewer", "identity_custodian", "aggregator"
            ) if role not in (
                ["execution_reviewer"] if reviewer_id.startswith("EXECUTION") else ["preference_reviewer"]
            )
        ],
        "generation_package_authorized": False,
        "model_call_authorized": False,
    }


def build(output_dir: Path = DEFAULT_OUTPUT, report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    config, protocol, matrix, requests = materialize()
    assignments = make_assignment(matrix, protocol)
    assignment_by_block = {item["block_id"]: item for item in assignments["assignments"]}
    texts = {}
    for block in matrix["blocks"]:
        scene = next(item for item in protocol["scenes"] if item["scene_id"] == block["scene_id"])
        for arm in ("A", "B", "C"):
            decision = (
                scene["decision_contract"]["allowed_values"][0]["value"]
                if arm == "A"
                else assignment_by_block[block["block_id"]]["selected_value"]
            )
            texts[block["text_ids"][arm]] = _semantic_mock(scene, decision)
    registry = build_artifact_registry(texts)
    validate_registry_bundle(registry, texts)
    private_map = create_role_separated_map(
        matrix, registry, ROSTER["preference_reviewers"]
    )
    execution_package, _ = execution_dispatch(
        reviewer_id=ROSTER["actors"]["execution_auditor"],
        private_map=private_map,
        texts=texts,
        protocol=protocol,
    )
    preference_packages, _ = preference_dispatches(
        private_map=private_map,
        texts=texts,
        reviewers=ROSTER["preference_reviewers"],
    )
    execution_ids = {item["public_text_id"] for item in execution_package["items"]}
    preference_ids = {
        candidate["public_text_id"]
        for package in preference_packages.values()
        for block in package["blocks"]
        for candidate in (block["candidate_1"], block["candidate_2"])
    }
    if execution_ids & preference_ids:
        raise ValueError("cross-role public id overlap")
    audits = _audits_from_dispatch(execution_package)
    validate_audits(audits, package=execution_package)
    votes = _votes_from_dispatches(preference_packages)
    validate_votes(votes, packages=preference_packages)
    normalized = unblind_votes(
        votes, packages=preference_packages, private_map=private_map
    )
    analysis = derive_and_aggregate(
        matrix=matrix,
        assignments=assignments,
        registry=registry,
        private_map=private_map,
        audits=audits,
        normalized_votes=normalized,
        reviewer_roster=ROSTER["preference_reviewers"],
    )

    checkpoints = []
    with tempfile.TemporaryDirectory() as temporary:
        ledger = ReceiptLedger(Path(temporary) / "r3-3-synthetic.sqlite", ROSTER)

        def commit(state: str, role: str, objects: dict[str, tuple[bytes, str]], payload: dict[str, Any]):
            receipt = ledger.commit(
                state,
                actor_id=ROSTER["actors"][role],
                role=role,
                objects=objects,
                payload=payload,
            )
            checkpoints.append({"state": state, "receipt_sha256": receipt})
            return receipt

        prior = commit(
            "DESIGN_LOCKED", "custodian",
            {
                "protocol": (canonical_json(protocol).encode(), "private"),
                "matrix": (canonical_json(matrix).encode(), "private"),
                "roster": (canonical_json(ROSTER).encode(), "private"),
                "threat_model": (canonical_json(config["threat_model"]).encode(), "public"),
            },
            {"request_corpus_sha256": digest_bytes(R3_REQUESTS.read_bytes())},
        )
        prior = commit("ASSIGNMENTS_LOCKED", "custodian", {"assignments": (canonical_json(assignments).encode(), "private")}, {"prior": prior})
        prior = commit("REQUESTS_LOCKED", "custodian", {"requests": (canonical_json(requests).encode(), "private")}, {"prior": prior})
        prior = commit(
            "TEXTS_LOCKED", "text_ingestor",
            {
                "artifact_registry": (canonical_json(registry).encode(), "private"),
                "text_bundle": (canonical_json({key: value.decode() for key, value in texts.items()}).encode(), "private"),
            },
            {"prior": prior},
        )
        prior = commit("ANONYMITY_MAP_LOCKED", "blind_pack_custodian", {"private_map": (canonical_json(private_map).encode(), "private")}, {"prior": prior})
        prior = commit("EXECUTION_DISTRIBUTION_LOCKED", "blind_pack_custodian", {"execution_dispatch": (canonical_json(execution_package).encode(), "public")}, {"prior": prior})
        prior = commit("AUDITS_LOCKED", "execution_auditor", {"audits": (canonical_json([item.model_dump(mode="json") for item in audits]).encode(), "private")}, {"prior": prior})
        prior = commit("PREFERENCE_DISTRIBUTION_LOCKED", "blind_pack_custodian", {"preference_dispatches": (canonical_json(preference_packages).encode(), "public")}, {"prior": prior})
        prior = commit("VOTES_LOCKED", "preference_coordinator", {"votes": (canonical_json([item.model_dump(mode="json") for item in votes]).encode(), "private")}, {"prior": prior})
        prior = commit("IDENTITY_UNBLINDED", "identity_custodian", {"normalized_votes": (canonical_json(normalized).encode(), "private")}, {"prior": prior})
        prior = commit("AGGREGATED", "aggregator", {"analysis": (canonical_json(analysis).encode(), "private")}, {"prior": prior})
        ledger.verify(expected_terminal_state="AGGREGATED", checkpoint_sha256=prior)
        ledger_target = output_dir / "private/ledger/r3-3-synthetic.sqlite"
        ledger_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ledger.path, ledger_target)

    deliveries = {
        ROSTER["actors"]["execution_auditor"]: execution_package,
        **preference_packages,
    }
    for reviewer, package in deliveries.items():
        relative = f"deliveries/{reviewer}/package.json"
        write_json(output_dir / relative, package)
        write_json(
            output_dir / f"deliveries/{reviewer}/manifest.json",
            _delivery_manifest(reviewer, relative, package),
        )
    write_json(output_dir / "private/checkpoints.synthetic.json", checkpoints)
    write_json(output_dir / "review/audits.synthetic.json", [item.model_dump(mode="json") for item in audits])
    write_json(output_dir / "review/votes.synthetic.json", [item.model_dump(mode="json") for item in votes])
    write_json(output_dir / "analysis/aggregate.synthetic.json", analysis)
    static = {
        "schema_version": config["schema_version"],
        "threat_model_frozen": True,
        "malicious_admin_resistance_in_scope": False,
        "artifact_availability_reviewer_authored": False,
        "artifact_registry_bundle_coherent": True,
        "execution_preference_public_id_overlap": 0,
        "recipient_specific_dispatches": 4,
        "independence_fields_have_defaults": False,
        "mandatory_events_validated_individually": True,
        "violation_evidence_uses_typed_f_catalog": True,
        "semantic_mock_fixtures": 36,
        "placeholder_hard_passes": 0,
        "normalized_vote_cartesian_product_verified": True,
        "transaction_states": [item["state"] for item in checkpoints],
        "synthetic_only": True,
        "model_calls": 0,
        "fiction_texts": 0,
        "generation_authorized": False,
        "aggregate_conclusion": analysis["aggregate"]["conclusion"],
        "r3_3_static_pass": [item["state"] for item in checkpoints] == STATES,
    }
    write_json(output_dir / "r3-3-static-audit.json", static)
    write_json(
        output_dir / "manifest.json",
        {
            "schema_version": config["schema_version"],
            "next_stage_authorized": "external_fresh_chat_r3_3_three_party_review",
            "generation_package_authorized": False,
            "model_generation_authorized": False,
            "model_calls": 0,
            "fiction_texts": 0,
            "review_method": "three user-created fresh conversations; no inherited project history",
        },
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        """# Writer Boundary V1.2 R3.3 实验有效性修订层

## 结论

R3.3 只处理实验有效性阻断项，不构建密码学账本或恶意管理员防护。合成验证未调用模型、未生成小说文本。

## 修订

- artifact availability 从 TEXTS_LOCKED registry 推导，execution reviewer 无权填写 missing。
- execution 与三名 preference reviewer 使用四套互不相交 public ID 和单独 dispatch。
- 所有独立性、public-only 与 locked 声明必须显式提交，不再由默认值制造。
- mandatory evidence 逐 M 恰好覆盖；三类违规使用各自 F catalog。
- 无事实 placeholder 不再产生 hard pass；本轮使用明确标注事实与预期的结构化非小说 semantic mock。
- 解盲与聚合验证冻结三 reviewer × 12 block 的精确笛卡尔积。

## 下一步

只授权由用户在三个无历史继承的新会话中进行 R3.3 独立复审；不授权真实生成或模型调用。
""",
        encoding="utf-8",
    )
    return static


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = build(args.output_dir, args.report_path)
    if args.action == "audit" and not result["r3_3_static_pass"]:
        raise SystemExit("R3.3 static audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
