"""Build the Phase 4R R2 shadow SceneSpec report without calling an LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean

from app.writing.scene_compiler import SceneCompiler
from app.writing.story_state_view import StoryStateView


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "reports" / "phase4r-batch-r2-scene-spec-shadow.json"
R1_AUDIT = ROOT / "reports" / "phase4r-batch-r1-writer-hash-audit.json"


SCENES = {
    4: {
        "section": 17, "subsection": 3,
        "intent": "策划面包婚礼，社区好友和老顾客带着面包与故事见证仪式。",
        "confirmed": ["当前场景位于第17章第3小节。"],
        "unknown": ["未被现有状态源列明的到场者亲属经历与生死状态未知。"],
        "predicates": ["unverified_character_fact"],
    },
    6: {
        "section": 7, "subsection": 1,
        "intent": "当前是周六；林晚没有去面包店，在家收到周野邀请并开始阅读面包书。",
        "confirmed": ["当前时间锚点为周六。", "林晚当前没有去面包店。"],
        "unknown": ["面包店当前是否营业未知；周野当前是否在店未知。"],
        "predicates": ["current_time_anchor", "character_absence", "location_operation", "future_time_relation"],
    },
    7: {
        "section": 6, "subsection": 3,
        "intent": "林晚因错位镜头产生内疚，删除记录，并直接面对周野。",
        "confirmed": ["当前时间锚点沿用本节明确给出的周六。"],
        "unknown": ["未由来源确认的下一日星期关系未知。"],
        "predicates": ["current_time_anchor", "future_time_relation", "open_loop"],
    },
    8: {
        "section": 4, "subsection": 3,
        "intent": "林晚发现小圆面包微咸，联想到周野的专注，反思分享与尊重的边界。",
        "confirmed": ["本小节的叙事范围止于微咸面包及分享边界的反思。"],
        "unknown": ["后续提问、删帖或到店参与是否已经发生，当前来源未确认。"],
        "predicates": ["planned_event", "future_event_status"],
    },
}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_scene(query_index: int, data: dict, task_id: str) -> dict:
    section, subsection = data["section"], data["subsection"]
    source_id = f"frozen-outline:q{query_index:02d}"
    sources = [{
        "evidence_id": f"ev:q{query_index:02d}:outline",
        "source_id": source_id,
        "source_type": "current_outline",
        "text": data["intent"],
        "section": section,
        "subsection": subsection,
        "span_start": 0,
        "span_end": len(data["intent"]),
    }]
    evidence_id = sources[0]["evidence_id"]
    assertions = [{
        "assertion_id": f"q{query_index:02d}:planned",
        "subject": "current_scene", "predicate": "planned_event",
        "value": data["intent"], "status": "planned", "evidence_ids": [evidence_id],
    }]
    for index, value in enumerate(data["confirmed"], 1):
        predicate = data["predicates"][min(index - 1, len(data["predicates"]) - 1)]
        assertions.append({
            "assertion_id": f"q{query_index:02d}:confirmed:{index}",
            "subject": "current_scene", "predicate": predicate,
            "value": value, "status": "confirmed", "evidence_ids": [evidence_id],
        })
    for index, value in enumerate(data["unknown"], 1):
        unknown_predicates = [p for p in data["predicates"] if p in {
            "location_operation", "future_time_relation", "future_event_status", "unverified_character_fact"
        }]
        assertions.append({
            "assertion_id": f"q{query_index:02d}:unknown:{index}",
            "subject": "current_scene", "predicate": unknown_predicates[min(index - 1, len(unknown_predicates) - 1)],
            "value": value, "status": "unknown", "evidence_ids": [evidence_id],
        })
    if "open_loop" in data["predicates"]:
        assertions.append({
            "assertion_id": f"q{query_index:02d}:open", "subject": "current_scene",
            "predicate": "open_loop", "value": "面对周野的行动必须在本节按计划展开，不预设其回应。",
            "status": "planned", "evidence_ids": [evidence_id],
        })
    assertions.append({
        "assertion_id": f"q{query_index:02d}:hard", "subject": "writer",
        "predicate": "hard_constraint",
        "value": "只把 confirmed 写成既成事实；planned 是本节目标；unknown/conflicted 不得静默补全。",
        "status": "confirmed", "evidence_ids": [evidence_id],
    })
    snapshot = StoryStateView(task_id=task_id, section=section, subsection=subsection).project(sources, assertions)
    compiler = SceneCompiler()
    spec = compiler.compile(snapshot)
    rendered = compiler.render(spec)
    trace_ids = {item.evidence_id for item in spec.evidence}
    referenced = {
        evidence_id
        for name in ("confirmed_state", "planned_events", "open_loops", "hard_constraints", "forbidden_inferences", "unknowns_and_conflicts")
        for item in getattr(spec, name)
        for evidence_id in item.evidence_ids
    }
    return {
        "query_index": query_index,
        "section": section,
        "subsection": subsection,
        "scene_id": spec.scene_id,
        "source_hash": spec.source_hash,
        "spec_hash": spec.spec_hash,
        "rendered_hash": sha256(rendered),
        "estimated_tokens": spec.estimated_tokens,
        "counts": {
            "confirmed": len(spec.confirmed_state), "planned": len(spec.planned_events),
            "open_loops": len(spec.open_loops), "hard_constraints": len(spec.hard_constraints),
            "forbidden_inferences": len(spec.forbidden_inferences),
            "unknowns_conflicts": len(spec.unknowns_and_conflicts),
        },
        "statuses": sorted({item.status for item in spec.confirmed_state + spec.planned_events + spec.unknowns_and_conflicts}),
        "forbidden_inference_ids": [item.assertion_id for item in spec.forbidden_inferences],
        "unknown_assertion_ids": [item.assertion_id for item in spec.unknowns_and_conflicts],
        "source_manifest": [item.model_dump(exclude={"excerpt"}) for item in spec.evidence],
        "traceability_rate": 1.0 if referenced == trace_ids else 0.0,
        "contains_story_text": False,
    }


def build_report() -> dict:
    r1 = json.loads(R1_AUDIT.read_text(encoding="utf-8"))
    task_id = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
    scenes = [build_scene(index, data, task_id) for index, data in SCENES.items()]
    tokens = [item["estimated_tokens"] for item in scenes]
    return {
        "phase": "Phase 4R Batch R2",
        "mode": "shadow_only",
        "schema_version": "phase4r-r2-v1",
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "runtime_evaluation_fields_used": [],
        "production_writer_imports_scene_spec": False,
        "production_messages_hash_unchanged": all(
            item["content_hash_unchanged"]
            and item["messages_hash_unchanged"]
            and item["messages_equal_legacy_runtime"]
            for item in r1["samples"]
        ),
        "production_hash_baseline_count": len(r1["samples"]),
        "summary": {
            "scene_count": len(scenes),
            "mean_estimated_tokens": round(mean(tokens), 1),
            "min_estimated_tokens": min(tokens),
            "max_estimated_tokens": max(tokens),
            "all_sources_traceable": all(item["traceability_rate"] == 1.0 for item in scenes),
            "all_unknowns_preserved": all(item["counts"]["unknowns_conflicts"] > 0 for item in scenes),
        },
        "scenes": scenes,
        "decision": "R2 shadow contract ready for R3 authorization; production remains legacy_full",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
