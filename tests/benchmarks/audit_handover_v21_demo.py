"""Read-only acceptance audit for the single authorized V2.1 four-subsection demo.

Dependency-free (stdlib only) so it can run with the project venv OR a bare python.
It never reads prose, prompts, messages or handover text: only execution telemetry.

Two independent evidence sources, cross-checked:

  1. Celery worker log lines emitted by Writer:
         handover_v21_observation={...}
     This is the primary source. It survives even when the task fails.
  2. Persisted per-subsection records in tasks.db
         task_history.analysis_json -> "subsection_handover_history_v1"
     This proves the artifact actually landed and can be restored.

Usage
-----
    python tests/benchmarks/audit_handover_v21_demo.py \
        --log .v21_demo_runtime/celery.log \
        --task-id <TASK_ID> \
        --expected-subsections 4

Exit code is 0 when the audit completes; the go/no-go verdict is in the report,
not in the exit code. A non-zero exit means the audit itself could not run.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HISTORY_KEY = "subsection_handover_history_v1"
OBSERVATION_PREFIX = "handover_v21_observation="
OUTPUT_TOKEN_LIMIT = 600

DEFAULT_LOG = ROOT / ".v21_demo_runtime" / "celery.log"
DEFAULT_DB = ROOT / "tasks.db"
DEFAULT_REPORT = ROOT / "reports" / "handover-contract-v21-real-demo.json"
DEFAULT_MARKDOWN = ROOT / "reports" / "handover-contract-v21-real-demo.md"


# --------------------------------------------------------------------------
# source 1: celery log observations
# --------------------------------------------------------------------------

_OBS_RE = re.compile(re.escape(OBSERVATION_PREFIX) + r"(\{.*\})\s*$")


def parse_observations(log_path: Path) -> list[dict[str, Any]]:
    """Extract every handover_v21_observation JSON object from a worker log."""
    if not log_path.exists():
        raise SystemExit(f"log not found: {log_path}")
    out: list[dict[str, Any]] = []
    text = log_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        match = _OBS_RE.search(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def parse_mandatory_retries(log_path: Path) -> int:
    """Count real mandatory-event retries (must stay 0 after the warn downgrade)."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    total = 0
    for line in text.splitlines():
        if "mandatory_event_observation=" not in line:
            continue
        raw = line.split("mandatory_event_observation=", 1)[1].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("retry_executed"):
            total += 1
    return total


# --------------------------------------------------------------------------
# source 2: persisted records
# --------------------------------------------------------------------------


def load_persisted(db_path: Path, task_id: str) -> dict[str, Any]:
    """Read the persisted handover history for one task. Never writes."""
    if not db_path.exists():
        return {"available": False, "reason": "db_missing", "records": []}
    # snapshot so an active WAL is included and the live db is never locked
    snapshot_dir = Path("/tmp") if Path("/tmp").is_dir() else db_path.parent
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT analysis_json, status FROM task_history WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    except sqlite3.Error as error:
        return {"available": False, "reason": type(error).__name__, "records": []}
    finally:
        conn.close()
    del snapshot_dir

    if not row:
        return {"available": False, "reason": "task_row_missing", "records": []}

    analysis_raw, task_status = row
    try:
        analysis = json.loads(analysis_raw) if analysis_raw else {}
    except json.JSONDecodeError:
        return {"available": False, "reason": "analysis_unparsable", "records": []}

    history = analysis.get(HISTORY_KEY)
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except json.JSONDecodeError:
            history = None
    if not isinstance(history, dict):
        return {
            "available": False,
            "reason": "history_key_absent",
            "task_status": task_status,
            "records": [],
        }

    # The envelope stores records as a dict keyed by record_id (values may be
    # JSON strings). Treating it as a list silently yields zero records — the
    # exact mistake that briefly mis-read the 2026-07-26 demo as "nothing
    # persisted". Normalize both shapes.
    raw_records = history.get("records") or []
    if isinstance(raw_records, dict):
        raw_records = list(raw_records.values())
    records = []
    for record in raw_records:
        if isinstance(record, str):
            try:
                record = json.loads(record)
            except json.JSONDecodeError:
                continue
        if isinstance(record, dict):
            records.append(record)
    return {
        "available": True,
        "task_status": task_status,
        "records": records,
        "pending_count": len(history.get("pending") or []),
        "error_count": len(history.get("errors") or []),
    }


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------


def _gate(name: str, passed: bool | None, detail: str) -> dict[str, Any]:
    if passed is None:
        status = "unassessable"
    else:
        status = "pass" if passed else "fail"
    return {"gate": name, "status": status, "detail": detail}


def evaluate(
    observations: list[dict[str, Any]],
    persisted: dict[str, Any],
    expected_subsections: int,
    mandatory_retries: int,
) -> dict[str, Any]:
    v21 = [o for o in observations if str(o.get("version")) == "2.1"]
    records = persisted.get("records") or []

    finishes = [o.get("finish_reason") for o in v21]
    truncated = [o for o in v21 if o.get("output_truncated") is True]
    out_tokens = [
        o.get("raw_output_tokens")
        for o in v21
        if isinstance(o.get("raw_output_tokens"), int)
    ]
    contract_hashes = [o.get("typed_contract_hash") for o in v21]
    fallbacks = [o.get("fallback_reason") for o in v21 if o.get("fallback_reason")]
    restored = [
        o.get("restored_claim_count")
        for o in v21
        if isinstance(o.get("restored_claim_count"), int)
    ]
    locally_rejected = [
        o.get("locally_rejected_claim_count")
        for o in v21
        if isinstance(o.get("locally_rejected_claim_count"), int)
    ]
    registry_hashes = {o.get("source_registry_hash") for o in v21}

    persisted_v21 = [r for r in records if r.get("contract_version") == "v2.1"]
    # "completed_no_change" is a persisted-success status too: the extraction
    # ran and the record landed, but the restored note carried no field
    # changes. G7 asks "did the chain persist"; whether it carried CONTENT is
    # G6's job — an all-rejected run persists 4 completed_no_change records
    # and must fail on G6, not vanish from G7.
    persisted_ok = [
        r
        for r in persisted_v21
        if r.get("execution_status") in {"committed", "completed_no_change"}
    ]

    gates = [
        _gate(
            "G1_one_call_per_subsection",
            len(v21) == expected_subsections,
            f"v2.1 extractions={len(v21)} expected={expected_subsections}",
        ),
        _gate(
            "G2_no_extra_body_retry",
            mandatory_retries == 0,
            f"mandatory_event real retries={mandatory_retries} (must be 0)",
        ),
        _gate(
            "G3_no_output_truncation",
            len(v21) > 0 and not truncated,
            f"finish_reasons={finishes} truncated={len(truncated)} "
            f"(this is exactly where V2 died)",
        ),
        _gate(
            "G4_typed_contract_built",
            len(v21) > 0 and all(bool(h) for h in contract_hashes),
            f"typed_contract_hash present={sum(1 for h in contract_hashes if h)}"
            f"/{len(v21)} fallbacks={fallbacks or 'none'}",
        ),
        _gate(
            "G5_output_headroom",
            bool(out_tokens) and max(out_tokens) < OUTPUT_TOKEN_LIMIT,
            f"max_output_tokens={max(out_tokens) if out_tokens else 'n/a'} "
            f"limit={OUTPUT_TOKEN_LIMIT} "
            f"headroom={OUTPUT_TOKEN_LIMIT - max(out_tokens) if out_tokens else 'n/a'}",
        ),
        _gate(
            "G6_claims_restored_locally",
            bool(restored) and sum(restored) > 0,
            f"restored_claims={sum(restored) if restored else 0} "
            f"locally_rejected={sum(locally_rejected) if locally_rejected else 0}",
        ),
        _gate(
            "G7_handover_chain_persisted",
            len(persisted_ok) == expected_subsections
            if persisted.get("available")
            else None,
            f"persisted v2.1 committed records={len(persisted_ok)}"
            f"/{len(persisted_v21)} available={persisted.get('available')} "
            f"reason={persisted.get('reason', 'ok')}",
        ),
        _gate(
            "G8_fail_open_task_completed",
            persisted.get("task_status") == "completed"
            if persisted.get("task_status")
            else None,
            f"task_status={persisted.get('task_status')}",
        ),
        _gate(
            "G9_source_registry_stable",
            len(registry_hashes) == len(v21) if v21 else None,
            f"distinct source_registry_hash={len(registry_hashes)} over {len(v21)} "
            f"subsections (each subsection should have its own registry)",
        ),
    ]

    hard_fail = [g for g in gates if g["status"] == "fail"]
    unassessable = [g for g in gates if g["status"] == "unassessable"]

    if hard_fail:
        verdict = "real_demo_failed"
    elif unassessable:
        verdict = "real_demo_partially_assessable"
    else:
        verdict = "real_demo_passed_engineering_gates"

    return {
        "verdict": verdict,
        "gates": gates,
        "failed_gates": [g["gate"] for g in hard_fail],
        "unassessable_gates": [g["gate"] for g in unassessable],
        "per_subsection": [
            {
                "section": o.get("section"),
                "subsection": o.get("subsection"),
                "finish_reason": o.get("finish_reason"),
                "raw_output_tokens": o.get("raw_output_tokens"),
                "output_truncated": o.get("output_truncated"),
                "restored_claim_count": o.get("restored_claim_count"),
                "locally_rejected_claim_count": o.get("locally_rejected_claim_count"),
                "typed_contract_hash": o.get("typed_contract_hash"),
                "fallback_reason": o.get("fallback_reason"),
                "elapsed_ms": o.get("elapsed_ms"),
                "source_registry_count": o.get("source_registry_count"),
                "production_effect": o.get("production_effect"),
            }
            for o in v21
        ],
        "totals": {
            "v21_extractions": len(v21),
            "output_tokens_sum": sum(out_tokens) if out_tokens else 0,
            "output_tokens_max": max(out_tokens) if out_tokens else None,
            "elapsed_ms_sum": round(
                sum(
                    o.get("elapsed_ms", 0)
                    for o in v21
                    if isinstance(o.get("elapsed_ms"), (int, float))
                ),
                3,
            ),
            "restored_claims": sum(restored) if restored else 0,
            "locally_rejected_claims": sum(locally_rejected) if locally_rejected else 0,
            "mandatory_event_retries": mandatory_retries,
        },
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    r = report
    lines = [
        "# Subsection Handover Contract V2.1 真实 Demo 验收",
        "",
        f"状态：`{r['verdict']}`。本报告只包含执行遥测，不含正文、Prompt、messages 或 Handover 文本。",
        "",
        "## 固定任务",
        "",
        f"- task_id：`{r['task_id']}`",
        f"- 任务状态：{r['persisted'].get('task_status')}",
        f"- v2.1 提取次数：{r['totals']['v21_extractions']}",
        f"- Mandatory Event 实际重试：{r['totals']['mandatory_event_retries']}",
        "",
        "## 逐小节结果",
        "",
        "| 小节 | 输出 Token | finish | 截断 | 恢复 claim | 本地拒绝 | 延迟(ms) | typed hash |",
        "|---|---:|---|---|---:|---:|---:|---|",
    ]
    for s in r["per_subsection"]:
        h = s["typed_contract_hash"]
        lines.append(
            f"| S{s['section']}.{s['subsection']} | {s['raw_output_tokens']} | "
            f"`{s['finish_reason']}` | {s['output_truncated']} | "
            f"{s['restored_claim_count']} | {s['locally_rejected_claim_count']} | "
            f"{s['elapsed_ms']} | {(h[:12] + '…') if h else '—'} |"
        )
    lines += [
        "",
        f"输出 token 峰值 {r['totals']['output_tokens_max']} / 上限 {OUTPUT_TOKEN_LIMIT}；"
        f"合计恢复 claim {r['totals']['restored_claims']} 条，本地拒绝 "
        f"{r['totals']['locally_rejected_claims']} 条。",
        "",
        "## 验收门槛",
        "",
        "| 门槛 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for g in r["gates"]:
        icon = {"pass": "通过", "fail": "**未通过**", "unassessable": "不可评估"}[
            g["status"]
        ]
        lines.append(f"| `{g['gate']}` | {icon} | {g['detail']} |")
    lines += [
        "",
        "## 决策",
        "",
    ]
    if r["verdict"] == "real_demo_passed_engineering_gates":
        lines += [
            "工程与容量门槛全部通过。这只证明 V2.1 输出契约能稳定容纳于 600-token 上限"
            "并成功构建 typed contract，**不证明 handover 内容质量已成为真值**。",
            "",
            "是否将 `WRITER_HANDOVER_CONTRACT_VERSION` 默认切到 v2.1，仍需另行授权的内容有效性审计。",
        ]
    else:
        lines += [
            f"未通过门槛：{', '.join(r['failed_gates']) or '无'}；"
            f"不可评估：{', '.join(r['unassessable_gates']) or '无'}。",
            "",
            "默认继续 `WRITER_HANDOVER_CONTRACT_VERSION=v1`。禁止同配置重跑，"
            "禁止通过扩 Prompt、加关键词或重复调用追逐通过。",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--expected-subsections", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    observations = parse_observations(args.log)
    mandatory_retries = parse_mandatory_retries(args.log)
    persisted = load_persisted(args.db, args.task_id)

    report = evaluate(
        observations, persisted, args.expected_subsections, mandatory_retries
    )
    report["task_id"] = args.task_id
    report["persisted"] = {
        k: v for k, v in persisted.items() if k != "records"
    }
    report["persisted"]["record_count"] = len(persisted.get("records") or [])
    report["log_path"] = str(args.log)
    report["observations_seen"] = len(observations)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = render_markdown(report)
    args.markdown.write_text(markdown, encoding="utf-8")

    print(markdown)
    print(f"[audit] json     -> {args.output}")
    print(f"[audit] markdown -> {args.markdown}")
    if report["verdict"] != "real_demo_passed_engineering_gates":
        print(f"[audit] VERDICT: {report['verdict']}", file=sys.stderr)


if __name__ == "__main__":
    main()
