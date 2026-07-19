"""Build the Phase 4 entry Writer-context census without calling an LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean

from app.agents.writer import Writer, _narrative_density_instruction
from app.context_census import (
    SOURCE_CONTRACTS,
    build_ledger,
    diagnose_duplicates,
    estimate_tokens,
    make_block,
    validate_required_manifest,
)
from app.utils.prompt_templates import WRITER_SYSTEM_PROMPT, WRITING_PROMPT, WRITING_SECTION1_PROMPT
from app.utils.style_brief import StyleSummarizer
from app.utils.style_mapping import build_style_examples
from app.vector_store import VectorStore
from tests.quality.baseline import DEFAULT_CHARACTER, DEFAULT_RAG, DEFAULT_STYLE, load_json


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "reports" / "phase4-entry-context-census.json"
REVIEW_PATH = ROOT / "tests" / "quality" / "phase3_shadow_candidates_review.json"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def serialize_block(block: dict) -> dict:
    """Keep traceability and metrics without copying prompt/Chroma text into reports."""
    value = dict(block)
    text = value.pop("text", "")
    value["text_hash"] = sha256_text(text)
    return value


def parse_story(path: Path) -> dict[int, dict]:
    text = path.read_text(encoding="utf-8")
    narrative = text.split("\n## 审阅意见", 1)[0]
    section_matches = list(re.finditer(r"(?m)^第(\d+)节：([^\n]+)$", narrative))
    sections = {}
    for index, match in enumerate(section_matches):
        number = int(match.group(1))
        body_end = section_matches[index + 1].start() if index + 1 < len(section_matches) else len(narrative)
        body = narrative[match.end():body_end].strip()
        subsection_matches = list(re.finditer(r"(?m)^【([^\n】]+)】\s*$", body))
        subsections = []
        for sub_index, sub_match in enumerate(subsection_matches):
            sub_end = subsection_matches[sub_index + 1].start() if sub_index + 1 < len(subsection_matches) else len(body)
            sub_text = body[sub_match.end():sub_end].strip()
            subsections.append({
                "subsection": sub_index + 1,
                "title": sub_match.group(1).strip(),
                "text": sub_text,
                "source_id": f"golden:S{number}:U{sub_index + 1}",
                "text_hash": sha256_text(sub_text),
            })
        sections[number] = {
            "section": number,
            "title": match.group(2).strip(),
            "subsections": subsections,
        }
    return sections


def parse_handovers(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8")
    marker = "## 交接笔记链"
    if marker not in text:
        return {}
    appendix = text.split(marker, 1)[1]
    matches = list(re.finditer(r"(?m)^- \*\*第(\d+)节→第\d+节\*\*\s*$", appendix))
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(appendix)
        result[int(match.group(1))] = appendix[match.end():end].strip()
    return result


def prior_subsections(sections: dict[int, dict], section: int, subsection: int, limit: int = 3) -> list[dict]:
    ordered = [
        item
        for section_number in sorted(sections)
        for item in sections[section_number]["subsections"]
        if (section_number, item["subsection"]) < (section, subsection)
    ]
    return ordered[-limit:]


def frozen_requirement(entry: dict, section_title: str, subsection_title: str, topic: str) -> str:
    value = str(entry["query"]).strip()
    for prefix in (topic, section_title, subsection_title):
        if prefix and value.startswith(prefix):
            value = value[len(prefix):].strip()
    return value or str(entry["query"]).strip()


def read_global_rules_readonly(path: Path) -> tuple[str, dict]:
    if not path.exists():
        return "", {"available": False, "reason": "rules.db missing"}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, content, priority FROM rules WHERE enabled = 1 ORDER BY priority DESC, id"
        ).fetchall()
        connection.close()
    except sqlite3.Error as exc:
        return "", {"available": False, "reason": type(exc).__name__}
    if not rows:
        return "", {"available": True, "count": 0, "database_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    lines = ["## 用户自定义规则（只读快照）"]
    for index, row in enumerate(rows, 1):
        lines.append(f"{index}. [优先级{row['priority']}] {row['content']}")
    return "\n".join(lines), {
        "available": True,
        "count": len(rows),
        "database_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def relevant_hard_rules(annotation: dict, query: str) -> list[dict]:
    names = {item["character"] for item in annotation["constraints"] if item["character"] in query}
    rules = []
    for item in annotation["constraints"]:
        if item.get("hardness") != "hard" or item["character"] not in names:
            continue
        rules.append(item)
    return rules


def human_evidence_manifest(review: dict, query_index: int) -> list[dict]:
    query = next(item for item in review["queries"] if int(item["query_index"]) == query_index)
    result = []
    for candidate in query["candidates"]:
        facts = list(candidate.get("supports_which_fact") or [])
        if candidate.get("human_relevant") != "相关" or not facts:
            continue
        result.append({
            "item_id": f"evidence:q{query_index:02d}:{candidate['source_id']}",
            "requirement": "evidence_required",
            "source_id": candidate["source_id"],
            "facts": facts,
            "review_provenance": "human_review",
        })
    return result


def format_rag(items: list[dict]) -> str:
    if not items:
        return "（无相关段落）"
    lines = ["已写段落参考（以下段落与当前章节语义相关，供风格和情节参照）："]
    for index, item in enumerate(items, 1):
        source = f"第{item.get('section', 0)}节 · {item.get('title', '')}" if item.get("title") else (
            f"第{item.get('section', 0)}.{item.get('subsection', 0)}小节"
        )
        lines.append(f"\n### 参考 {index}：{source}\n{item.get('text', '')}")
    return "\n".join(lines)


def build_outline(sections: dict[int, dict]) -> list[dict]:
    return [
        {
            "section": number,
            "title": sections[number]["title"],
            "key_points": [],
            "subsections": [
                {"subsection": item["subsection"], "title": item["title"]}
                for item in sections[number]["subsections"]
            ],
        }
        for number in sorted(sections)
    ]


def add_block(blocks: list[dict], field_blocks: dict[str, list[str]], block: dict, field: str) -> None:
    blocks.append(block)
    field_blocks[field].append(block["text"])


def build_sample(
    entry: dict,
    *,
    sections: dict[int, dict],
    handovers: dict[int, str],
    outline: list[dict],
    rag_items: list[dict],
    hard_annotation: dict,
    evidence_review: dict | None,
    global_rules: str,
    serialize_blocks: bool = True,
) -> dict:
    query_index = int(entry["query_index"])
    section_number = int(entry["section"])
    subsection_number = int(entry["subsection"])
    section = sections[section_number]
    subsection = section["subsections"][subsection_number - 1]
    topic = "周六面包店与凌晨三点半"
    requirement = frozen_requirement(entry, section["title"], subsection["title"], topic)
    key_points = [requirement]
    recent = prior_subsections(sections, section_number, subsection_number)
    recent_text = "【最近内容】\n" + "\n\n".join(item["text"] for item in recent) if recent else "（故事开头，暂无前文）"
    handover_text = handovers.get(section_number - 1, "（这是第一节，无前文交接笔记）")
    rules = relevant_hard_rules(hard_annotation, entry["query"])
    stability_rules = [item for item in rules if item.get("rule_scope", "character_stability") != "relationship_stage"]
    relationship_rules = [item for item in rules if item.get("rule_scope") == "relationship_stage"]
    character_constraints = "\n".join(
        f"- [{item['id']}] {item['character']}: {item['constraint']}" for item in stability_rules
    ) or "（冻结任务未保留原始角色卡；无可重建的角色稳定约束）"
    arc_context = "\n".join(
        f"- [{item['id']}] {item['constraint']}" for item in relationship_rules
    ) or "（冻结任务未保留角色弧线，仅审计可追溯的人工 hard 规则）"
    character_context = "出场人物（由冻结 query 确定）: " + "、".join(
        sorted({item["character"] for item in rules})
    ) if rules else "（冻结任务未保留原始角色卡）"
    mandatory_events = Writer._build_mandatory_events(
        key_points, [], "", section_number, subsection_number
    )
    progress_context = Writer._build_progress_context(
        outline, section_number, subsection_number, len(section["subsections"]), key_points, ""
    )
    rag_context = format_rag(rag_items)
    style = {
        "emotion_intensity": 50,
        "sentence_preference": "balanced",
        "sensory_density": "medium",
        "dialogue_ratio": 0.3,
    }
    style_structured = StyleSummarizer.for_writer(style)
    density_instruction = _narrative_density_instruction(0.7)
    style_examples = build_style_examples(style)

    values = {
        "mandatory_events": mandatory_events,
        "character_constraints": character_constraints,
        "style_constraints": "",
        "beat_reminder": "",
        "progress_context": progress_context,
        "rules_context": global_rules,
        "topic": topic,
        "world_setting": "",
        "section": section_number,
        "subsection": subsection_number,
        "subsection_title": subsection["title"],
        "section_outline": f"第{section_number}节「{section['title']}」",
        "key_points": requirement,
        "sub_description": "（冻结 query 已承载本小节需求，历史 description 未单独持久化）",
        "narrative_density_instruction": density_instruction,
        "style_examples": style_examples,
        "emotion_intensity": style["emotion_intensity"],
        "sentence_preference": style["sentence_preference"],
        "sensory_density": style["sensory_density"],
        "dialogue_ratio": 30,
        "ranked_events": "（冻结任务的 EventGraph/Redis 状态不可用）",
        "world_facts": "（冻结任务的 WorldState/Redis 状态不可用）",
        "world_contradictions": "（无可恢复的矛盾警告）",
        "character_context": character_context,
        "arc_context": arc_context,
        "handover_context": handover_text,
        "summary_context": recent_text,
        "retrieved_context": rag_context,
        "target_words": 2000,
        "style_structured": style_structured,
    }
    template = WRITING_SECTION1_PROMPT if (section_number, subsection_number) == (1, 1) else WRITING_PROMPT
    user_prompt = template.format(**values)
    total_tokens = estimate_tokens(WRITER_SYSTEM_PROMPT) + estimate_tokens(user_prompt)

    blocks: list[dict] = []
    field_blocks: dict[str, list[str]] = defaultdict(list)
    add_block(blocks, field_blocks, make_block(
        "fixed:system", "fixed_prompt", WRITER_SYSTEM_PROMPT,
        source_id="prompt:WRITER_SYSTEM_PROMPT", injection_position="system message",
    ), "system")
    empty_values = {key: "" for key in values}
    user_scaffold = template.format(**empty_values)
    add_block(blocks, field_blocks, make_block(
        "fixed:user-scaffold", "fixed_prompt", user_scaffold,
        source_id="prompt:WRITING_PROMPT", injection_position="user message scaffold",
    ), "user_scaffold")
    current_fields = {
        "mandatory_events": mandatory_events,
        "progress_context": progress_context,
        "topic": topic,
        "section_outline": values["section_outline"],
        "key_points": requirement,
        "sub_description": values["sub_description"],
    }
    for field, text in current_fields.items():
        add_block(blocks, field_blocks, make_block(
            f"current:{field}", "current_writing", text,
            source_id=f"rag-annotation:q{query_index:02d}", injection_position=f"user prompt field {{{field}}}",
        ), field)
    for item in recent:
        add_block(blocks, field_blocks, make_block(
            f"recent:{item['source_id']}", "recent_original", item["text"],
            source_id=item["source_id"], injection_position="user prompt field {summary_context}",
        ), "summary_context")
    add_block(blocks, field_blocks, make_block(
        "handover:previous-section", "handover", handover_text,
        source_id=f"golden:handover:S{max(section_number - 1, 0)}", injection_position="user prompt field {handover_context}",
    ), "handover_context")
    for field, text, source_id in (
        ("character_constraints", character_constraints, "human-hard-rules:character-stability"),
        ("character_context", character_context, "frozen-query:actor-names"),
        ("arc_context", arc_context, "human-hard-rules:relationship-stage"),
    ):
        add_block(blocks, field_blocks, make_block(
            f"character:{field}", "character_relation", text,
            source_id=source_id, injection_position=f"user prompt field {{{field}}}",
        ), field)
    if global_rules:
        add_block(blocks, field_blocks, make_block(
            "other:global-rules", "other", global_rules,
            source_id="rules.db:enabled", injection_position="user prompt field {rules_context}",
        ), "rules_context")
    for field, text in (
        ("world_facts", values["world_facts"]),
        ("world_contradictions", values["world_contradictions"]),
        ("ranked_events", values["ranked_events"]),
    ):
        add_block(blocks, field_blocks, make_block(
            f"world:{field}", "world_event", text,
            source_id="unavailable:frozen-redis-state", injection_position=f"user prompt field {{{field}}}", available=False,
        ), field)
    for index, item in enumerate(rag_items, 1):
        rag_block = make_block(
            f"rag:{index}:{item.get('id', '')}", "rag", item.get("text", ""),
            source_id=item.get("id") or f"chroma:q{query_index}:rank{index}",
            injection_position="user prompt field {retrieved_context}",
        )
        rag_block.update({
            "section": item.get("section"),
            "subsection": item.get("subsection"),
            "title": item.get("title", ""),
        })
        add_block(blocks, field_blocks, rag_block, "retrieved_context")
    for field, text in (
        ("narrative_density_instruction", density_instruction),
        ("style_examples", style_examples),
    ):
        add_block(blocks, field_blocks, make_block(
            f"style:{field}", "style_examples", text,
            source_id="style:four-control-default-reconstruction", injection_position=f"user prompt field {{{field}}}",
        ), field)

    ledger = build_ledger(blocks, total_tokens)
    duplicates = diagnose_duplicates(blocks)
    required = [
        {
            "item_id": f"current:q{query_index:02d}",
            "requirement": "hard_required",
            "source_id": f"rag-annotation:q{query_index:02d}",
            "description": "current subsection goal and frozen writing request",
        }
    ]
    required.extend({
        "item_id": f"rule:q{query_index:02d}:{item['id']}",
        "requirement": "hard_required",
        "source_id": item["id"],
        "description": item["constraint"],
        "rule_scope": item.get("rule_scope", "character_stability"),
    } for item in rules)
    if recent:
        required.append({
            "item_id": f"continuity:q{query_index:02d}",
            "requirement": "continuity_required",
            "source_id": recent[-1]["source_id"],
            "description": "immediately previous subsection original text",
            "text_hash": recent[-1]["text_hash"],
        })
    evidence_items = human_evidence_manifest(evidence_review, query_index) if evidence_review else []
    returned_rag_ids = {str(item.get("id", "")) for item in rag_items}
    for item in evidence_items:
        item["present_in_current_prompt"] = item["source_id"] in returned_rag_ids
    required.extend(evidence_items)
    for category in ("world_event", "style_examples", "other"):
        required.append({
            "item_id": f"optional:q{query_index:02d}:{category}",
            "requirement": "optional_context",
            "source_id": f"category:{category}",
            "description": f"optional {category} context; presence does not prove relevance",
        })
    validate_required_manifest(required)
    protected_block_ids = {
        block["block_id"]
        for block in blocks
        if block["category"] in {"fixed_prompt", "current_writing", "character_relation", "handover"}
    }
    if recent:
        protected_block_ids.add(f"recent:{recent[-1]['source_id']}")
    required_evidence_sources = {item["source_id"] for item in evidence_items}
    protected_block_ids.update(
        block["block_id"] for block in blocks
        if block["category"] == "rag" and block["source_id"] in required_evidence_sources
    )
    removable_blocks = [
        block for block in blocks
        if block["trimmable"] and block["block_id"] not in protected_block_ids and block.get("available", True)
    ]
    return {
        "query_index": query_index,
        "section": section_number,
        "subsection": subsection_number,
        "query": entry["query"],
        "prompt_hash": sha256_text(WRITER_SYSTEM_PROMPT + "\n" + user_prompt),
        "prompt_rendered_without_llm": True,
        "token_method": "estimated_token: Writer._estimate_prompt_tokens compatible",
        "ledger": ledger,
        "blocks": [serialize_block(block) for block in blocks] if serialize_blocks else blocks,
        "duplicates": duplicates,
        "required_manifest": required,
        "theoretical_non_required_ceiling": {
            "estimated_tokens": sum(block["estimated_tokens"] for block in removable_blocks),
            "block_ids": [block["block_id"] for block in removable_blocks],
            "warning": "upper bound only; optional or older context is not automatically irrelevant",
        },
        "recent_original_count": len(recent),
        "rag_item_count": len(rag_items),
    }


def aggregate(samples: list[dict]) -> dict:
    totals = [sample["ledger"]["total_estimated_tokens"] for sample in samples]
    category_means = {}
    for category in SOURCE_CONTRACTS:
        values = [sample["ledger"]["categories"][category]["estimated_tokens"] for sample in samples]
        category_means[category] = round(mean(values), 1)
    top_three = sorted(category_means.items(), key=lambda item: item[1], reverse=True)[:3]
    mean_total = round(mean(totals), 1)
    duplicate_mean = round(mean(sample["duplicates"]["provable_duplicate_tokens"] for sample in samples), 1)
    optional_categories = {"world_event", "style_examples", "other"}
    optional_ceiling = round(sum(category_means[category] for category in optional_categories), 1)
    non_required_ceiling = round(mean(
        sample["theoretical_non_required_ceiling"]["estimated_tokens"] for sample in samples
    ), 1)
    evidence_items = [
        item for sample in samples for item in sample["required_manifest"]
        if item["requirement"] == "evidence_required"
    ]
    evidence_present = sum(bool(item["present_in_current_prompt"]) for item in evidence_items)
    return {
        "query_count": len(samples),
        "mean_total_estimated_tokens": mean_total,
        "min_total_estimated_tokens": min(totals),
        "max_total_estimated_tokens": max(totals),
        "mean_category_tokens": category_means,
        "top_three_sources": [{"category": key, "mean_estimated_tokens": value} for key, value in top_three],
        "mean_rag_share": round(category_means["rag"] / mean_total, 4) if mean_total else 0.0,
        "mean_recent_original_share": round(category_means["recent_original"] / mean_total, 4) if mean_total else 0.0,
        "recent_original_is_largest_source": bool(top_three and top_three[0][0] == "recent_original"),
        "mean_provable_duplicate_tokens": duplicate_mean,
        "provable_duplicate_share": round(duplicate_mean / mean_total, 4) if mean_total else 0.0,
        "mean_optional_context_ceiling_tokens": optional_ceiling,
        "optional_context_ceiling_share": round(optional_ceiling / mean_total, 4) if mean_total else 0.0,
        "mean_theoretical_non_required_ceiling_tokens": non_required_ceiling,
        "theoretical_non_required_ceiling_share": round(non_required_ceiling / mean_total, 4) if mean_total else 0.0,
        "theoretical_non_required_reduction_is_not_a_recommendation": True,
        "human_evidence_items": len(evidence_items),
        "human_evidence_sources_present_in_legacy_prompt": evidence_present,
        "human_evidence_presence_rate": round(evidence_present / len(evidence_items), 4) if evidence_items else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rag_annotation = load_json(DEFAULT_RAG)
    style_config = load_json(DEFAULT_STYLE)
    hard_annotation = load_json(DEFAULT_CHARACTER)
    evidence_review = load_json(REVIEW_PATH)
    story_path = ROOT / style_config["source_file"]
    sections = parse_story(story_path)
    handovers = parse_handovers(story_path)
    outline = build_outline(sections)
    global_rules, rules_snapshot = read_global_rules_readonly(ROOT / "rules.db")
    store = VectorStore()
    samples = []
    retrieval = []
    for entry in rag_annotation["entries"]:
        items = store.search_with_meta(entry["query"], k=5, task_id=rag_annotation["task_id"])
        retrieval.append({
            "query_index": int(entry["query_index"]),
            "returned": len(items),
            "source_ids": [item.get("id", "") for item in items],
            "filter": {"task_id": rag_annotation["task_id"]},
            "elapsed_ms": store.last_search_trace.get("elapsed_ms"),
        })
        samples.append(build_sample(
            entry,
            sections=sections,
            handovers=handovers,
            outline=outline,
            rag_items=items,
            hard_annotation=hard_annotation,
            evidence_review=evidence_review,
            global_rules=global_rules,
        ))
    summary = aggregate(samples)
    report = {
        "schema_version": 1,
        "purpose": "Phase 3 closure and Phase 4 entry Writer context census",
        "offline_llm_calls": 0,
        "writer_generation_calls": 0,
        "production_behavior_changed": False,
        "context_manager_contract": "most recent 3 subsection originals + handover; running_summary remains removed",
        "phase3_closure": {
            "status": "closed_experiments_not_promoted_production_legacy_frozen",
            "production_contract": "shared_collection + original task_id filter + current RAG_TOP_K",
            "experimental_assets": [
                "QueryPlannerV2", "V2Reranker", "ContextCompactor", "StructuredContextCompactor",
                "EventChunker", "EventShadowStore",
            ],
            "findings": {
                "sentence_compaction": "fact evidence lost",
                "structured_windows": "no profile met evidence completeness and 20% reduction together",
                "event_chunker_offline": "feasible: 9/9 verifiable facts and 21.43% reduction",
                "direct_event_vector_retrieval": "failed precision, retention, late, fact-parent and token gates",
            },
            "shadow_event_task_id": "80d1a9c6-4d8d-566a-82a7-192bd172d68c",
            "shadow_event_count_retained": 45,
            "cleanup_executed": False,
        },
        "input_provenance": {
            "frozen_queries": "tests/rag_annotation_07d1391e.json",
            "golden_story": style_config["source_file"],
            "golden_story_sha256": hashlib.sha256(story_path.read_bytes()).hexdigest().upper(),
            "recent_original": "exact preceding subsection text reconstructed from golden story",
            "handover": "exact previous-section entry from golden story appendix",
            "rag": "real legacy shared-collection query with original task_id filter",
            "rules_snapshot": rules_snapshot,
            "character_relation": "human-reviewed hard-rule audit overlay; original historical character cards/arcs unavailable",
            "style": "current deterministic four-control defaults; historical profile and LLM-generated behavior text unavailable",
            "world_event": "historical Redis/EventGraph/WorldState unavailable; explicit unavailable placeholders only",
        },
        "source_contracts": SOURCE_CONTRACTS,
        "writer_call_chain": [
            "coordinator._run_writing_stage loads task state and auxiliary contexts",
            "Writer.run builds legacy RAG, character, handover, ContextManager, world/event, rules and style fields",
            "WRITING_PROMPT.format renders the user message and WRITER_SYSTEM_PROMPT supplies the system message",
            "Writer._generate_with_retry would call the LLM; this census stops before that boundary",
        ],
        "duplicate_status": "per sample in samples[].duplicates; no source category is presumed duplicate",
        "non_injected_computed_inputs": {
            "style_structured": "Writer computes and passes this value, but current WRITING_PROMPT templates do not reference {style_structured}",
        },
        "retrieval_runs": retrieval,
        "summary": summary,
        "phase4_entry": {
            "eligible_for_shadow_context_broker_implementation": True,
            "priority": [
                "govern recent_original at item level while retaining ContextManager storage contract",
                "deduplicate recent_original against RAG before any lossy compression",
                "account fixed/style/optional blocks under a single total budget",
            ],
            "first_batch_constraints": [
                "hard_required content retention = 100%",
                "continuity_required previous-subsection retention = 100%",
                "human-reviewed evidence sources already present in legacy input retention = 100%",
                "shadow only; Writer continues to consume legacy prompt",
            ],
        },
        "limitations": [
            "The historical task row and Redis context are unavailable, so this is a traceable reconstruction, not a byte-identical replay of the 2026-07-15 Writer prompt.",
            "No must_recall_facts, gold sections or review answers are used to assemble the prompt; human evidence labels are consulted only after assembly to build the required manifest.",
            "estimated_token uses the project's stable local heuristic and is not the serving model tokenizer.",
            "The optional-context ceiling assumes every optional block could be omitted and is an upper bound, not a recommended removal plan.",
            "Low lexical overlap is never classified as irrelevance.",
        ],
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": summary, "phase4_entry": report["phase4_entry"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
