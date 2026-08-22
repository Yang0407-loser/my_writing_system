"""WR3.8-R2 real dual-chain comparison: legacy post-write vs WR chain (C2.1-R4).

Legacy side: one SharedPostWriteExtractor call per C2.1-R4 subsection text
(3 calls total, exactly once, zero retries).  WR side: the frozen C2.1-R4
commits are reused without new calls.  The offline comparison quantifies
divergence, with a focus on clock changes so the deferred clock defect impact
(intermediate time misses / parse errors) can be measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.llm_client import get_llm_client
from app.utils.json_parser import parse_json
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
from experiments.world_runtime_writer_canary.semantic_projector_wr2c513r5 import (
    _parse_clock,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve()
RUNTIME = ROOT / ".world_runtime_wr38r2_dual_chain_runtime"
DEFAULT_AUTHORIZATION = RUNTIME / "private/external-execution-authorization.json"
CANARY_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r4/private/commits"
SUBSECTIONS = 3
PROVIDER = {"temperature": 0.2, "max_tokens": 4000, "transport_retries": 0, "json_mode": True}


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
    }


def _sample_text(subsection: int) -> tuple[str, str]:
    path = ROOT / ".world_runtime_state_commit_canary_runtime/c21r4/private/outputs" / f"S{subsection}.txt"
    text = path.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_messages(text: str) -> list[dict[str, str]]:
    from app.writing.post_write_extraction import POST_WRITE_EXTRACTION_PROMPT

    return [
        {"role": "system", "content": "你是一位严谨的小说状态记录员，只输出JSON。"},
        {"role": "user", "content": POST_WRITE_EXTRACTION_PROMPT.format(
            text=text,
            known_context=json.dumps({}, ensure_ascii=False),
        )},
    ]


def _extract_bundle(
    client,
    *,
    task_id: str,
    section: int,
    subsection: int,
    text: str,
    output_hash: str,
) -> tuple[PostWriteStateBundle, str]:
    """One local extraction call (max_tokens=4000) with the shared validation."""
    if _sha256(text) != output_hash:
        raise ValueError("output_hash_mismatch")
    if not text.strip():
        raise ValueError("empty_committed_text")
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
    finish_reason = str(metadata.get("finish_reason") or "unknown")
    parsed = parse_json(response)
    if not isinstance(parsed, dict):
        raise ValueError("invalid_extraction_shape")
    warnings: list[str] = []
    if len(text) > 8000:
        warnings.append("input_truncated")
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
        if category not in ALLOWED_CATEGORIES:
            warnings.append(f"change_{index}:invalid_category")
            continue
        if status not in ALLOWED_STATUSES:
            warnings.append(f"change_{index}:invalid_status")
            continue
        if not subject or not predicate or not value or not evidence_text:
            warnings.append(f"change_{index}:missing_required_field")
            continue
        start = text.find(evidence_text)
        if start < 0:
            warnings.append(f"change_{index}:evidence_not_found")
            continue
        end = start + len(evidence_text)
        confidence = raw.get("confidence", 0.0)
        try:
            confidence = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0
            warnings.append(f"change_{index}:invalid_confidence")
        identity = {
            "category": category,
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "status": status,
            "start": start,
            "end": end,
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
            change_id=change_id,
            category=category,
            subject=subject,
            predicate=predicate,
            value=value,
            status=status,
            confidence=confidence,
            evidence=[evidence],
        ))
    manifest = [{"source_id": source_id, "text_hash": output_hash}]
    bundle_body = {
        "task_id": task_id,
        "section": section,
        "subsection": subsection,
        "output_hash": output_hash,
        "source_manifest": manifest,
        "changes": [item.model_dump(mode="json") for item in changes],
        "extraction_warnings": warnings,
        "schema_version": "post-write-state-v1",
    }
    bundle = PostWriteStateBundle(
        **bundle_body,
        bundle_hash=_canonical_hash(bundle_body),
    )
    return bundle, finish_reason


def build(output_dir: Path = RUNTIME) -> dict[str, Any]:
    if (output_dir / "attempt-ledger.json").exists():
        raise FileExistsError("WR3.8-R2 ledger exists; refusing rebuild")
    samples = []
    for subsection in range(1, SUBSECTIONS + 1):
        text, output_hash = _sample_text(subsection)
        messages = _build_messages(text)
        samples.append({
            "subsection": subsection,
            "source": f"c21r4:S{subsection}",
            "text_sha256": output_hash,
            "messages": messages,
            "request_hash": _digest({"messages": messages, "provider": PROVIDER}),
            "status": "pending",
            "attempt_count": 0,
        })
    manifest = {
        "schema_version": "world-runtime-wr38r2-dual-chain-manifest-v1",
        "experiment_id": "world-runtime-wr38r2-dual-chain",
        "partition_role": "real_dual_chain_comparison",
        "provider": PROVIDER,
        "provider_host": "api.deepseek.com",
        "model": settings.WRITER_LLM_MODEL,
        "source_hashes": _source_hashes(),
        "samples": samples,
        "production_behavior_changed": False,
        "state_commit_authorized": False,
    }
    ledger = {
        "schema_version": "world-runtime-wr38r2-dual-chain-ledger-v1",
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
        "schema_version": "world-runtime-wr38r2-dual-chain-preflight-v1",
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
        "schema_version": "world-runtime-wr38r2-dual-chain-external-authorization-v1",
        "authorized": True,
        "experiment_id": manifest["experiment_id"],
        "locked_manifest_sha256": _sha256_file(output_dir / "private/locked-manifest.json"),
        "runner_source_sha256": manifest["source_hashes"]["runner_sha256"],
        "maximum_requests": SUBSECTIONS,
        "transport_retries": 0,
        "execute_command_exactly_once": True,
        "production_writer_change_authorized": False,
        "state_commit_authorized": False,
    }
    for field, value in expected.items():
        if authorization.get(field) != value:
            issues.append(f"authorization.{field}:expected:{value!r}")
    return {
        "schema_version": "world-runtime-wr38r2-dual-chain-external-preflight-v1",
        "ready": not issues,
        "issues": issues,
        "model_calls": SUBSECTIONS,
        "production_default": settings.WRITER_WORLD_RUNTIME_MODE,
        "authorization_sha256": _sha256_file(authorization_path) if authorization_path.exists() else None,
        "state_commit_authorized": False,
    }


def _legacy_times(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    times = []
    for change in bundle.get("changes", []):
        if change.get("category") != "temporal_state":
            continue
        value = str(change.get("value", ""))
        parsed = None
        match = re.fullmatch(r"\d{1,2}[:：]\d{2}", value.strip())
        if match:
            hour, minute = (int(part) for part in re.split(r"[:：]", value.strip()))
            parsed = f"{hour:02d}:{minute:02d}"
        else:
            parsed = _parse_clock(value)
        times.append({
            "subject": change.get("subject", ""),
            "predicate": change.get("predicate", ""),
            "raw_value": value,
            "parsed_time": parsed,
            "status": change.get("status", ""),
        })
    return times


def _wr_times(commit: dict[str, Any]) -> list[str]:
    return [
        str(entry["after_value"])
        for entry in commit["ledger"]["entries"]
        if entry["change_type"] == "clock_state"
    ]


def compare_subsections(output_dir: Path = RUNTIME) -> dict[str, Any]:
    reports = []
    for subsection in range(1, SUBSECTIONS + 1):
        bundle_path = output_dir / "private/outputs" / f"S{subsection}.bundle.json"
        commit_path = CANARY_COMMITS / f"S{subsection}.json"
        if not bundle_path.exists() or not commit_path.exists():
            reports.append({
                "subsection": subsection,
                "error": "missing_inputs",
            })
            continue
        bundle = _read_json(bundle_path)
        commit = _read_json(commit_path)
        legacy_times = _legacy_times(bundle)
        legacy_parsed = {
            item["parsed_time"] for item in legacy_times if item["parsed_time"] is not None
        }
        wr_times = _wr_times(commit)
        wr_set = set(wr_times)
        legacy_only = sorted(legacy_parsed - wr_set)
        wr_only = sorted(wr_set - legacy_parsed)
        matched = sorted(legacy_parsed & wr_set)
        reports.append({
            "subsection": subsection,
            "wr_clock_changes": wr_times,
            "legacy_temporal_changes": legacy_times,
            "clock_divergence": {
                "matched": matched,
                "wr_only": wr_only,
                "legacy_only": legacy_only,
            },
            "legacy_change_count": len(bundle.get("changes", [])),
            "wr_committed_count": len(commit["ledger"]["entries"]),
            "legacy_categories": sorted({
                change.get("category", "") for change in bundle.get("changes", [])
            }),
            "wr_change_types": sorted({
                entry["change_type"] for entry in commit["ledger"]["entries"]
            }),
        })
    totals = {
        "subsections": len(reports),
        "clock_matched": sum(len(r.get("clock_divergence", {}).get("matched", [])) for r in reports),
        "clock_wr_only": sum(len(r.get("clock_divergence", {}).get("wr_only", [])) for r in reports),
        "clock_legacy_only": sum(len(r.get("clock_divergence", {}).get("legacy_only", [])) for r in reports),
    }
    return {
        "schema_version": "world-runtime-wr38r2-dual-chain-report-v1",
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
        raise RuntimeError("wr38r2_preflight_failed:" + "|".join(check["issues"]))
    manifest = _read_json(output_dir / "private/locked-manifest.json")
    ledger_path = output_dir / "attempt-ledger.json"
    ledger = _read_json(ledger_path)
    client = get_llm_client(manifest["model"])
    records = []
    for sample in manifest["samples"]:
        subsection = sample["subsection"]
        entry = ledger["samples"][str(subsection)]
        if entry["status"] != "pending" or entry["attempt_count"] != 0:
            raise RuntimeError(f"wr38r2_refusing_nonpristine_subsection:{subsection}")
        entry.update(status="started", attempt_count=1)
        _write_json(ledger_path, ledger)
        record: dict[str, Any] = {"subsection": subsection, "status": "pending"}
        try:
            text, output_hash = _sample_text(subsection)
            bundle, finish_reason = _extract_bundle(
                client,
                task_id="c21r4-dual-chain",
                section=1,
                subsection=subsection,
                text=text,
                output_hash=output_hash,
            )
            bundle_path = output_dir / "private/outputs" / f"S{subsection}.bundle.json"
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_text(
                json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            record.update(
                status="succeeded",
                changes=len(bundle.changes),
                bundle_hash=bundle.bundle_hash,
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
        "schema_version": "world-runtime-wr38r2-dual-chain-report-v1",
        "records": records,
        "comparison": comparison,
    }
    _write_json(output_dir / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="WR3.8-R2 dual-chain comparison")
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
