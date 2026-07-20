"""Deterministic, read-only compilation of current story state for shadow use."""

from __future__ import annotations

import hashlib
import json
import re

from .contracts import StateAssertion, StateFrame, StoryStateSnapshot


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    return max(1, int(chinese * 1.5 + (len(text) - chinese) * 0.3))


class StateFrameCompiler:
    """Selects current facts without planning events or rewriting source state."""

    CATEGORY_PREDICATES = {
        "temporal_state": frozenset({"current_time_anchor", "time_anchor"}),
        "location_state": frozenset({"current_location", "location_state", "location_operation"}),
        "character_presence": frozenset({"character_presence", "character_absence"}),
        "persistent_state": frozenset({"character_state", "continuity_state", "world_fact"}),
        "relationship_state": frozenset({"relationship_stage"}),
    }

    def compile(self, snapshot: StoryStateSnapshot) -> StateFrame:
        groups: dict[str, list[StateAssertion]] = {
            name: [] for name in self.CATEGORY_PREDICATES
        }
        unresolved: list[StateAssertion] = []
        excluded: list[str] = []

        for assertion in snapshot.assertions:
            if assertion.status in {"unknown", "conflicted"}:
                unresolved.append(assertion)
                continue
            if assertion.predicate == "open_loop":
                groups.setdefault("open_loops", []).append(assertion)
                continue
            category = next(
                (
                    name for name, predicates in self.CATEGORY_PREDICATES.items()
                    if assertion.predicate in predicates
                ),
                None,
            )
            if assertion.status == "confirmed" and category is not None:
                groups[category].append(assertion)
            else:
                excluded.append(assertion.assertion_id)

        open_loops = groups.get("open_loops", [])
        included = [
            assertion
            for name in self.CATEGORY_PREDICATES
            for assertion in groups[name]
        ] + open_loops + unresolved
        evidence_ids = {
            evidence_id
            for assertion in included
            for evidence_id in assertion.evidence_ids
        }
        evidence = [
            item for item in snapshot.evidence if item.evidence_id in evidence_ids
        ]
        core = {
            "frame_id": f"{snapshot.task_id}:state:S{snapshot.section}.{snapshot.subsection}",
            "task_id": snapshot.task_id,
            "section": snapshot.section,
            "subsection": snapshot.subsection,
            **{
                name: [item.model_dump() for item in groups[name]]
                for name in self.CATEGORY_PREDICATES
            },
            "open_loops": [item.model_dump() for item in open_loops],
            "unknowns_and_conflicts": [item.model_dump() for item in unresolved],
            "evidence": [item.model_dump(exclude={"excerpt"}) for item in evidence],
            "excluded_assertion_ids": sorted(excluded),
            "source_hash": snapshot.source_hash,
        }
        rendered = self.render_fields(core)
        return StateFrame(
            **core,
            frame_hash=_stable_hash(core),
            estimated_tokens=_estimate_tokens(rendered),
        )

    @staticmethod
    def render_fields(core: dict) -> str:
        labels = (
            ("temporal_state", "TIME"),
            ("location_state", "LOCATION"),
            ("character_presence", "PRESENCE"),
            ("persistent_state", "PERSISTENT"),
            ("relationship_state", "RELATIONSHIP"),
            ("open_loops", "OPEN"),
            ("unknowns_and_conflicts", "UNKNOWN/CONFLICT"),
        )
        lines = [f"STATE {core['section']}.{core['subsection']}"]
        for key, label in labels:
            for item in core[key]:
                lines.append(f"[{label}] {item['value']} <{','.join(item['evidence_ids'])}>")
        return "\n".join(lines)

    def render(self, frame: StateFrame) -> str:
        return self.render_fields(frame.model_dump())
