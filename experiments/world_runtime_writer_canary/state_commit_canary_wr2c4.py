"""WR2-C4 State Commit Canary Phase C2.1: limited real-generation canary.

Single project (saturday-bakery), single task, three consecutive subsections.
Each subsection is generated exactly once (zero retries), then run through the
frozen WR2-C4 extractor -> Validator -> Committer with new-fact creation
enabled.  Commits chain: subsection N+1 commits on top of subsection N's state.
Everything is written only to the canary namespace; production is untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.llm_client import get_llm_client
from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c import _state_payload
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c4 import (
    build_messages,
    parse_semantic_response,
)
from experiments.world_runtime_writer_canary.state_commit_adapter_wr2c4 import (
    to_committable,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve()
EXTRACTOR = ROOT / "experiments/world_runtime_writer_canary/semantic_extractor_wr2c4.py"
PROJECTOR = ROOT / "experiments/world_runtime_writer_canary/semantic_projector_wr2c4.py"
VALIDATOR = ROOT / "experiments/world_runtime_writer_canary/delta_shadow_wr2b.py"
COMMITTER = ROOT / "app/writing/world_runtime_state_committer.py"
ADAPTER = ROOT / "experiments/world_runtime_writer_canary/state_commit_adapter_wr2c4.py"
RUNTIME = ROOT / ".world_runtime_state_commit_canary_runtime" / "c21"
DEFAULT_AUTHORIZATION = RUNTIME / "private/external-execution-authorization.json"
SUBSECTIONS = 3
GEN_PROVIDER = {"temperature": 0.7, "max_tokens": 2400, "transport_retries": 0, "json_mode": False}
JUDGMENT_PROVIDER = {"temperature": 0.0, "max_tokens": 4000, "transport_retries": 0, "json_mode": True}

SCENES = (
    "清晨五点前后，周野在操作间准备第一批面团，林晚在店里处理文章发布和群里的反馈；"
    "世界必须产生至少一个持久变化（时钟/位置/知识/发布等），并与给定状态一致。",
    "五点三十分左右，出现顾客或投递/邮件相关事件；"
    "世界必须产生至少一个持久变化（售出/手递/投递/留档/时钟等），并与给定状态一致。",
    "临近六点开门，处理辞职确认或雇佣状态、发布确认等收尾；"
    "世界必须产生至少一个持久变化，并与给定状态一致。",
)


SYSTEM_PROMPT = """你是中文小说续写模型。只输出小说正文，不加标题、说明、分析或 JSON。
必须严格保持给定世界状态与前置正文的一致性；不得引入正文未支持的越权事件。"""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {
        "runner_sha256": _sha256_file(SOURCE),
        "extractor_sha256": _sha256_file(EXTRACTOR),
        "projector_sha256": _sha256_file(PROJECTOR),
        "validator_sha256": _sha256_file(VALIDATOR),
        "committer_sha256": _sha256_file(COMMITTER),
        "adapter_sha256": _sha256_file(ADAPTER),
    }


def build_generation_messages(
    *,
    state,
    previous_text: str,
    scene_index: int,
) -> list[dict[str, str]]:
    user = (
        "CURRENT_STATE:\n"
        + json.dumps(_state_payload(state), ensure_ascii=False, separators=(",", ":"))
        + "\n\nPREVIOUS_TEXT"
        + (":\n" + previous_text if previous_text else "：本节为故事开头。")
        + "\n\nSCENE:\n"
        + SCENES[scene_index]
        + "\n\n只输出本节中文小说正文，目标 600~900 字。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build(output_dir: Path = RUNTIME) -> dict[str, Any]:
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("C2.1 ledger exists; refusing rebuild")
    gold = build_saturday_bakery_gold_fixture()
    samples = []
    for index in range(SUBSECTIONS):
        messages = build_generation_messages(
            state=gold.state_before,
            previous_text="[PREVIOUS_SUBSECTION]",
            scene_index=index,
        )
        samples.append({
            "subsection": index + 1,
            "scene_index": index,
            "messages": messages,
            "request_hash": _digest({"messages": messages, "provider": GEN_PROVIDER}),
            "status": "pending",
            "attempt_count": 0,
        })
    manifest = {
        "schema_version": "world-runtime-state-commit-canary-c21-manifest-v1",
        "experiment_id": "world-runtime-state-commit-canary-c21",
        "partition_role": "limited_real_canary",
        "project_id": gold.state_before.project_id,
        "initial_revision": gold.state_before.revision,
        "subsection_count": SUBSECTIONS,
        "provider": GEN_PROVIDER,
        "judgment_provider": JUDGMENT_PROVIDER,
        "model_calls_per_subsection": 2,
        "provider_host": "api.deepseek.com",
        "model": settings.WRITER_LLM_MODEL,
        "source_hashes": _source_hashes(),
        "samples": samples,
        "production_behavior_changed": False,
        "state_commit_authorized": False,
    }
    ledger = {
        "schema_version": "world-runtime-state-commit-canary-c21-ledger-v1",
        "samples": {
            sample["subsection"]: {
                "request_hash": sample["request_hash"],
                "status": "pending",
                "attempt_count": 0,
            }
            for sample in samples
        },
    }
    _write_json(output_dir / "private/locked-manifest.json", manifest)
    _write_json(output_dir / "attempt-ledger.json", ledger)
    return manifest


def audit(output_dir: Path = RUNTIME) -> dict[str, Any]:
    issues = []
    manifest_path = output_dir / "private/locked-manifest.json"
    ledger_path = output_dir / "attempt-ledger.json"
    if not manifest_path.exists() or not ledger_path.exists():
        issues.append("manifest_or_ledger_missing")
    manifest = _read_json(manifest_path) if manifest_path.exists() else {}
    ledger = _read_json(ledger_path) if ledger_path.exists() else {}
    for field, expected in manifest.get("source_hashes", {}).items():
        current = _source_hashes().get(field)
        if current != expected:
            issues.append(f"source_hash_mismatch:{field}")
    pending = sum(item["status"] == "pending" for item in ledger.get("samples", {}).values())
    attempts = sum(item["attempt_count"] for item in ledger.get("samples", {}).values())
    outputs = list((output_dir / "private/outputs").glob("*")) if (output_dir / "private/outputs").exists() else []
    if pending != SUBSECTIONS or attempts != 0 or outputs:
        issues.append("ledger_or_outputs_not_pristine")
    if settings.WRITER_WORLD_RUNTIME_MODE != "off":
        issues.append("production_default_not_off")
    return {
        "schema_version": "world-runtime-state-commit-canary-c21-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "pending": pending,
        "attempt_count_total": attempts,
        "output_files": len(outputs),
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
    }


def preflight(
    output_dir: Path = RUNTIME,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = audit(output_dir)
    issues = list(check["issues"])
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    if not authorization_path.exists():
        issues.append("authorization_missing")
        authorization = {}
    else:
        authorization = _read_json(authorization_path)
    expected = {
        "schema_version": "world-runtime-state-commit-canary-c21-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": _sha256_file(output_dir / "private/locked-manifest.json"),
        "runner_source_sha256": manifest["source_hashes"]["runner_sha256"],
        "maximum_requests": SUBSECTIONS * 2,
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }
    for field, value in expected.items():
        if authorization.get(field) != value:
            issues.append(f"authorization.{field}:expected:{value!r}")
    return {
        "schema_version": "world-runtime-state-commit-canary-c21-external-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "model_calls": SUBSECTIONS * 2,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "authorization_sha256": _sha256_file(authorization_path) if authorization_path.exists() else None,
        "state_commit_authorized": False,
    }


def run_once(
    output_dir: Path = RUNTIME,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = preflight(output_dir, authorization_path)
    if not check["ready"]:
        raise RuntimeError("c21_preflight_failed:" + "|".join(check["issues"]))
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read_json(ledger_path)
    gold = build_saturday_bakery_gold_fixture()
    client = get_llm_client(manifest["model"])
    committer = WorldRuntimeStateCommitter()
    records: list[dict[str, Any]] = []
    previous_text = ""
    for sample in manifest["samples"]:
        subsection = sample["subsection"]
        entry = ledger["samples"][str(subsection)]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"c21_refusing_nonpristine_subsection:{subsection}")
        entry.update(status="started", attempt_count=1)
        _write_json(ledger_path, ledger)
        record: dict[str, Any] = {"subsection": subsection, "status": "pending"}
        try:
            gen_messages = build_generation_messages(
                state=gold.state_before,
                previous_text=previous_text,
                scene_index=sample["scene_index"],
            )
            final_text = client.chat_completion(
                gen_messages,
                temperature=GEN_PROVIDER["temperature"],
                max_tokens=GEN_PROVIDER["max_tokens"],
                max_retries=0,
                json_mode=GEN_PROVIDER["json_mode"],
                prompt_name="state_commit_canary_c21_generation_v1",
                completion_metadata_sink=(lambda md, _r=record: _r.update(gen_metadata=md)),
            )
            text_path = output_dir / "private/outputs" / f"S{subsection}.txt"
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(final_text, encoding="utf-8", newline="\n")
            text_hash = hashlib.sha256(final_text.encode("utf-8")).hexdigest()
            record.update(chars=len(final_text), output_hash=text_hash)

            judgment_messages = build_messages(
                text=final_text,
                state_variant="before",
            )
            judgment = client.chat_completion(
                judgment_messages,
                temperature=JUDGMENT_PROVIDER["temperature"],
                max_tokens=JUDGMENT_PROVIDER["max_tokens"],
                max_retries=0,
                json_mode=JUDGMENT_PROVIDER["json_mode"],
                prompt_name="state_commit_canary_c21_judgment_v1",
                completion_metadata_sink=(lambda md, _r=record: _r.update(judgment_metadata=md)),
            )
            judgment_path = output_dir / "private/outputs" / f"S{subsection}.judgment.json"
            judgment_path.write_text(judgment, encoding="utf-8", newline="\n")

            artifact = parse_semantic_response(
                text=final_text,
                response_text=judgment,
                sample_id=f"C21-S{subsection}",
                scene_id="saturday-bakery-canary",
                state_variant="before",
                base_revision=gold.state_before.revision,
            )
            validation = validate_delta_v2(artifact.delta)
            record.update(
                extracted=len(artifact.delta.changes),
                accepted=len(validation.accepted_change_ids),
                rejected=len(validation.rejected_change_ids),
                unresolved=len(validation.unresolved_change_ids),
            )
            if not validation.accepted_change_ids:
                record.update(status="no_commit_no_accepted")
            else:
                committable_delta, committable_validation = to_committable(
                    artifact.delta,
                    validation,
                    project_id=gold.state_before.project_id,
                )
                committed = committer.commit(
                    idempotency_key=f"c21:S{subsection}",
                    before=gold.state_before,
                    delta=committable_delta,
                    validation=committable_validation,
                    final_text_hash=text_hash,
                    task_id="c21-saturday-bakery",
                    section=1,
                    subsection=subsection,
                )
                record.update(
                    status="committed",
                    after_revision=committed.after.revision,
                    ledger_entries=len(committed.ledger.entries),
                    created_facts=[
                        entry.fact_id
                        for entry in committed.ledger.entries
                        if entry.fact_id and entry.fact_id not in {
                            fact.fact_id for fact in gold.state_before.facts
                        }
                    ],
                    commit_id=committed.commit_id,
                    artifact_hash=committed.artifact_hash,
                )
                _write_json(
                    output_dir / "private/commits" / f"S{subsection}.json",
                    committed.model_dump(mode="json"),
                )
            entry.update(status="succeeded")
            previous_text = final_text
        except Exception as exc:
            entry.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
            _write_json(ledger_path, ledger)
            raise
        _write_json(ledger_path, ledger)
        records.append(record)
    summary = {
        "subsections": len(records),
        "model_calls": len(records) * 2,
        "committed": sum(r["status"] == "committed" for r in records),
        "no_commit_no_accepted": sum(r["status"] == "no_commit_no_accepted" for r in records),
        "extracted_changes": sum(r.get("extracted", 0) for r in records),
        "accepted_changes": sum(r.get("accepted", 0) for r in records),
    }
    report = {
        "schema_version": "world-runtime-state-commit-canary-c21-report-v1",
        "partition_role": "limited_real_canary",
        "summary": summary,
        "records": records,
        "human_review_pending": True,
    }
    _write_json(output_dir / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="WR2-C4 State Commit C2.1 limited real canary")
    parser.add_argument("command", choices=("build", "preflight", "run-once"))
    parser.add_argument("--output", type=Path, default=RUNTIME)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.output)
    elif args.command == "preflight":
        result = preflight(args.output, args.authorization)
    else:
        result = run_once(args.output, args.authorization)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
