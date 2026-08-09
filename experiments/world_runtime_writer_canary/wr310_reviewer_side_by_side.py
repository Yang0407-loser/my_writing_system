"""WR3.10+ Reviewer context side-by-side harness (offline, read-only).

Compares the four Reviewer context inputs (handover_chain,
character_consistency_context, relation_context, subplot_context) between the
legacy chain and the WR projection, per frozen C2.1-R10 subsection.

- handover_chain has both sides in the frozen WR3.9 snapshot -> field-level
  structural comparison (six legacy note fields).
- character / relation / subplot legacy inputs live in task stores that are
  NOT part of the frozen snapshot -> the harness records their providers and
  marks them ``unavailable_in_frozen_snapshot``, so a real-task snapshot is
  the only remaining step before a Reviewer read-switch decision.

Zero LLM, zero state mutation; one frozen JSON report is written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.writing.world_runtime_handover_projection import project_handover
from app.writing.world_runtime_reviewer_projection import (
    project_reviewer_context,
)
from app.writing.world_runtime_state_committer import CommittedWorldState


HANDOVER_FIELDS = (
    "foreshadowing",
    "character_state",
    "open_threads",
    "new_facts",
    "found_contradictions",
    "arc_progress",
)

LEGACY_CONTEXT_PROVIDERS = {
    "character_consistency_context": (
        "CharacterFormatter.build_context(characters, character_arcs)"
    ),
    "relation_context": "character_relation_store.build_relation_context(task_id)",
    "subplot_context": "subplot_manager.build_subplot_context(task_id)",
}


def _json_safe(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _item_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    if value in (None, "", "[]"):
        return 0
    return 1


def render_handover_chain_text(notes: list[dict[str, Any]]) -> str:
    """Render the same global-review handover_chain format as coordinator.py."""
    lines = []
    for note in notes:
        from_section = note.get("from_section", "?")
        to_section = note.get("to_section", "?")
        foreshadowing = str(note.get("foreshadowing", "") or "")[:80]
        lines.append(f"第{from_section}节→第{to_section}节: 伏笔={foreshadowing}")
    return "\n".join(lines)


def compare_handover_field(
    field: str,
    legacy_value: Any,
    wr_value: Any,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    status = coverage[field]["status"]
    if status == "projected_from_wr":
        value_status = (
            "identical"
            if _json_safe(legacy_value) == _json_safe(wr_value)
            else "different"
        )
    else:
        value_status = "legacy_only"
    return {
        "field": field,
        "status": status,
        "legacy_item_count": _item_count(legacy_value),
        "wr_item_count": _item_count(wr_value),
        "legacy_type": type(legacy_value).__name__,
        "wr_type": type(wr_value).__name__,
        "value_status": value_status,
    }


def compare_context_field(
    field: str,
    wr_reviewer: dict[str, Any],
) -> dict[str, Any]:
    if field == "character_consistency_context":
        return {
            "field": field,
            "wr_status": "projected_from_wr",
            "legacy_status": "unavailable_in_frozen_snapshot",
            "legacy_provider": LEGACY_CONTEXT_PROVIDERS[field],
            "wr_text_length": len(str(wr_reviewer.get(field, ""))),
        }
    wr_status = wr_reviewer["coverage"][{
        "relation_context": "relation_context_status",
        "subplot_context": "subplot_context_status",
    }[field]]
    return {
        "field": field,
        "wr_status": wr_status,
        "legacy_status": "unavailable_in_frozen_snapshot",
        "legacy_provider": LEGACY_CONTEXT_PROVIDERS[field],
        "wr_text": str(wr_reviewer.get(field, "")),
    }


def compare_legacy_and_wr_notes(
    legacy_note: dict[str, Any],
    wr_handover: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    wr_note = wr_handover["note"]
    coverage = wr_handover["field_coverage"]
    wr_note = {
        **wr_note,
        "from_section": legacy_note.get("from_section"),
        "to_section": legacy_note.get("to_section"),
    }
    field_rows = [
        compare_handover_field(
            field, legacy_note.get(field), wr_note.get(field), coverage
        )
        for field in HANDOVER_FIELDS
    ]
    legacy_text = render_handover_chain_text([legacy_note])
    wr_text = render_handover_chain_text([wr_note])
    rendered = {
        "legacy_chain_text": legacy_text,
        "wr_chain_text": wr_text,
        "rendered_identical": legacy_text == wr_text,
    }
    return field_rows, rendered


def build_subsection_report(
    subsection: int,
    committed: CommittedWorldState,
    legacy_note: dict[str, Any],
) -> dict[str, Any]:
    wr_reviewer = project_reviewer_context(committed)
    wr_handover = project_handover(committed)
    field_rows, rendered = compare_legacy_and_wr_notes(
        legacy_note, wr_handover
    )
    context_rows = [
        compare_context_field(field, wr_reviewer)
        for field in LEGACY_CONTEXT_PROVIDERS
    ]
    fields = [*field_rows, *context_rows]
    projected = sum(
        1
        for row in fields
        if row.get("status") == "projected_from_wr"
        or row.get("wr_status") == "projected_from_wr"
    )
    legacy_only = sum(
        1
        for row in fields
        if row.get("status") == "legacy_only_not_projected"
        or row.get("wr_status") == "legacy_only_not_projected"
    )
    unavailable_legacy = sum(
        1
        for row in fields
        if row.get("legacy_status") == "unavailable_in_frozen_snapshot"
    )
    return {
        "subsection": subsection,
        "fields": fields,
        "handover_chain_rendered": rendered,
        "summary": {
            "field_count": len(fields),
            "projected_field_count": projected,
            "legacy_only_field_count": legacy_only,
            "legacy_provider_unavailable_count": unavailable_legacy,
        },
    }


def aggregate_reports(
    reports: list[dict[str, Any]],
) -> dict[str, Any]:
    field_statuses: dict[str, set[str]] = {}
    for report in reports:
        for row in report["fields"]:
            field = row["field"]
            statuses = {
                row.get("status"),
                row.get("wr_status"),
                row.get("legacy_status"),
            } - {None}
            field_statuses.setdefault(field, set()).update(statuses)
    rendered_identical = all(
        report["handover_chain_rendered"]["rendered_identical"]
        for report in reports
    )
    return {
        "subsection_count": len(reports),
        "field_statuses": {
            field: sorted(statuses)
            for field, statuses in sorted(field_statuses.items())
        },
        "handover_chain_rendered_identical_all": rendered_identical,
        "legacy_sources_still_required": [
            field
            for field, statuses in sorted(field_statuses.items())
            if "unavailable_in_frozen_snapshot" in statuses
        ],
        "recommendation": (
            "handover_chain_comparable;"
            "character_relation_subplot_need_real_task_snapshot"
        ),
    }


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".world_runtime_wr39_dual_chain_runtime"
CANARY_COMMITS = ROOT / ".world_runtime_state_commit_canary_runtime/c21r10/private/commits"
OUTPUT = ROOT / "reports" / "wr310-reviewer-side-by-side-2026-08-07.json"


def _load_legacy_note(subsection: int) -> dict[str, Any]:
    payload = json.loads(
        (
            RUNTIME / "private/outputs" / f"S{subsection}.handover.json"
        ).read_text(encoding="utf-8")
    )
    note = dict(payload.get("note") or {})
    note["from_section"] = subsection - 1
    note["to_section"] = subsection
    return note


def _load_committed(subsection: int) -> CommittedWorldState:
    return CommittedWorldState.model_validate(
        json.loads(
            (CANARY_COMMITS / f"S{subsection}.json").read_text(encoding="utf-8")
        )
    )


def main() -> None:
    reports = []
    for subsection in range(1, 4):
        report = build_subsection_report(
            subsection,
            _load_committed(subsection),
            _load_legacy_note(subsection),
        )
        reports.append(report)
    aggregate = aggregate_reports(reports)
    payload = {
        "schema_version": "wr310-reviewer-side-by-side-v1",
        "note": (
            "offline side-by-side over frozen C2.1-R10 commits and WR3.9 "
            "legacy handover outputs; legacy character/relation/subplot "
            "stores are not part of the frozen snapshot"
        ),
        "aggregate": aggregate,
        "subsections": reports,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
