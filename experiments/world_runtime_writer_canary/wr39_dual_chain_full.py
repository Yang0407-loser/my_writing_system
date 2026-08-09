"""WR3.9 real full dual-chain comparison: legacy Writer chain vs WR chain.

Corpus: the frozen C2.1-R10 canary texts (3 subsections).
Legacy side (6 LLM calls total, exactly once, zero retries):
  - Writer._extract_handover_with_observation per subsection (3 calls);
  - SharedPostWriteExtractor-style typed extraction per subsection (3 calls,
    local copy with max_tokens=4000).
WR side: the frozen C2.1-R10 commits are reused without new calls.
Offline comparison: legacy StateFrame V1 (built from handover notes + post-write
bundles) vs WR legacy StateFrame projection; plus handover field coverage and
clock-time divergence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.agents.writer import Writer
from app.config import settings
from app.utils.json_parser import parse_json
from app.utils.llm_client import get_llm_client
from app.writing.contracts import (
    PostWriteEvidence,
    PostWriteStateBundle,
    PostWriteStateChange,
)
from app.writing.post_write_extraction import (
    ALLOWED_CATEGORIES,
    ALLOWED_STATUSES,
    POST_WRITE_EXTRACTION_PROMPT,
    _canonical_hash,
    _sha256,
    _string_value,
)
from app.writing.state_frame_service import build_state_frame_artifacts
from app.writing.world_runtime_legacy_projection import project_state_frame
from app.writing.world_runtime_handover_projection import project_handover
from app.writing.world_runtime_state_committer import CommittedWorldState


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve()
RUNTIME = ROOT / ".world_runtime_wr39_dual_chain_runtime"
DEFAULT_AUTHORIZATION = RUNTIME / "private/external-execution-authorization.json"
CANARY_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits"
CANARY_OUTPUTS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/outputs"
SUBSECTIONS = 3
PROVIDER = {"temperature": 0.2, "max_tokens": 4000, "transport_retries": 0, "json_mode": True}

CHARACTERS = [
    {"name": "林晚", "personality": "细致"},
    {"name": "周野", "personality": "专注"},
    {"name": "季晴", "personality": "敏锐"},
    {"name": "老吴", "personality": "热心"},
]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
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
        "post_write_extraction_sha256": _sha256_file(
            ROOT / "app/writing/post_write_extraction.py"
        ),
        "writer_sha256": _sha256_file(ROOT / "app/agents/writer.py"),
        "state_frame_service_sha256": _sha256_file(
            ROOT / "app/writing/state_frame_service.py"
        ),
    }


def _sample_text(subsection: int) -> tuple[str, str]:
    path = CANARY_OUTPUTS / f"S{subsection}.txt"
    text = path.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(output_dir: Path = RUNTIME) -> dict[str, Any]:
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("WR3.9 ledger exists; refusing rebuild")
    samples = []
    for subsection in range(1, SUBSECTIONS + 1):
        text, output_hash = _sample_text(subsection)
        messages = [
            {"role": "system", "content": "你是一位严谨的小说状态记录员，只输出JSON。"},
            {"role": "user", "content": POST_WRITE_EXTRACTION_PROMPT.format(
                text=text[:8000],
                known_context=json.dumps({}, ensure_ascii=False),
            )},
        ]
        samples.append({
            "subsection": subsection,
            "source": f"c21r10:S{subsection}",
            "text_sha256": output_hash,
            "post_write_request_hash": _digest({"messages": messages, "provider": PROVIDER}),
            "handover_input_hash": _digest({"text_sha256": output_hash, "provider": PROVIDER}),
            "status": "pending",
            "attempt_count": 0,
        })
    manifest = {
        "schema_version": "world-runtime-wr39-dual-chain-manifest-v1",
        "experiment_id": "world-runtime-wr39-dual-chain",
        "partition_role": "real_full_dual_chain_comparison",
        "provider": PROVIDER,
        "provider_host": "api.deepseek.com",
        "model": settings.WRITER_LLM_MODEL,
        "source_hashes": _source_hashes(),
        "samples": samples,
        "production_behavior_changed": False,
        "state_commit_authorized": False,
    }
    ledger = {
        "schema_version": "world-runtime-wr39-dual-chain-ledger-v1",
        "samples": {
            sample["subsection"]: {
                "post_write_request_hash": sample["post_write_request_hash"],
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
        if _source_hashes().get(field) != expected:
            issues.append(f"source_hash_mismatch:{field}")
    pending = sum(item["status"] == "pending" for item in ledger.get("samples", {}).values())
    attempts = sum(item["attempt_count"] for item in ledger.get("samples", {}).values())
    outputs = list((output_dir / "private/outputs").glob("*")) if (output_dir / "private/outputs").exists() else []
    if pending != SUBSECTIONS or attempts != 0 or outputs:
        issues.append("ledger_or_outputs_not_pristine")
    if settings.WRITER_WORLD_RUNTIME_MODE != "off":
        issues.append("production_default_not_off")
    return {
        "schema_version": "world-runtime-wr39-dual-chain-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "pending": pending,
        "attempt_count_total": attempts,
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
        "schema_version": "world-runtime-wr39-dual-chain-external-authorization-v1",
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
        "schema_version": "world-runtime-wr39-dual-chain-external-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "model_calls": SUBSECTIONS * 2,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "authorization_sha256": _sha256_file(authorization_path) if authorization_path.exists() else None,
        "state_commit_authorized": False,
    }


def _extract_bundle(client, *, task_id: str, section: int, subsection: int, text: str, output_hash: str):
    """One local post-write extraction call (max_tokens=4000)."""
    if _sha256(text) != output_hash:
        raise ValueError("output_hash_mismatch")
    input_text = text[:8000]
    metadata: dict[str, Any] = {}
    response = client.chat_completion(
        [
            {"role": "system", "content": "你是一位严谨的小说状态记录员，只输出JSON。"},
            {"role": "user", "content": POST_WRITE_EXTRACTION_PROMPT.format(
                text=input_text,
                known_context=json.dumps({}, ensure_ascii=False),
            )},
        ],
        temperature=0.2,
        max_tokens=4000,
        json_mode=True,
        prompt_name="post_write_state_extraction",
        completion_metadata_sink=(lambda md: metadata.update(md)),
    )
    parsed = parse_json(response)
    if not isinstance(parsed, dict):
        raise ValueError("invalid_extraction_shape")
    warnings: list[str] = []
    changes: list[PostWriteStateChange] = []
    source_id = f"writer-output:{task_id}:{section}:{subsection}"
    for index, raw in enumerate(parsed.get("changes", [])):
        if not isinstance(raw, dict):
            warnings.append(f"change_{index}:not_object")
            continue
        category = str(raw.get("category", "")).strip()
        status = str(raw.get("status", "")).strip()
        subject = str(raw.get("subject", "")).strip()
        predicate = str(raw.get("predicate", "")).strip()
        value = _string_value(raw.get("value", ""))
        evidence_text = str(raw.get("evidence_text", "")).strip()
        if category not in ALLOWED_CATEGORIES or status not in ALLOWED_STATUSES:
            warnings.append(f"change_{index}:invalid_category_or_status")
            continue
        if not subject or not predicate or not value or not evidence_text:
            warnings.append(f"change_{index}:missing_required_field")
            continue
        start = text.find(evidence_text)
        if start < 0:
            warnings.append(f"change_{index}:evidence_not_found")
            continue
        end = start + len(evidence_text)
        try:
            confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        identity = {
            "category": category, "subject": subject, "predicate": predicate,
            "value": value, "status": status, "start": start, "end": end,
        }
        change_id = f"pws:{_canonical_hash(identity)[:20]}"
        evidence = PostWriteEvidence(
            evidence_id=f"evidence:{change_id}",
            source_id=source_id,
            text_hash=output_hash,
            span_start=start,
            span_end=end,
            excerpt=evidence_text[:140],
        )
        changes.append(PostWriteStateChange(
            change_id=change_id, category=category, subject=subject,
            predicate=predicate, value=value, status=status,
            confidence=confidence, evidence=[evidence],
        ))
    bundle_body = {
        "task_id": task_id, "section": section, "subsection": subsection,
        "output_hash": output_hash,
        "source_manifest": [{"source_id": source_id, "text_hash": output_hash}],
        "changes": [item.model_dump(mode="json") for item in changes],
        "extraction_warnings": warnings,
        "schema_version": "post-write-state-v1",
    }
    bundle = PostWriteStateBundle(**bundle_body, bundle_hash=_canonical_hash(bundle_body))
    return bundle, str(metadata.get("finish_reason") or "unknown")


def _extract_handover(writer: Writer, *, text: str, subsection: int, task_id: str):
    note, observation = writer._extract_handover_with_observation(
        text,
        1,
        subsection,
        task_id=task_id,
    )
    return note, observation


def _legacy_frame_facts(output_dir: Path, subsection: int) -> tuple[list[dict[str, Any]], list[str]]:
    handover_path = output_dir / "private/outputs" / f"S{subsection}.handover.json"
    bundle_path = output_dir / "private/outputs" / f"S{subsection}.bundle.json"
    note_payload = _read_json(handover_path)
    note = dict(note_payload.get("note") or {})
    note["to_section"] = subsection
    bundle = _read_json(bundle_path)
    artifacts = build_state_frame_artifacts(
        task_id="c21r10-dual-chain",
        section=1,
        subsection=subsection,
        task_data={
            "handover_notes": [note],
            "post_write_extraction_shadow": [bundle],
            "characters": CHARACTERS,
        },
    )
    after = artifacts["after"]
    facts = [
        {
            "fact_type": fact.get("fact_type", ""),
            "subject": fact.get("subject", ""),
            "predicate": fact.get("predicate", ""),
            "value": fact.get("value"),
            "status": fact.get("status", ""),
        }
        for fact in after.get("facts", [])
    ]
    return facts, after.get("unavailable_source_types", [])


def _wr_frame_facts(commit: dict[str, Any], subsection: int) -> list[dict[str, Any]]:
    committed = CommittedWorldState.model_validate(commit)
    frame = project_state_frame(committed, task_id="c21r10-dual-chain", section=1, subsection=subsection)
    return [
        {
            "fact_type": fact.fact_type,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "status": fact.status,
        }
        for fact in frame.facts
    ]


def _fact_key(fact: dict[str, Any]) -> tuple[str, str, str]:
    return (fact["fact_type"], fact["subject"], fact["predicate"])


def compare_subsections(output_dir: Path = RUNTIME) -> dict[str, Any]:
    reports = []
    for subsection in range(1, SUBSECTIONS + 1):
        commit_path = CANARY_COMMITS / f"S{subsection}.json"
        if not commit_path.exists():
            reports.append({"subsection": subsection, "error": "missing_wr_commit"})
            continue
        legacy_facts, unavailable = _legacy_frame_facts(output_dir, subsection)
        wr_facts = _wr_frame_facts(_read_json(commit_path), subsection)
        legacy_by_key = {_fact_key(fact): fact for fact in legacy_facts}
        wr_by_key = {_fact_key(fact): fact for fact in wr_facts}
        matched = []
        value_mismatch = []
        for key, wr_fact in wr_by_key.items():
            legacy_fact = legacy_by_key.get(key)
            if legacy_fact is None:
                continue
            if json.dumps(wr_fact["value"], ensure_ascii=False, sort_keys=True) == json.dumps(
                legacy_fact["value"], ensure_ascii=False, sort_keys=True
            ):
                matched.append(key)
            else:
                value_mismatch.append({"key": list(key), "wr": wr_fact["value"], "legacy": legacy_fact["value"]})
        wr_only = [list(key) for key in wr_by_key.keys() - legacy_by_key.keys()]
        legacy_only = [list(key) for key in legacy_by_key.keys() - wr_by_key.keys()]
        commit = _read_json(commit_path)
        wr_clock = [
            str(entry["after_value"])
            for entry in commit["ledger"]["entries"]
            if entry["change_type"] == "clock_state"
        ]
        legacy_temporal = sorted({
            str(fact["value"])
            for fact in legacy_facts
            if fact["fact_type"] == "temporal_state" and fact["value"] is not None
        })
        reports.append({
            "subsection": subsection,
            "legacy_frame_fact_count": len(legacy_facts),
            "wr_frame_fact_count": len(wr_facts),
            "matched_fact_keys": len(matched),
            "wr_only_fact_keys": wr_only,
            "legacy_only_fact_keys": legacy_only,
            "value_mismatches": value_mismatch,
            "legacy_unavailable_sources": unavailable,
            "wr_clock_values": wr_clock,
            "legacy_temporal_values": legacy_temporal,
        })
    totals = {
        "subsections": len(reports),
        "matched_fact_keys": sum(r.get("matched_fact_keys", 0) for r in reports),
        "wr_only_fact_keys": sum(len(r.get("wr_only_fact_keys", [])) for r in reports),
        "legacy_only_fact_keys": sum(len(r.get("legacy_only_fact_keys", [])) for r in reports),
        "value_mismatches": sum(len(r.get("value_mismatches", [])) for r in reports),
    }
    return {
        "schema_version": "world-runtime-wr39-dual-chain-report-v1",
        "source": str(CANARY_COMMITS),
        "totals": totals,
        "subsections": reports,
    }


def run_once(
    output_dir: Path = RUNTIME,
    authorization_path: Path = DEFAULT_AUTHORIZATION,
) -> dict[str, Any]:
    check = preflight(output_dir, authorization_path)
    if not check["ready"]:
        raise RuntimeError("wr39_preflight_failed:" + "|".join(check["issues"]))
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read_json(ledger_path)
    client = get_llm_client(manifest["model"])
    writer = Writer()
    records = []
    for sample in manifest["samples"]:
        subsection = sample["subsection"]
        entry = ledger["samples"][str(subsection)]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"wr39_refusing_nonpristine_subsection:{subsection}")
        entry.update(status="started", attempt_count=1)
        _write_json(ledger_path, ledger)
        record: dict[str, Any] = {"subsection": subsection, "status": "pending"}
        try:
            text, output_hash = _sample_text(subsection)
            note, observation = _extract_handover(
                writer, text=text, subsection=subsection, task_id="c21r10-dual-chain"
            )
            handover_path = output_dir / "private/outputs" / f"S{subsection}.handover.json"
            handover_path.parent.mkdir(parents=True, exist_ok=True)
            handover_path.write_text(
                json.dumps({
                    "note": note,
                    "observation": observation.model_dump(mode="json"),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            bundle, finish_reason = _extract_bundle(
                client,
                task_id="c21r10-dual-chain",
                section=1,
                subsection=subsection,
                text=text,
                output_hash=output_hash,
            )
            bundle_path = output_dir / "private/outputs" / f"S{subsection}.bundle.json"
            bundle_path.write_text(
                json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            record.update(
                status="succeeded",
                handover_ok=note is not None,
                handover_contract_version=observation.contract_version,
                post_write_changes=len(bundle.changes),
                finish_reason=finish_reason,
            )
        except Exception as exc:
            entry.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
            _write_json(ledger_path, ledger)
            raise
        entry.update(status="succeeded")
        _write_json(ledger_path, ledger)
        records.append(record)
    comparison = compare_subsections(output_dir)
    report = {
        "schema_version": "world-runtime-wr39-dual-chain-report-v1",
        "records": records,
        "comparison": comparison,
    }
    _write_json(output_dir / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="WR3.9 full dual-chain comparison")
    parser.add_argument("command", choices=("build", "preflight", "run-once", "compare"))
    args = parser.parse_args()
    if args.command == "build":
        result = build()
    elif args.command == "preflight":
        result = preflight()
    elif args.command == "compare":
        result = compare_subsections()
    else:
        result = run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
