"""Read-only projection over existing story-state sources for shadow compilation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import SourceEvidence, StateAssertion, StoryStateSnapshot


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _hash_text(payload)


class StoryStateView:
    """Builds an immutable snapshot without owning or mutating story state."""

    def __init__(self, *, task_id: str, section: int, subsection: int) -> None:
        self.task_id = task_id
        self.section = section
        self.subsection = subsection

    def project(
        self,
        sources: Iterable[Mapping[str, Any]],
        assertions: Iterable[Mapping[str, Any]],
    ) -> StoryStateSnapshot:
        evidence = [self._evidence(item) for item in sources]
        evidence_ids = {item.evidence_id for item in evidence}
        projected = [self._assertion(item, evidence_ids) for item in assertions]
        open_loops = [item for item in projected if item.predicate == "open_loop"]
        hard_constraints = [item for item in projected if item.predicate == "hard_constraint"]
        canonical = {
            "task_id": self.task_id,
            "section": self.section,
            "subsection": self.subsection,
            "evidence": [item.model_dump(exclude={"excerpt"}) for item in evidence],
            "assertions": [item.model_dump() for item in projected],
        }
        return StoryStateSnapshot(
            task_id=self.task_id,
            section=self.section,
            subsection=self.subsection,
            evidence=evidence,
            assertions=projected,
            open_loops=open_loops,
            hard_constraints=hard_constraints,
            source_hash=_stable_hash(canonical),
        )

    def project_runtime(
        self,
        *,
        current_outline: Mapping[str, Any],
        world_state: Any = None,
        event_graph: Any = None,
        rule_store: Any = None,
        relation_store: Any = None,
        foreshadowing_store: Any = None,
        handover: Mapping[str, Any] | None = None,
    ) -> StoryStateSnapshot:
        """Read existing stores through query/list APIs; never consume or write state."""
        sources: list[dict[str, Any]] = []
        assertions: list[dict[str, Any]] = []

        def add(kind: str, source_id: str, value: Any, predicate: str, status: str) -> None:
            text = value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False, sort_keys=True, default=str
            )
            evidence_id = f"ev:{kind}:{source_id}"
            sources.append({
                "evidence_id": evidence_id, "source_id": source_id,
                "source_type": kind, "text": text,
                "section": self.section, "subsection": self.subsection,
                "span_start": 0, "span_end": len(text),
            })
            assertions.append({
                "assertion_id": f"state:{kind}:{source_id}", "subject": kind,
                "predicate": predicate, "value": text, "status": status,
                "evidence_ids": [evidence_id],
            })

        outline_id = str(current_outline.get("source_id", f"outline:S{self.section}.{self.subsection}"))
        for index, value in enumerate(current_outline.get("planned_events", []), 1):
            add("current_outline", f"{outline_id}:{index}", value, "planned_event", "planned")

        for item in self._read(world_state, "get_all_facts"):
            data = self._mapping(item)
            fact_id = str(data.get("fact_id") or _stable_hash(data)[:12])
            status = "confirmed" if data.get("verified") is True else "unknown"
            add("world_state", fact_id, data.get("fact", data), "world_fact", status)
        for index, item in enumerate(self._read(world_state, "get_contradictions"), 1):
            data = self._mapping(item)
            add("world_state", f"conflict:{index}", data, "world_fact", "conflicted")

        for item in self._read(event_graph, "get_arc_events", self.section, self.subsection):
            data = self._mapping(item)
            event_id = str(data.get("event_id") or _stable_hash(data)[:12])
            status = "confirmed" if data.get("status") == "established" else "planned"
            add("event_graph", event_id, data.get("description", data), "arc_milestone", status)

        for item in self._read(rule_store, "list_rules", True):
            data = self._mapping(item)
            if data.get("enabled", True) is False:
                continue
            rule_id = str(data.get("id") or _stable_hash(data)[:12])
            add("rule_store", rule_id, data.get("content", data.get("constraint", data)), "hard_constraint", "confirmed")

        for item in self._read(relation_store, "list_relations", self.task_id):
            data = self._mapping(item)
            relation_id = str(data.get("id") or _stable_hash(data)[:12])
            add("relation_store", relation_id, data, "relationship_stage", "confirmed")

        foreshadows = self._read(
            foreshadowing_store, "get_active_for_chapter", self.task_id, self.section
        )
        if not foreshadows:
            foreshadows = self._read(
                foreshadowing_store, "get_unresolved_foreshadowings", self.task_id, self.section
            )
        for item in foreshadows:
            data = self._mapping(item)
            source_id = str(data.get("id") or data.get("foreshadow_id") or _stable_hash(data)[:12])
            add("foreshadowing_store", source_id, data.get("description", data.get("content", data)), "open_loop", "planned")

        if handover:
            handover_id = str(handover.get("source_id", f"handover:S{max(self.section - 1, 0)}"))
            for index, value in enumerate(handover.get("confirmed_state", []), 1):
                add("handover", f"{handover_id}:state:{index}", value, "continuity_state", "confirmed")
            for index, value in enumerate(handover.get("open_loops", []), 1):
                add("handover", f"{handover_id}:open:{index}", value, "open_loop", "planned")

        return self.project(sources, assertions)

    @staticmethod
    def _read(source: Any, method: str, *args: Any) -> list[Any]:
        if source is None or not hasattr(source, method):
            return []
        value = getattr(source, method)(*args)
        if value is None:
            return []
        if isinstance(value, Mapping):
            return list(value.values())
        return list(value)

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "to_dict"):
            return dict(value.to_dict())
        if hasattr(value, "model_dump"):
            return dict(value.model_dump())
        return dict(vars(value))

    @staticmethod
    def _evidence(item: Mapping[str, Any]) -> SourceEvidence:
        text = str(item.get("text", ""))
        start = item.get("span_start")
        end = item.get("span_end")
        if (start is None) != (end is None):
            raise ValueError("evidence spans require both start and end")
        if start is not None and (start < 0 or end < start or end > len(text)):
            raise ValueError("evidence span is outside source text")
        evidence_id = str(item.get("evidence_id") or f"evidence:{item['source_id']}")
        return SourceEvidence(
            evidence_id=evidence_id,
            source_id=str(item["source_id"]),
            source_type=str(item["source_type"]),
            text_hash=str(item.get("text_hash") or _hash_text(text)),
            section=item.get("section"),
            subsection=item.get("subsection"),
            span_start=start,
            span_end=end,
            excerpt=text[start:end] if start is not None else text,
        )

    @staticmethod
    def _assertion(item: Mapping[str, Any], evidence_ids: set[str]) -> StateAssertion:
        linked = [str(value) for value in item.get("evidence_ids", [])]
        if not linked or not set(linked) <= evidence_ids:
            raise ValueError("every assertion must reference existing evidence")
        status = str(item.get("status", "unknown"))
        if status not in {"confirmed", "planned", "unknown", "conflicted"}:
            raise ValueError(f"unsupported epistemic status: {status}")
        return StateAssertion(
            assertion_id=str(item["assertion_id"]),
            subject=str(item.get("subject", "scene")),
            predicate=str(item["predicate"]),
            value=str(item["value"]),
            status=status,
            evidence_ids=linked,
        )
