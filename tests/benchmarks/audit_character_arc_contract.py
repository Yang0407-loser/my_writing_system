"""Build the public character-arc contract impact audit without LLM calls."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


E7_TASK_ID = "e7cb9ac2-c76c-44e8-a9de-4d470c238872"
B5_TASK_ID = "b5ddb41c-da52-47a1-a03e-9278a0b2ab12"
EXCLUDED_TASK_ID = "6d8187a1-8a53-47b3-9d90-1f3e4bdc3961"


def _estimate_tokens(text: str) -> int:
    chinese = sum("\u4e00" <= char <= "\u9fff" for char in text)
    return int(chinese * 1.5 + (len(text) - chinese) * 0.3)


def _event_counts(events: list[dict]) -> dict:
    by_character: Counter[str] = Counter()
    by_section: Counter[int] = Counter()
    related_pairs: set[tuple[str, str]] = set()
    explicit_pairs: set[tuple[str, str]] = set()
    writer_tokens = 0
    missing_classification = 0

    for event in events:
        if not isinstance(event, dict) or event.get("type") != "arc_milestone":
            continue
        event_id = str(event.get("event_id", ""))
        by_character[str(event.get("character_id", ""))] += 1
        by_section[int(event.get("section", 0) or 0)] += 1
        writer_tokens += _estimate_tokens(str(event.get("description", "")))
        if not event.get("classification"):
            missing_classification += 1
        metadata = event.get("relation_metadata", {}) or {}
        for related in event.get("related_events", []) or []:
            pair = tuple(sorted((event_id, str(related))))
            related_pairs.add(pair)
            if str(related) in metadata:
                explicit_pairs.add(pair)

    milestones = sum(by_character.values())
    same_character = sum(max(0, count - 1) for count in by_character.values())
    same_section = sum(count * (count - 1) // 2 for count in by_section.values())
    return {
        "availability": "available",
        "milestones": milestones,
        "milestones_by_character_id_count": dict(sorted(by_character.items())),
        "milestones_by_section": {str(key): value for key, value in sorted(by_section.items())},
        "legacy_link_operations": same_character + same_section,
        "same_character_consecutive_links": same_character,
        "same_section_pairwise_links": same_section,
        "stored_unique_undirected_edges": len(related_pairs),
        "edges_with_explicit_metadata": len(explicit_pairs),
        "proven_causal_edges": len(explicit_pairs),
        "legacy_unclassified": missing_classification,
        "v2_compatibility_view": {
            "soft_arc_progress": missing_classification,
            "legacy_unclassified": missing_classification,
            "hard_arc_transition": 0,
        },
        "offline_evidence_classification": {
            "unresolved": missing_classification,
            "structurally_proven_hard": 0,
        },
        "v2_legal_edges_from_existing_metadata": len(explicit_pairs),
        "writer_visible_arc_event_estimated_tokens": writer_tokens,
    }


def _load_task_events_read_only(db_path: Path, task_id: str) -> dict:
    if not db_path.exists():
        return {"availability": "unavailable", "reason": "tasks.db missing"}
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT events_json, status FROM task_history WHERE task_id = ?", (task_id,)
        ).fetchone()
    finally:
        connection.close()
    if not row:
        return {"availability": "unavailable", "reason": "task history row missing"}
    try:
        events = json.loads(row[0] or "[]")
    except json.JSONDecodeError:
        return {"availability": "unavailable", "reason": "events_json invalid"}
    result = _event_counts(events if isinstance(events, list) else [])
    result["task_status"] = row[1]
    return result


def build_report(root: Path) -> dict:
    db_path = root / "tasks.db"
    task_results = {
        E7_TASK_ID: _load_task_events_read_only(db_path, E7_TASK_ID),
        B5_TASK_ID: _load_task_events_read_only(db_path, B5_TASK_ID),
    }
    available = [item for item in task_results.values() if item.get("availability") == "available"]
    totals = {
        "tasks_available": len(available),
        "legacy_milestones": sum(item.get("milestones", 0) for item in available),
        "legacy_link_operations": sum(item.get("legacy_link_operations", 0) for item in available),
        "legacy_same_section_pairwise_links": sum(item.get("same_section_pairwise_links", 0) for item in available),
        "stored_unique_undirected_edges": sum(item.get("stored_unique_undirected_edges", 0) for item in available),
        "proven_causal_edges": sum(item.get("proven_causal_edges", 0) for item in available),
        "structurally_proven_hard": sum(
            item.get("offline_evidence_classification", {}).get("structurally_proven_hard", 0)
            for item in available
        ),
        "unresolved_without_new_inference": sum(
            item.get("offline_evidence_classification", {}).get("unresolved", 0)
            for item in available
        ),
        "v2_legal_edges_from_existing_metadata": sum(
            item.get("v2_legal_edges_from_existing_metadata", 0) for item in available
        ),
        "writer_visible_arc_event_estimated_tokens": sum(
            item.get("writer_visible_arc_event_estimated_tokens", 0) for item in available
        ),
    }
    return {
        "schema_version": "character_arc_contract_impact_audit.v1",
        "date": "2026-07-21",
        "scope": {
            "writer_llm_calls": 0,
            "new_generation_runs": 0,
            "private_prose_committed": False,
            "data_source": "read-only tasks.db events_json; no review or candidate files",
            "excluded_cost_sample": EXCLUDED_TASK_ID,
            "excluded_reason": "worker interruption caused Redis redelivery",
        },
        "decision_gate": {
            "contract_v2_implementation_allowed": True,
            "reason": "milestones enter Writer arc context and mandatory event context; EventGraph edges affect causal expansion and handover extraction",
        },
        "production_impact_chain": [
            {"stage": "planning", "module": "app/agents/character_manager.py::plan_arcs", "output": "character_arcs", "changes_writer_messages": False, "changes_generation_count": True, "production_effect": True},
            {"stage": "formatting", "module": "app/agents/character_formatter.py::build_arc_context", "output": "arc_context", "changes_writer_messages": True, "changes_generation_count": False, "production_effect": True},
            {"stage": "graph_query", "module": "app/agents/writer.py::Writer.run", "output": "ranked_events_str", "changes_writer_messages": True, "changes_generation_count": False, "production_effect": True},
            {"stage": "pre_check", "module": "app/rule_checks.py::pre_check", "output": "required event prompt", "changes_writer_messages": True, "changes_generation_count": False, "production_effect": True},
            {"stage": "causal_expansion", "module": "app/narrative_event.py::EventGraph.expand_causal", "output": "causal RAG appendix", "changes_writer_messages": True, "changes_generation_count": False, "production_effect": True},
            {"stage": "handover", "module": "app/agents/writer.py::_extract_handover", "output": "open_threads prompt", "changes_writer_messages": False, "changes_generation_count": True, "production_effect": True},
            {"stage": "post_check", "module": "app/rule_checks.py::post_check", "output": "warning", "changes_writer_messages": False, "changes_generation_count": False, "production_effect": False},
            {"stage": "commit", "module": "app/writing/state_committer.py::commit_handover_effects", "output": "event status", "changes_writer_messages": False, "changes_generation_count": False, "changes_checkpoint": True, "production_effect": True},
        ],
        "task_results": task_results,
        "totals": totals,
        "contract_v2": {
            "default_version": "v1",
            "legacy_unclassified_runtime_view": "soft_arc_progress",
            "legacy_storage_rewritten": False,
            "hard_limit_per_character_section": 2,
            "hard_requires_complete_transition_and_provenance": True,
            "non_injectable": ["observational_texture", "ordinary_plot_event", "unsupported_planning_inference", "unresolved"],
            "allowed_edge_types": ["explicit_causal", "explicit_dependency", "ordered_hard_transition"],
            "same_section_pairwise_edges": False,
        },
        "conclusions": {
            "enters_writer": True,
            "adds_writer_tokens": True,
            "arc_post_check_causes_retry": False,
            "edges_have_real_causal_evidence": False,
            "true_hard_count": "unavailable from legacy structure; zero structurally provable without reusing evaluation labels",
            "v2_worth_implementing": True,
            "v1_preserved_by_default": True,
            "next_demo_authorization": "eligible after directed tests pass; one real task only",
        },
        "next_demo_metrics": [
            "EventGraph edge count", "hard/soft/observational counts", "Writer call count",
            "arc warning count", "input tokens", "subsection goal completion", "user usability",
        ],
    }


def render_markdown(report: dict) -> str:
    totals = report["totals"]
    rows = []
    for task_id, item in report["task_results"].items():
        rows.append(
            f"| `{task_id[:8]}` | {item.get('availability')} | {item.get('milestones', 'N/A')} | "
            f"{item.get('legacy_link_operations', 'N/A')} | {item.get('same_section_pairwise_links', 'N/A')} | "
            f"{item.get('proven_causal_edges', 'N/A')} | {item.get('v2_legal_edges_from_existing_metadata', 'N/A')} |"
        )
    return f"""# Character Arc Contract Impact Audit

Date: 2026-07-21

## Decision

Contract V2 implementation is justified because character-arc milestones reach Writer twice: through the subsection arc context and through EventGraph/pre-check. EventGraph relations also participate in causal RAG expansion and handover extraction. Arc post-check remains warning-only and causes no retry.

Production remains `CHARACTER_ARC_CONTRACT_VERSION=v1` by default. Legacy checkpoints are read through a soft compatibility view and are not rewritten.

## Fixed Tasks

| Task | Availability | Milestones | V1 link operations | Same-section pairwise | Proven causal edges | V2 legal existing edges |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

Totals: {totals['legacy_milestones']} legacy milestones and {totals['legacy_link_operations']} link operations, including {totals['legacy_same_section_pairwise_links']} same-section pairwise operations. Proven causal edges: {totals['proven_causal_edges']}.

The legacy schema has no before/trigger/after transition fields or provenance. Therefore {totals['structurally_proven_hard']} milestones are structurally provable as hard and {totals['unresolved_without_new_inference']} remain unresolved for evidence classification. Compatibility treats unclassified legacy milestones as soft; it does not reuse previous human/Codex evaluation labels.

## Production Impact

- `CharacterFormatter.build_arc_context` injects subsection milestones into Writer messages.
- `EventGraph.query_relevant` and `pre_check` append the same planning material to event context; V1 treats every arc milestone as mandatory.
- `expand_causal` previously expanded every same-section event even without an explicit edge.
- `_extract_handover` sends up to ten arc events to a separate handover LLM call.
- `post_check` only logs warnings and does not retry, rollback, or block output.
- `StateCommitter` can update EventGraph state after generation.

## V2 Contract

- Only complete, sourced state changes can remain `hard_arc_transition`; maximum two per character per chapter/section.
- `soft_arc_progress` is non-mandatory reference context.
- observational, ordinary plot, unsupported, and unresolved milestones are not injected as arc events.
- Edges require explicit causality/dependency or an exact hard-state chain.
- Same-section position alone never creates an edge or causal expansion.

## Limits

No Writer or LLM was called. The duplicate/redelivered `6d8187a1...` task was excluded from cost evidence. Legacy data cannot validate how a newly generated V2 plan will distribute classifications; that requires one separately authorized real Demo.

## Next Demo

If directed tests pass, run one task with `CHARACTER_ARC_CONTRACT_VERSION=v2`. Observe edge and classification counts, Writer calls/tokens, arc warnings, subsection goal completion, and whether the draft is easier to continue. Do not use frozen Precision/Recall as the primary conclusion.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_report(root)
    json_output = args.json_output or root / "reports" / "character-arc-contract-impact-audit.json"
    markdown_output = args.markdown_output or root / "reports" / "character-arc-contract-impact-audit-2026-07-21.md"
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_output), "markdown": str(markdown_output), "totals": report["totals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
