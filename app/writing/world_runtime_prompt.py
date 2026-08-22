"""WR1 compact World Runtime projection and allowlisted prompt application.

This boundary deliberately sits between the executable runtime frame and the
Writer prompt.  It consumes only the scene-relevant frame plus the resolved
definitions needed to explain activated rules; it never exposes provenance,
excluded candidates, conflict/debug payloads, or the complete world database.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from app.utils.llm_client import estimate_tokens

from .world_runtime_compiler import SceneRuntimeFrame
from .world_runtime_contracts import (
    FrozenRuntimeModel,
    StatePredicate,
    canonical_hash,
)
from .world_runtime_resolver import ResolvedWorldConstitution


WORLD_RUNTIME_PROMPT_VERSION = "world-runtime-prompt-wr1-v1"
WORLD_RUNTIME_PROMPT_HEADER = "\n\n## 本小节世界运行边界（只约束事实与因果）\n"
VALID_RUNTIME_PROMPT_MODES = frozenset({"off", "shadow", "canary"})
NON_WRITER_RULE_PREFIXES = ("meta.commit.", "meta.delta.")

RuntimePromptMode = Literal["off", "shadow", "canary"]


class PromptRuntimeFact(FrozenRuntimeModel):
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: Any = None
    epistemic_status: str = Field(min_length=1)


class PromptRuntimeRule(FrozenRuntimeModel):
    semantic_key: str = Field(min_length=1)
    enforcement: Literal["block", "warn"]
    prerequisites: tuple[StatePredicate, ...] = ()


class PromptRuntimeTransition(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    from_state: str = Field(min_length=1)
    to_state: str = Field(min_length=1)
    required_by_event: bool
    preceding_transition_count: int = Field(ge=0)


class PromptRuntimeEvent(FrozenRuntimeModel):
    event_id: str = Field(min_length=1)
    required: bool
    status: str = Field(min_length=1)


class PromptRuntimeUnknown(FrozenRuntimeModel):
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)


class RuntimePromptProjection(FrozenRuntimeModel):
    projection_id: str = Field(min_length=1)
    frame_id: str = Field(min_length=1)
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    state_revision: int = Field(ge=0)
    frame_status: str = Field(min_length=1)
    facts: tuple[PromptRuntimeFact, ...] = ()
    rules: tuple[PromptRuntimeRule, ...] = ()
    transitions: tuple[PromptRuntimeTransition, ...] = ()
    must_events: tuple[PromptRuntimeEvent, ...] = ()
    unknowns: tuple[PromptRuntimeUnknown, ...] = ()
    estimated_tokens: int = Field(ge=0)
    schema_version: str = WORLD_RUNTIME_PROMPT_VERSION

    @model_validator(mode="after")
    def reject_duplicate_prompt_items(self):
        identities = (
            [(item.subject, item.predicate) for item in self.facts],
            [item.semantic_key for item in self.rules],
            [
                (item.event_id, item.from_state, item.to_state)
                for item in self.transitions
            ],
            [item.event_id for item in self.must_events],
            [(item.subject, item.predicate) for item in self.unknowns],
        )
        if any(len(values) != len(set(values)) for values in identities):
            raise ValueError("runtime prompt projection contains duplicates")
        return self

    @property
    def projection_hash(self) -> str:
        return canonical_hash(self)


class RuntimePromptObservation(FrozenRuntimeModel):
    task_id_hash: str = Field(min_length=1)
    mode: RuntimePromptMode
    frame_id: str
    projection_hash: str | None = None
    compiled: bool
    injected: bool
    estimated_tokens: int = Field(ge=0)
    fallback_code: str | None = None
    schema_version: str = WORLD_RUNTIME_PROMPT_VERSION


class RuntimePromptApplication(FrozenRuntimeModel):
    messages: tuple[dict[str, str], ...]
    observation: RuntimePromptObservation
    projection: RuntimePromptProjection | None = None
    rendered: str = ""
    schema_version: str = WORLD_RUNTIME_PROMPT_VERSION


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "unknown"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _operator(value: str) -> str:
    return {
        "equals": "=",
        "not_equals": "!=",
        "in": "in",
        "not_in": "not-in",
        "exists": "exists",
        "not_exists": "not-exists",
        "greater_than": ">",
        "greater_or_equal": ">=",
        "less_than": "<",
        "less_or_equal": "<=",
    }.get(value, value)


def _predicate(item: StatePredicate) -> str:
    subject = "" if item.subject.startswith("$") else f"{item.subject}."
    return f"{subject}{item.predicate}{_operator(item.operator)}{_value(item.expected)}"


def _rule_text(item: PromptRuntimeRule) -> str:
    concise = {
        "meta.knowledge.acquisition_path": "信息变化须有传播或感知路径",
        "meta.state_change.causality": "状态变化须有角色动作或已声明机制",
        "meta.transition.prerequisites": "状态跳转须满足前置，不得跳步",
        "bakery.storefront.schedule": "店面对外开放须符合项目营业表",
        "employment.resignation.private_draft_delivery": "私人草稿不等于送达；发进机构通道才到 delivered",
        "employment.termination.prerequisite": "送达不等于离职生效",
        "publication.public_reaction.reach": "评论或反应者须先收到可见内容",
        "publication.public_visibility.prerequisite": "未发布内容不可公开可见",
        "storefront.internal_activity.public_opening": "内部作业不等于对外营业",
        "storefront.public_opening.schedule": "对外营业须到营业时段或有明确例外",
        "event.completed.no_repeat": "已完成事件不得无重置再次发生",
    }
    if item.semantic_key in concise:
        return concise[item.semantic_key]
    requirement = "&".join(_predicate(value) for value in item.prerequisites)
    return f"{item.semantic_key}←{requirement}" if requirement else item.semantic_key


def render_runtime_prompt_projection(projection: RuntimePromptProjection) -> str:
    """Render a compact execution boundary, not a debug dump."""

    grouped: dict[str, list[str]] = {}
    for item in projection.facts:
        grouped.setdefault(item.subject, []).append(
            f"{item.predicate}={_value(item.value)}"
        )
    lines = [f"当前状态 r{projection.state_revision}："]
    lines.extend(
        f"- {subject}: {','.join(values)}"
        for subject, values in grouped.items()
    )
    if projection.rules:
        lines.append("因果边界（硬=不可无解释违反，默认=可用明确事件覆盖）：")
        for item in projection.rules:
            level = "硬" if item.enforcement == "block" else "默认"
            lines.append(f"- {level}: {_rule_text(item)}")
    if projection.transitions:
        paths: dict[str, list[PromptRuntimeTransition]] = {}
        for item in projection.transitions:
            paths.setdefault(item.event_id, []).append(item)
        lines.append("合法路径（顺序落实）：")
        for event_id, items in paths.items():
            states = [items[0].from_state, *(item.to_state for item in items)]
            lines.append(f"- {event_id}: {'→'.join(states)}")
    if projection.must_events:
        lines.append(
            "必写事件（不得为规避一致性而删除）："
            + ",".join(
                item.event_id for item in projection.must_events if item.required
            )
        )
    if projection.unknowns:
        lines.append(
            "未知（不得自行补真）："
            + ",".join(
                f"{item.subject}.{item.predicate}" for item in projection.unknowns
            )
        )
    lines.append("只把这些边界落实进情节，不要在正文中复述规则、字段名或状态清单。")
    return "\n".join(lines)


def compile_runtime_prompt_projection(
    *,
    frame: SceneRuntimeFrame,
    resolved: ResolvedWorldConstitution,
) -> RuntimePromptProjection:
    """Compile the only World Runtime payload that a Writer may consume."""

    if frame.project_id != resolved.project_id:
        raise ValueError("runtime_prompt_project_mismatch")
    if frame.status == "blocked":
        raise ValueError("runtime_frame_blocked")
    active_by_id = {item.rule_id: item for item in resolved.active_rules}
    prompt_rules: list[PromptRuntimeRule] = []
    for activated in frame.activated_rules:
        if activated.enforcement not in {"block", "warn"}:
            continue
        # Revision and idempotency rules are consumed by State Commit, not by
        # prose generation.  Keeping them out is a data-ownership boundary,
        # not a heuristic token trim.
        if activated.semantic_key.startswith(NON_WRITER_RULE_PREFIXES):
            continue
        source = active_by_id.get(activated.rule_id)
        if source is None:
            raise ValueError("runtime_rule_definition_missing")
        prompt_rules.append(
            PromptRuntimeRule(
                semantic_key=activated.semantic_key,
                enforcement=activated.enforcement,
                prerequisites=source.prerequisites,
            )
        )
    payload = {
        "frame_id": frame.frame_id,
        "section": frame.section,
        "subsection": frame.subsection,
        "state_revision": frame.state_revision,
        "frame_status": frame.status,
        "facts": tuple(
            PromptRuntimeFact(
                subject=item.subject,
                predicate=item.predicate,
                value=item.value,
                epistemic_status=item.epistemic_status,
            )
            for item in frame.facts
        ),
        "rules": tuple(sorted(prompt_rules, key=lambda item: item.semantic_key)),
        "transitions": tuple(
            PromptRuntimeTransition(
                event_id=item.event_id,
                from_state=item.from_state,
                to_state=item.to_state,
                required_by_event=item.required_by_event,
                preceding_transition_count=len(item.preceding_transition_ids),
            )
            for item in frame.transition_options
        ),
        "must_events": tuple(
            PromptRuntimeEvent(
                event_id=item.event_id,
                required=item.required,
                status=item.status,
            )
            for item in frame.event_boundaries
        ),
        "unknowns": tuple(
            PromptRuntimeUnknown(subject=item.subject, predicate=item.predicate)
            for item in frame.unknowns
        ),
    }
    identity = canonical_hash(payload)
    provisional = RuntimePromptProjection(
        projection_id=f"runtime-prompt:{identity[:24]}",
        estimated_tokens=0,
        **payload,
    )
    estimated = estimate_tokens(render_runtime_prompt_projection(provisional))
    return provisional.model_copy(update={"estimated_tokens": estimated})


class WorldRuntimePromptController:
    """Compile in shadow; inject only for exact canary task allowlists."""

    def __init__(
        self,
        *,
        mode: str = "off",
        canary_task_ids: str | set[str] = "",
        token_cap: int = 1100,
    ) -> None:
        requested = str(mode or "off").strip().lower()
        self.mode: RuntimePromptMode = (
            requested if requested in VALID_RUNTIME_PROMPT_MODES else "off"
        )
        values = canary_task_ids.split(",") if isinstance(canary_task_ids, str) else canary_task_ids
        self.canary_task_ids = frozenset(
            str(item).strip() for item in values if str(item).strip()
        )
        self.token_cap = token_cap

    def apply(
        self,
        messages: list[dict[str, str]],
        *,
        task_id: str,
        frame: SceneRuntimeFrame,
        resolved: ResolvedWorldConstitution,
    ) -> RuntimePromptApplication:
        task_hash = canonical_hash(task_id)
        if self.mode == "off":
            return self._fallback(messages, task_hash, frame, "mode_off")
        try:
            projection = compile_runtime_prompt_projection(frame=frame, resolved=resolved)
            rendered = render_runtime_prompt_projection(projection)
            if projection.estimated_tokens > self.token_cap:
                raise ValueError("runtime_prompt_over_token_cap")
            inject = self.mode == "canary" and task_id in self.canary_task_ids
            if self.mode == "canary" and not inject:
                fallback = "task_not_allowlisted"
            else:
                fallback = None
            applied = self._inject(messages, rendered) if inject else copy.deepcopy(messages)
            observation = RuntimePromptObservation(
                task_id_hash=task_hash,
                mode=self.mode,
                frame_id=frame.frame_id,
                projection_hash=projection.projection_hash,
                compiled=True,
                injected=inject,
                estimated_tokens=projection.estimated_tokens,
                fallback_code=fallback,
            )
            return RuntimePromptApplication(
                messages=tuple(applied),
                observation=observation,
                projection=projection,
                rendered=rendered,
            )
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return self._fallback(messages, task_hash, frame, code)

    def _fallback(self, messages, task_hash, frame, code):
        return RuntimePromptApplication(
            messages=tuple(copy.deepcopy(messages)),
            observation=RuntimePromptObservation(
                task_id_hash=task_hash,
                mode=self.mode,
                frame_id=frame.frame_id,
                compiled=False,
                injected=False,
                estimated_tokens=0,
                fallback_code=code,
            ),
        )

    @staticmethod
    def _inject(messages: list[dict[str, str]], rendered: str) -> list[dict[str, str]]:
        applied = copy.deepcopy(messages)
        user_indices = [
            index for index, item in enumerate(applied)
            if item.get("role") == "user"
        ]
        if not user_indices:
            raise ValueError("user_message_missing")
        index = user_indices[-1]
        applied[index]["content"] += WORLD_RUNTIME_PROMPT_HEADER + rendered
        return applied
