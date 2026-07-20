"""Deterministic SceneSpec construction and fail-safe Writer canary injection."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import PromptArtifact, SceneSpec
from .prompt_builder import estimate_prompt_tokens, messages_hash
from .scene_compiler import SceneCompiler
from .story_state_view import StoryStateView


logger = logging.getLogger("writing_system.scene_spec_canary")

SCENE_SPEC_HEADER = "\n\n## SceneSpec（结构化写作约束）\n"
SCENE_SPEC_TOKEN_CAP = 400
VALID_MODES = frozenset({"off", "shadow", "canary"})
SAFE_FALLBACK_REASONS = frozenset({
    "current_outline_missing",
    "next_outline_missing",
    "current_subsection_invalid",
    "source_id_missing",
    "current_target_missing",
    "next_outline_not_consecutive",
    "next_target_missing",
    "scene_spec_empty",
    "scene_spec_over_token_cap",
    "source_manifest_untraceable",
    "user_message_missing",
})


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SceneSpecBuildResult:
    spec: SceneSpec
    rendered: str
    source_manifest: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class SceneSpecApplication:
    prompt: PromptArtifact
    record: dict[str, Any] | None


class OutlineSceneSpecProvider:
    """Compile only the outline facts used by the final Phase 4R field trial."""

    def build(
        self,
        *,
        task_id: str,
        section: int,
        current_subsection: Mapping[str, Any],
        next_subsection: Mapping[str, Any] | None,
        is_last_subsection: bool,
    ) -> SceneSpecBuildResult:
        if not current_subsection:
            raise ValueError("current_outline_missing")
        if next_subsection is None and not is_last_subsection:
            raise ValueError("next_outline_missing")

        sub_number = int(current_subsection.get("subsection", 0))
        if sub_number < 1:
            raise ValueError("current_subsection_invalid")
        source_id = str(
            current_subsection.get("source_id", f"outline:S{section}.{sub_number}")
        ).strip()
        if not source_id:
            raise ValueError("source_id_missing")

        points = self._points(current_subsection)
        target = "；".join(points) or str(current_subsection.get("title", "")).strip()
        if not target:
            raise ValueError("current_target_missing")
        source_text = str(current_subsection.get("description", "")).strip() or target

        next_target = ""
        if next_subsection is not None:
            next_number = int(next_subsection.get("subsection", 0))
            if next_number != sub_number + 1:
                raise ValueError("next_outline_not_consecutive")
            next_points = self._points(next_subsection)
            next_target = "；".join(next_points) or str(next_subsection.get("title", "")).strip()
            if not next_target:
                raise ValueError("next_target_missing")
            source_text += "\nNEXT:" + next_target

        evidence_id = f"ev:{source_id}"
        sources = [{
            "evidence_id": evidence_id,
            "source_id": source_id,
            "source_type": "current_outline",
            "text": source_text,
            "section": section,
            "subsection": sub_number,
            "span_start": 0,
            "span_end": len(source_text),
        }]
        assertions = [{
            "assertion_id": f"planned:{source_id}",
            "subject": "current_scene",
            "predicate": "planned_event",
            "value": target,
            "status": "planned",
            "evidence_ids": [evidence_id],
        }]
        if next_target:
            assertions.append({
                "assertion_id": f"boundary:{source_id}",
                "subject": "writer",
                "predicate": "future_event_status",
                "value": f"本小节止于当前目标，不得提前完成下一小节事件：{next_target}",
                "status": "unknown",
                "evidence_ids": [evidence_id],
            })

        snapshot = StoryStateView(
            task_id=task_id,
            section=section,
            subsection=sub_number,
        ).project(sources, assertions)
        compiler = SceneCompiler()
        spec = compiler.compile(snapshot)
        rendered = compiler.render(spec)
        manifest = tuple(
            {"source_id": item.source_id, "text_hash": item.text_hash}
            for item in spec.evidence
        )
        return SceneSpecBuildResult(spec=spec, rendered=rendered, source_manifest=manifest)

    @staticmethod
    def _points(subsection: Mapping[str, Any]) -> list[str]:
        return [
            str(value).strip()
            for value in subsection.get("key_points", [])
            if str(value).strip()
        ]


class SceneSpecCanaryController:
    """Apply SceneSpec without allowing compilation failures to affect Writer."""

    def __init__(
        self,
        *,
        mode: str = "off",
        canary_task_ids: str | set[str] = "",
        provider: OutlineSceneSpecProvider | None = None,
        token_cap: int = SCENE_SPEC_TOKEN_CAP,
    ) -> None:
        requested_mode = str(mode or "off").strip().lower()
        self.mode = requested_mode if requested_mode in VALID_MODES else "off"
        if isinstance(canary_task_ids, str):
            values = canary_task_ids.split(",")
        else:
            values = canary_task_ids
        self.canary_task_ids = frozenset(str(value).strip() for value in values if str(value).strip())
        self.provider = provider or OutlineSceneSpecProvider()
        self.token_cap = token_cap

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def apply(
        self,
        prompt: PromptArtifact,
        *,
        task_id: str,
        section: int,
        current_subsection: Mapping[str, Any],
        next_subsection: Mapping[str, Any] | None,
        is_last_subsection: bool,
    ) -> SceneSpecApplication:
        if self.mode == "off":
            return SceneSpecApplication(prompt=prompt, record=None)

        started = time.perf_counter()
        if self.mode == "canary" and task_id not in self.canary_task_ids:
            return self._fallback(
                prompt, task_id, section, current_subsection,
                "task_not_allowlisted", started,
            )

        try:
            built = self.provider.build(
                task_id=task_id,
                section=section,
                current_subsection=current_subsection,
                next_subsection=next_subsection,
                is_last_subsection=is_last_subsection,
            )
            self._validate(built)
            injected = self.mode == "canary"
            applied_prompt = self._inject(prompt, built) if injected else prompt
        except Exception as exc:
            value_error = str(exc) if isinstance(exc, ValueError) else ""
            reason = value_error if value_error in SAFE_FALLBACK_REASONS else type(exc).__name__
            return self._fallback(prompt, task_id, section, current_subsection, reason, started)

        record = self._record(
            task_id=task_id,
            section=section,
            subsection=built.spec.subsection,
            injected=injected,
            spec_hash=built.spec.spec_hash,
            estimated_tokens=built.spec.estimated_tokens,
            source_ids=[item["source_id"] for item in built.source_manifest],
            fallback_reason=None,
            started=started,
        )
        self._log(record)
        return SceneSpecApplication(prompt=applied_prompt, record=record)

    def _validate(self, built: SceneSpecBuildResult) -> None:
        if not built.rendered.strip() or not built.spec.planned_events:
            raise ValueError("scene_spec_empty")
        if built.spec.estimated_tokens > self.token_cap:
            raise ValueError("scene_spec_over_token_cap")
        if not built.source_manifest or any(
            not item.get("source_id") or not item.get("text_hash")
            for item in built.source_manifest
        ):
            raise ValueError("source_manifest_untraceable")

    @staticmethod
    def _inject(prompt: PromptArtifact, built: SceneSpecBuildResult) -> PromptArtifact:
        messages = copy.deepcopy(prompt.messages)
        user_indices = [index for index, item in enumerate(messages) if item.get("role") == "user"]
        if not user_indices:
            raise ValueError("user_message_missing")
        messages[user_indices[-1]]["content"] += SCENE_SPEC_HEADER + built.rendered
        token_by_source = dict(prompt.token_by_source)
        token_by_source["scene_spec"] = built.spec.estimated_tokens
        source_manifest = list(prompt.source_manifest)
        source_manifest.extend(dict(item) for item in built.source_manifest)
        content_payload = "\n".join(item["content"] for item in messages)
        return PromptArtifact(
            messages=messages,
            messages_hash=messages_hash(messages),
            content_hash=_sha256_text(content_payload),
            estimated_tokens=sum(estimate_prompt_tokens(item["content"]) for item in messages),
            token_by_source=token_by_source,
            source_manifest=source_manifest,
            prompt_version=prompt.prompt_version,
        )

    def _fallback(
        self,
        prompt: PromptArtifact,
        task_id: str,
        section: int,
        current_subsection: Mapping[str, Any],
        reason: str,
        started: float,
    ) -> SceneSpecApplication:
        try:
            subsection = int(current_subsection.get("subsection", 0))
        except Exception:
            subsection = 0
        record = self._record(
            task_id=task_id,
            section=section,
            subsection=subsection,
            injected=False,
            spec_hash=None,
            estimated_tokens=0,
            source_ids=[],
            fallback_reason=reason,
            started=started,
        )
        self._log(record)
        return SceneSpecApplication(prompt=prompt, record=record)

    def _record(
        self,
        *,
        task_id: str,
        section: int,
        subsection: int,
        injected: bool,
        spec_hash: str | None,
        estimated_tokens: int,
        source_ids: list[str],
        fallback_reason: str | None,
        started: float,
    ) -> dict[str, Any]:
        return {
            "task_id_hash": _sha256_text(task_id),
            "section": section,
            "subsection": subsection,
            "mode": self.mode,
            "injected": injected,
            "scene_spec_hash": spec_hash,
            "estimated_tokens": estimated_tokens,
            "source_ids": source_ids,
            "fallback_reason": fallback_reason,
            "compile_elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "production_effect": injected,
        }

    @staticmethod
    def _log(record: dict[str, Any]) -> None:
        logger.info(
            "scene_spec_canary=%s",
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
