from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import R341IndependentReview


ROOT = Path(__file__).resolve().parents[2]
R34_REVIEW_DIR = (
    ROOT
    / "outputs"
    / "writer-boundary-v1-2-r3-4-generation-package"
    / "review"
)
PINNED_REVIEW_HASHES = {
    "reviewer-01-independent.private.json": "6a17e99506af60d129da3d97fead4f0333fd605793122b759446e8d44689f146",
    "reviewer-02-independent.private.json": "abc59c5c289190606f5f04daf9ace43afc88c7814135488943e41058599d005d",
    "reviewer-03-independent.private.json": "90e0c61b0653f90ee985b3c424291ba0d7aaff770ea4f32a91c2fe106cc9604c",
}


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def validate_pinned_reviews() -> dict[str, str]:
    actual: dict[str, str] = {}
    for name, expected in PINNED_REVIEW_HASHES.items():
        digest = sha256((R34_REVIEW_DIR / name).read_bytes())
        if digest != expected:
            raise ValueError(f"historical reviewer output changed: {name}")
        actual[name] = digest
    return actual


def contract() -> dict[str, Any]:
    return {
        "schema_version": "1.2-r3.4.1-review-contract",
        "correction_scope": "review_protocol_only",
        "protocol_failure": {
            "classification": "invalid_review_protocol",
            "cause": (
                "R3.4 used one field both as a reviewer recommendation and as an "
                "effective authorization flag."
            ),
            "reviewer_fault_assigned": False,
            "historical_reviews_modified": False,
        },
        "semantic_split": {
            "recommendation": {
                "field": "recommendation.capability_probe_layer_build_recommended",
                "reviewer_may_choose": [True, False],
                "effect": "advisory_only",
            },
            "authorization": {
                "field": "authorization.capability_probe_layer_build_authorized",
                "required_literal": False,
                "effect": "no_authority_expansion",
            },
        },
        "frozen_authorization": {
            "authorized_scope": "independent_r3_4_generation_package_audit_only",
            "capability_probe_layer_build_authorized": False,
            "capability_probe_call_authorized": False,
            "real_generation_authorized": False,
            "model_call_authorized": False,
        },
        "reaudit_policy": {
            "all_three_targeted_reviews_required": True,
            "reuse_old_verdict_as_new_vote": False,
            "reuse_old_findings_as_context": False,
            "model_calls_allowed": False,
            "network_requests_allowed": False,
        },
    }


def instruction_snapshot() -> str:
    return """Writer Boundary V1.2 R3.4.1 定向独立复审合同

本轮只复核 R3.4 generation package 是否可建议进入 capability-probe layer
build；不得修改实现，不得访问其他 reviewer 输出，不得调用网络或模型。

必须使用随本合同发布的 JSON Schema 验证最终 JSON。

字段语义：

1. recommendation.capability_probe_layer_build_recommended
   - 这是 reviewer 的审计建议，可以填 true 或 false。
   - 它不产生任何实际授权。
2. authorization
   - 这是当前冻结权限，不是 reviewer 的建议。
   - authorized_scope 必须严格为
     independent_r3_4_generation_package_audit_only。
   - 四个 *_authorized 字段必须全部严格为 false。
   - 无论 verdict 或 recommendation 如何，都不得改成 true。

任意 P0/P1 必须 verdict=fail；fail 时 recommendation 必须为 false。
P2/P3 不自动导致 fail。severity_counts 必须从 findings 机械计算。

当前阶段不授权 capability probe 调用、真实生成、模型调用，也不授权
capability-probe layer build。只有三份有效定向复审完成后的聚合器，才可以
决定是否建议用户授权下一版本的 layer build。
"""


def build(output_dir: Path, report_path: Path) -> dict[str, Any]:
    pinned = validate_pinned_reviews()
    output_dir.mkdir(parents=True, exist_ok=True)
    schema = R341IndependentReview.model_json_schema()
    write_json(output_dir / "review-schema.json", schema)
    write_json(output_dir / "review-contract.json", contract())
    (output_dir / "review-instruction-snapshot.txt").write_bytes(
        instruction_snapshot().encode("utf-8")
    )
    files = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            raw = path.read_bytes()
            files[path.name] = {"sha256": sha256(raw), "bytes": len(raw)}
    manifest = {
        "schema_version": "1.2-r3.4.1-protocol-correction-manifest",
        "historical_review_hashes": pinned,
        "files": files,
        "model_calls": 0,
        "network_requests": 0,
        "capability_probe_layer_build_authorized": False,
        "capability_probe_call_authorized": False,
        "real_generation_authorized": False,
        "model_call_authorized": False,
        "authorized_next_stage": "independent_r3_4_1_targeted_reaudit",
    }
    write_json(output_dir / "manifest.json", manifest)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# Writer Boundary V1.2 R3.4.1 审计协议纠错\n\n"
        "R3.4 的 reviewer 建议与实际授权共用了同一个字段，导致三个独立会话"
        "对 `false` 的语义产生不同解释。本层将该问题定性为 "
        "`invalid_review_protocol`，不归责 Reviewer 03。\n\n"
        "R3.4.1 已把 advisory recommendation 与 frozen authorization 拆开，"
        "并通过 Pydantic `Literal[False]` 锁死四个授权字段。三份历史 reviewer "
        "文件按 SHA-256 固定且未修改。\n\n"
        "本层没有调用网络、模型或 capability probe；当前只允许进行三份新的"
        " R3.4.1 定向独立复审。\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


if __name__ == "__main__":
    build(
        ROOT / "outputs" / "writer-boundary-v1-2-r3-4-1-review-protocol",
        ROOT
        / "reports"
        / "writer-boundary-v1-2-r3-4-1-review-protocol-correction-2026-07-30.md",
    )
