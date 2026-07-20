"""Deterministic, shadow-only compiler from story state to a concise SceneSpec."""

from __future__ import annotations

import hashlib
import json
import re

from .contracts import SceneSpec, StateAssertion, StoryStateSnapshot


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _estimate_tokens(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    return max(1, int(chinese * 1.5 + (len(text) - chinese) * 0.3))


class SceneCompiler:
    """Preserves epistemic status; it never promotes unknown input to fact."""

    def compile(self, snapshot: StoryStateSnapshot) -> SceneSpec:
        confirmed = [
            item for item in snapshot.assertions
            if item.status == "confirmed" and item.predicate != "hard_constraint"
        ]
        planned = [
            item for item in snapshot.assertions
            if item.status == "planned" and item.predicate != "open_loop"
        ]
        unresolved = [item for item in snapshot.assertions if item.status in {"unknown", "conflicted"}]
        forbidden = self._forbidden_inferences(snapshot, unresolved)
        evidence_ids = {
            evidence_id
            for group in (confirmed, planned, snapshot.open_loops, snapshot.hard_constraints, unresolved, forbidden)
            for item in group
            for evidence_id in item.evidence_ids
        }
        evidence = [item for item in snapshot.evidence if item.evidence_id in evidence_ids]
        core = {
            "scene_id": f"{snapshot.task_id}:S{snapshot.section}.{snapshot.subsection}",
            "task_id": snapshot.task_id,
            "section": snapshot.section,
            "subsection": snapshot.subsection,
            "confirmed_state": [item.model_dump() for item in confirmed],
            "planned_events": [item.model_dump() for item in planned],
            "open_loops": [item.model_dump() for item in snapshot.open_loops],
            "hard_constraints": [item.model_dump() for item in snapshot.hard_constraints],
            "forbidden_inferences": [item.model_dump() for item in forbidden],
            "unknowns_and_conflicts": [item.model_dump() for item in unresolved],
            "evidence": [item.model_dump(exclude={"excerpt"}) for item in evidence],
            "source_hash": snapshot.source_hash,
        }
        rendered = self.render_fields(core)
        return SceneSpec(**core, spec_hash=_stable_hash(core), estimated_tokens=_estimate_tokens(rendered))

    @staticmethod
    def _forbidden_inferences(
        snapshot: StoryStateSnapshot,
        unresolved: list[StateAssertion],
    ) -> list[StateAssertion]:
        result: list[StateAssertion] = []
        predicates = {item.predicate for item in snapshot.assertions}
        unknown_by_predicate = {item.predicate: item for item in unresolved}
        rules = (
            ("character_absence", "location_operation", "人物未到场不能推出地点停业或其他人物不在"),
            ("current_time_anchor", "future_time_relation", "明确的当前时间锚点不得被改写成矛盾日期"),
            ("planned_event", "future_event_status", "计划中的后续事件不得写成已经发生"),
            ("open_loop", "open_loop_resolution", "未闭合事件不得在没有证据时自行解决"),
            ("unverified_character_fact", "unverified_character_fact", "未由状态源确认的人物经历、亲属或生死信息不得补写成事实"),
        )
        for trigger, target, message in rules:
            if trigger not in predicates:
                continue
            source = unknown_by_predicate.get(target) or next(
                item for item in snapshot.assertions if item.predicate == trigger
            )
            result.append(StateAssertion(
                assertion_id=f"forbid:{trigger}:{target}",
                subject="writer",
                predicate="forbidden_inference",
                value=message,
                status="unknown",
                evidence_ids=source.evidence_ids,
            ))
        return result

    @staticmethod
    def render_fields(core: dict) -> str:
        labels = (
            ("confirmed_state", "CONFIRMED"),
            ("planned_events", "PLANNED"),
            ("open_loops", "OPEN"),
            ("hard_constraints", "HARD"),
            ("forbidden_inferences", "FORBIDDEN"),
            ("unknowns_and_conflicts", "UNKNOWN/CONFLICT"),
        )
        lines = [f"SCENE {core['section']}.{core['subsection']}"]
        for key, label in labels:
            for item in core[key]:
                lines.append(f"[{label}] {item['value']} <{','.join(item['evidence_ids'])}>")
        return "\n".join(lines)

    def render(self, spec: SceneSpec) -> str:
        return self.render_fields(spec.model_dump())
