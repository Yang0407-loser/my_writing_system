"""冻结 handover 金标夹具：从 tasks.db + 成稿 md 重建并逐 hash 验证后写入。

用途：把一次真实运行（首个金标：任务 3650fd64，Demo #4）的输入侧全链
（小节正文、大纲、source registry、next boundary）与结局数字冻结为
回归夹具。任何一个 hash 与库中记录不一致即拒绝写入（exit 2）。

只读打开 tasks.db；不修改任何生产数据。

用法（仓库根目录）：
    python tests/benchmarks/freeze_handover_gold.py \
        --task-prefix 3650fd64 \
        --story-file "output/周六面包店与凌晨3点_20260727_010552.md"

可选：--db tasks.db --out tests/fixtures/subsection_handover_gold_v1.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.writing.handover_contract_v2 import (  # noqa: E402
    _outline_text,
    build_handover_sources,
    compile_next_boundary,
    sha256_json,
    sha256_text,
)
from app.writing.handover_contract_v21 import (  # noqa: E402
    build_compact_source_registry,
)

HISTORY_KEY = "subsection_handover_history_v1"


def load_task(db_path: Path, task_prefix: str) -> dict:
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT task_id, topic, outline_json, analysis_json FROM task_history "
            "WHERE task_id LIKE ?",
            (f"{task_prefix}%",),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        raise SystemExit(f"task not found: {task_prefix}")
    task_id, topic, outline_json, analysis_json = row
    analysis = json.loads(analysis_json)
    history = analysis.get(HISTORY_KEY)
    if isinstance(history, str):
        history = json.loads(history)
    if not history or not history.get("records"):
        raise SystemExit("no handover history records on this task")
    records = history["records"]
    entries = records if isinstance(records, list) else list(records.values())
    parsed = [json.loads(e) if isinstance(e, str) else e for e in entries]
    parsed.sort(key=lambda r: (r["section"], r["subsection"]))
    return {
        "task_id": task_id,
        "topic": topic,
        "outline": json.loads(outline_json),
        "records": parsed,
    }


def subsection_text_candidates(story: str, titles: list[str]) -> list[str]:
    positions = []
    for title in titles:
        marker = f"【{title}】"
        idx = story.find(marker)
        if idx < 0:
            raise SystemExit(f"subsection marker not found in story file: {marker}")
        positions.append((idx, idx + len(marker)))
    slices = []
    for i in range(len(titles)):
        start = positions[i][1]
        end = positions[i + 1][0] if i + 1 < len(titles) else len(story)
        slices.append(story[start:end])
    return slices


def text_variants(raw: str):
    seen = set()
    tail_cut = raw
    for stop in ("\n---", "\n## ", "\n第1节"):
        p = tail_cut.find(stop)
        if p >= 0:
            tail_cut = tail_cut[:p]
    for base in (raw, tail_cut):
        for v in (base, base.strip(), base.strip() + "\n", base.lstrip("\n").rstrip()):
            if v not in seen:
                seen.add(v)
                yield v


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-prefix", required=True)
    parser.add_argument("--story-file", required=True)
    parser.add_argument("--db", default="tasks.db")
    parser.add_argument(
        "--out", default="tests/fixtures/subsection_handover_gold_v1.json"
    )
    args = parser.parse_args()

    data = load_task(REPO_ROOT / args.db, args.task_prefix)
    story = (REPO_ROOT / args.story_file).read_text(encoding="utf-8")

    outline_section = data["outline"][0]
    section_num = int(outline_section["section"])
    subsections = outline_section["subsections"]
    titles = [str(s["title"]).strip() for s in subsections]
    slices = subsection_text_candidates(story, titles)

    frozen_subsections = []
    failures = []
    for i, record in enumerate(data["records"]):
        sub_num = int(record["subsection"])
        manifest = {m["source_type"]: m for m in record["source_manifest"]}

        matched_text = None
        want_text_hash = manifest["generated_subsection"]["source_hash"]
        for candidate in text_variants(slices[i]):
            if sha256_text(candidate) == want_text_hash:
                matched_text = candidate
                break
        if matched_text is None:
            failures.append(f"S{section_num}.{sub_num}: generated text hash unmatched")
            continue

        current_outline = dict(subsections[i])
        current_outline["_section"] = section_num
        if sha256_text(_outline_text(current_outline)) != manifest["current_outline"]["source_hash"]:
            failures.append(f"S{section_num}.{sub_num}: current_outline hash mismatch")
        next_outline = None
        if "next_outline" in manifest:
            next_outline = dict(subsections[i + 1])
            next_outline["_section"] = section_num
            if sha256_text(_outline_text(next_outline)) != manifest["next_outline"]["source_hash"]:
                failures.append(f"S{section_num}.{sub_num}: next_outline hash mismatch")

        sources = build_handover_sources(
            section=section_num,
            subsection=sub_num,
            generated_text=matched_text,
            current_outline=current_outline,
            next_outline=next_outline,
            arc_milestones=(),
        )
        registry = build_compact_source_registry(sources, arc_milestones=())
        if registry.registry_hash != record["source_registry_hash"]:
            failures.append(f"S{section_num}.{sub_num}: registry hash mismatch")

        boundary = compile_next_boundary(
            section=section_num,
            subsection=sub_num,
            current_outline=current_outline,
            next_outline=next_outline,
        )
        if sha256_json(boundary.model_dump(mode="json")) != record["next_boundary_hash"]:
            failures.append(f"S{section_num}.{sub_num}: boundary hash mismatch")

        note_fields = {f["field_name"]: f["value"] for f in record["fields"]}
        note = dict(note_fields)
        note["resolved_events"] = []
        if sha256_json(note) != record["handover_note_hash"]:
            failures.append(f"S{section_num}.{sub_num}: note hash mismatch")
        for field in record["fields"]:
            if sha256_json(field["value"]) != field["value_hash"]:
                failures.append(
                    f"S{section_num}.{sub_num}: field {field['field_name']} value_hash mismatch"
                )

        frozen_subsections.append(
            {
                "subsection": sub_num,
                "execution_status": record["execution_status"],
                "counts": {
                    "restored_claim_count": record["restored_claim_count"],
                    "locally_rejected_claim_count": record["locally_rejected_claim_count"],
                    "accepted_claim_count": record["accepted_claim_count"],
                    "rejected_claim_count": record["rejected_claim_count"],
                },
                "rejection_counts": record["rejection_counts"] or {},
                "rejection_shape_skeletons": record["rejection_shape_skeletons"],
                "raw_output_tokens": record["raw_output_tokens"],
                "finish_reason": record["finish_reason"],
                "truncation_status": record["truncation_status"],
                "hashes": {
                    "output_sha256": record["output_sha256"],
                    "prompt_messages_hash": record["prompt_messages_hash"],
                    "handover_note_hash": record["handover_note_hash"],
                    "typed_contract_hash": record["typed_contract_hash"],
                    "compact_payload_hash": record["compact_payload_hash"],
                    "source_registry_hash": record["source_registry_hash"],
                    "next_boundary_hash": record["next_boundary_hash"],
                },
                "source_manifest": record["source_manifest"],
                "generated_text": matched_text,
                "current_outline": subsections[i],
                "next_outline": subsections[i + 1] if next_outline is not None else None,
                "note_fields": note_fields,
            }
        )

    if failures:
        print("REFUSING TO WRITE FIXTURE — hash verification failed:")
        for item in failures:
            print(f"  - {item}")
        return 2

    records = data["records"]
    aggregate = {
        "emitted_items": sum(
            r["restored_claim_count"] + r["locally_rejected_claim_count"] for r in records
        ),
        "restored_local_layer": sum(r["restored_claim_count"] for r in records),
        "accepted_validator_layer": sum(r["accepted_claim_count"] for r in records),
        "locally_rejected": sum(r["locally_rejected_claim_count"] for r in records),
        "rejected_total": sum(r["rejected_claim_count"] for r in records),
        "completed_with_changes": sum(
            r["execution_status"] == "completed_with_changes" for r in records
        ),
        "subsection_total": len(records),
    }
    rejection_distribution: dict[str, int] = {}
    for record in records:
        for reason, count in (record["rejection_counts"] or {}).items():
            rejection_distribution[reason] = rejection_distribution.get(reason, 0) + count

    fixture = {
        "fixture": "subsection-handover-gold-v1",
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": {
            "task_id": data["task_id"],
            "topic": data["topic"],
            "story_file": args.story_file,
            "demo": "handover-contract-v22-real-demo2-2026-07-26",
            "regenerate_with": "tests/benchmarks/freeze_handover_gold.py",
        },
        "contract_version": records[0]["contract_version"],
        "payload_version": records[0]["payload_version"],
        "producer_version": records[0]["producer_version"],
        "section": section_num,
        "baseline": {**aggregate, "rejection_distribution": rejection_distribution},
        "subsections": frozen_subsections,
    }

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"fixture written: {args.out}")
    print(
        "verified hashes: generated_text/current_outline/next_outline/"
        "registry/boundary/note/field values — all match"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
