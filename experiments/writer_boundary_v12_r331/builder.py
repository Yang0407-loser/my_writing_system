from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes
from experiments.writer_boundary_v12_r33.builder import build as build_r33


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "outputs/writer-boundary-v1-2-r3-3-1"
DEFAULT_REPORT = ROOT / "reports/writer-boundary-v1-2-r3-3-1-delivery-byte-fix-2026-07-30.md"
R33_ROOT = ROOT / "outputs/writer-boundary-v1-2-r3-3"
R33_HISTORY = (
    R33_ROOT / "r3-3-static-audit.json",
    R33_ROOT / "manifest.json",
    R33_ROOT / "private/ledger/r3-3-synthetic.sqlite",
)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_json_bytes(path: Path, value: Any) -> bytes:
    raw = json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rebuild_delivery(
    *,
    source_package: Path,
    target_root: Path,
    reviewer_id: str,
) -> dict[str, Any]:
    package = load_json(source_package)
    relative_package = f"deliveries/{reviewer_id}/package.json"
    package_path = target_root / relative_package
    package_raw = write_json_bytes(package_path, package)
    manifest = {
        "schema_version": "1.2-r3.3.1-delivery-manifest",
        "base_experiment_id": "writer-boundary-v1-2-r3-3",
        "delivery_layer_id": "writer-boundary-v1-2-r3-3-1",
        "reviewer_id": reviewer_id,
        "dispatch_sha256": package["dispatch_sha256"],
        "required_files": [
            {
                "path": relative_package,
                "sha256": digest_bytes(package_raw),
                "bytes": len(package_raw),
            }
        ],
        "required_acceptance_validator": (
            "ExecutionAudit: validate_audits(); "
            "PreferenceVote: validate_votes(); "
            "Pydantic model validation alone is insufficient."
        ),
        "generation_package_authorized": False,
        "model_call_authorized": False,
    }
    manifest_path = target_root / f"deliveries/{reviewer_id}/manifest.json"
    write_json_bytes(manifest_path, manifest)
    actual = package_path.read_bytes()
    if (
        digest_bytes(actual) != manifest["required_files"][0]["sha256"]
        or len(actual) != manifest["required_files"][0]["bytes"]
    ):
        raise ValueError("on-disk delivery bytes do not match manifest")
    return {
        "reviewer_id": reviewer_id,
        "package_path": relative_package,
        "package_sha256": digest_bytes(actual),
        "package_bytes": len(actual),
        "manifest_path": f"deliveries/{reviewer_id}/manifest.json",
        "on_disk_match": True,
    }


def build(output_dir: Path = DEFAULT_OUTPUT, report_path: Path = DEFAULT_REPORT) -> dict[str, Any]:
    history_before = {str(path): digest_bytes(path.read_bytes()) for path in R33_HISTORY}
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        base_output = temporary_root / "r33-base"
        build_r33(base_output, temporary_root / "r33-base-report.md")
        source_deliveries = sorted((base_output / "deliveries").glob("*/package.json"))
        if len(source_deliveries) != 4:
            raise ValueError("R3.3 base build did not produce four deliveries")
        delivery_checks = [
            rebuild_delivery(
                source_package=source,
                target_root=output_dir,
                reviewer_id=source.parent.name,
            )
            for source in source_deliveries
        ]
        for relative in (
            "analysis/aggregate.synthetic.json",
            "review/audits.synthetic.json",
            "review/votes.synthetic.json",
            "private/checkpoints.synthetic.json",
            "private/ledger/r3-3-synthetic.sqlite",
        ):
            source = base_output / relative
            target = output_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    history_after = {str(path): digest_bytes(path.read_bytes()) for path in R33_HISTORY}
    if history_before != history_after:
        raise ValueError("R3.3 historical artifacts changed")
    runbook = {
        "schema_version": "1.2-r3.3.1-review-acceptance-runbook",
        "execution_audit_acceptance": [
            "Parse every response with ExecutionAudit.model_validate.",
            "Then call validate_audits(audits, package=recipient_package).",
            "Reject if either step fails; model validation alone is not acceptance."
        ],
        "preference_vote_acceptance": [
            "Parse every response with PreferenceVote.model_validate.",
            "Then call validate_votes(votes, packages=recipient_packages).",
            "Reject if either step fails; model validation alone is not acceptance."
        ],
        "delivery_verification": [
            "Read package.json as raw bytes.",
            "Compare exact byte length and SHA-256 with manifest required_files.",
            "Do not normalize CRLF/LF during verification."
        ],
        "generation_package_authorized": False,
        "model_call_authorized": False,
    }
    write_json_bytes(output_dir / "review-acceptance-runbook.json", runbook)
    static = {
        "schema_version": "1.2-r3.3.1",
        "base_layer": "writer-boundary-v1-2-r3-3",
        "delivery_count": len(delivery_checks),
        "all_on_disk_hashes_match": all(item["on_disk_match"] for item in delivery_checks),
        "delivery_checks": delivery_checks,
        "write_mode": "binary UTF-8 exact bytes",
        "newline_normalization_during_verification": False,
        "kernel_validator_runbook_present": True,
        "r3_3_history_unchanged": history_before == history_after,
        "synthetic_only": True,
        "model_calls": 0,
        "fiction_texts": 0,
        "generation_authorized": False,
        "r3_3_1_static_pass": (
            len(delivery_checks) == 4
            and all(item["on_disk_match"] for item in delivery_checks)
            and history_before == history_after
        ),
    }
    write_json_bytes(output_dir / "r3-3-1-static-audit.json", static)
    write_json_bytes(
        output_dir / "manifest.json",
        {
            "schema_version": "1.2-r3.3.1",
            "next_stage_authorized": "targeted_delivery_byte_independent_reaudit",
            "generation_package_build_authorized": False,
            "real_generation_authorized": False,
            "model_call_authorized": False,
            "model_calls": 0,
            "fiction_texts": 0,
        },
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(
        """# Writer Boundary V1.2 R3.3.1 Delivery Byte 修订

## 结论

R3.3.1 只修复 Windows CRLF 导致的 delivery manifest hash/bytes 漂移，并补充 kernel validator 验收规则。R3.3 历史代码和产物未修改。

## 修订

- package 与 manifest 均以 `write_bytes()` 写入确定的 UTF-8 字节。
- manifest 的 hash 和 bytes 直接来自实际写入的同一字节数组。
- 四份 package 写入后重新读取磁盘原始字节并逐项核验。
- 验证时禁止 CRLF/LF 归一化。
- runbook 明确：Pydantic 只负责结构解析；audit/vote 必须继续经过 `validate_audits()` / `validate_votes()` 才能接受。

## 授权

当前只授权 delivery-byte 定向独立复审，不授权 generation package build、真实生成或模型调用。
""".encode("utf-8")
    )
    return static


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "audit"])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    result = build(args.output_dir, args.report_path)
    if args.action == "audit" and not result["r3_3_1_static_pass"]:
        raise SystemExit("R3.3.1 static audit failed")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
