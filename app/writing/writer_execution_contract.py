"""Deterministic first-draft execution contract and fail-safe prompt injection."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..utils.word_counter import count_chinese_chars
from .contracts import PromptArtifact, SceneSpec
from .prompt_builder import estimate_prompt_tokens, messages_hash
from .scene_spec_provider import OutlineSceneSpecProvider, SceneSpecBuildResult


logger = logging.getLogger("writing_system.writer_execution_contract")

EXECUTION_CONTRACT_HEADER = "\n\n[本小节执行契约]\n"
EXECUTION_CONTRACT_FOOTER = "\n[/本小节执行契约]"
EXECUTION_CONTRACT_TOKEN_CAP = 450
VALID_MODES = frozenset({"off", "shadow", "canary"})
SCHEMA_VERSION = "writer-execution-contract-v1"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


class ExecutionSourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    text_hash: str


class WriterExecutionContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    task_id_hash: str
    section: int = Field(ge=1)
    subsection: int = Field(ge=1)
    objective: str
    ordered_required_events: tuple[str, ...]
    confirmed_continuity: tuple[str, ...] = ()
    prohibited_inventions: tuple[str, ...] = ()
    stop_boundary: str
    target_characters: int = Field(gt=0)
    soft_min_characters: int = Field(gt=0)
    soft_max_characters: int = Field(gt=0)
    scene_spec_hash: str
    source_manifest: tuple[ExecutionSourceRef, ...]
    overplanned_contract: bool
    estimated_tokens: int = Field(ge=0)
    contract_hash: str


@dataclass(frozen=True)
class WriterExecutionContractBuildResult:
    contract: WriterExecutionContract
    rendered: str
    component_token_breakdown: dict[str, Any]


@dataclass(frozen=True)
class WriterExecutionContractApplication:
    prompt: PromptArtifact
    record: dict[str, Any] | None
    contract: WriterExecutionContract | None


class WriterExecutionContractProvider:
    """Compile an execution view from existing outline and SceneSpec inputs."""

    def __init__(
        self, scene_spec_provider: OutlineSceneSpecProvider | None = None
    ) -> None:
        self.scene_spec_provider = scene_spec_provider or OutlineSceneSpecProvider()

    def build(
        self,
        *,
        task_id: str,
        section: int,
        current_subsection: Mapping[str, Any],
        next_subsection: Mapping[str, Any] | None,
        is_last_subsection: bool,
        required_events: Sequence[Mapping[str, str]],
        target_characters: int,
    ) -> WriterExecutionContractBuildResult:
        objective = str(current_subsection.get("description", "")).strip()
        if not objective:
            raise ValueError("current_target_missing")
        try:
            normalized_target = int(target_characters)
        except (TypeError, ValueError):
            raise ValueError("target_characters_invalid") from None
        if isinstance(target_characters, bool) or normalized_target <= 0:
            raise ValueError("target_characters_invalid")
        target_characters = normalized_target

        scene = self.scene_spec_provider.build(
            task_id=task_id,
            section=section,
            current_subsection=current_subsection,
            next_subsection=next_subsection,
            is_last_subsection=is_last_subsection,
        )
        events, event_sources = self._normalize_events(required_events)
        if not events:
            raise ValueError("execution_contract_empty")

        stop_boundary = self._stop_boundary(
            scene.spec, objective=objective, is_last_subsection=is_last_subsection
        )
        confirmed = tuple(
            item.value for item in scene.spec.confirmed_state if item.value.strip()
        )
        prohibited = tuple(
            item.value
            for item in (
                *scene.spec.forbidden_inferences,
                *scene.spec.unknowns_and_conflicts,
            )
            if item.predicate != "future_event_status" and item.value.strip()
        )
        source_manifest = self._merge_sources(
            scene=scene, event_sources=event_sources
        )

        semantic_payload = {
            "schema_version": SCHEMA_VERSION,
            "task_id_hash": _sha256_text(task_id),
            "section": int(section),
            "subsection": int(current_subsection.get("subsection", 0)),
            "objective": objective,
            "ordered_required_events": events,
            "confirmed_continuity": confirmed,
            "prohibited_inventions": prohibited,
            "stop_boundary": stop_boundary,
            "target_characters": target_characters,
            "soft_min_characters": max(1, round(target_characters * 0.85)),
            "soft_max_characters": max(1, round(target_characters * 1.30)),
            "scene_spec_hash": scene.spec.spec_hash,
            "source_manifest": [item.model_dump() for item in source_manifest],
            "overplanned_contract": len(events) > 5,
        }
        contract_hash = _sha256_text(_canonical_json(semantic_payload))
        preliminary = WriterExecutionContract(
            **semantic_payload, estimated_tokens=0, contract_hash=contract_hash
        )
        rendered, component_token_breakdown = self.render_with_breakdown(preliminary)
        contract = preliminary.model_copy(
            update={"estimated_tokens": estimate_prompt_tokens(rendered)}
        )
        component_token_breakdown["total"] = contract.estimated_tokens
        return WriterExecutionContractBuildResult(
            contract=contract,
            rendered=rendered,
            component_token_breakdown=component_token_breakdown,
        )

    @staticmethod
    def render(contract: WriterExecutionContract) -> str:
        rendered, _ = WriterExecutionContractProvider.render_with_breakdown(contract)
        return rendered

    @staticmethod
    def render_with_breakdown(
        contract: WriterExecutionContract,
    ) -> tuple[str, dict[str, Any]]:
        events = WriterExecutionContractProvider._unique_render_values(
            contract.ordered_required_events
        )
        objective_and_events = (contract.objective, *contract.ordered_required_events)
        confirmed = WriterExecutionContractProvider._unique_render_values(
            contract.confirmed_continuity,
            excluded=objective_and_events,
        )
        prohibited = WriterExecutionContractProvider._unique_render_values(
            contract.prohibited_inventions,
            excluded=objective_and_events,
        )

        lines = [f"目标：{contract.objective}", "按序完成："]
        lines.extend(f"{index}. {value}" for index, value in events)
        if confirmed:
            lines.append("连续状态：")
            lines.extend(f"- {value}" for _, value in confirmed)
        if prohibited:
            lines.append("禁止补造：")
            lines.extend(f"- {value}" for _, value in prohibited)

        length_instruction = (
            "以下范围指本小节全文总长度，不是单段长度。"
            f"目标约{contract.target_characters}字，建议保持在"
            f"{contract.soft_min_characters}～{contract.soft_max_characters}字；"
            "优先压缩描写完成既定事件，不增加合同外人物、场景或后续事件。"
        )
        stop_instruction = "完成上述事件后立即结束本小节。"
        lines.extend([
            f"篇幅：{length_instruction}",
            "停止边界：",
            f"- {contract.stop_boundary}",
            f"- {stop_instruction}",
        ])
        rendered = "\n".join(lines)

        required_event_tokens = [
            {
                "index": index,
                "tokens": estimate_prompt_tokens(value),
                "rendered": any(item_index == index for item_index, _ in events),
            }
            for index, value in enumerate(contract.ordered_required_events, 1)
        ]
        confirmed_tokens = [
            {
                "index": index,
                "tokens": estimate_prompt_tokens(value),
                "rendered": any(item_index == index for item_index, _ in confirmed),
            }
            for index, value in enumerate(contract.confirmed_continuity, 1)
        ]
        prohibited_tokens = [
            {
                "index": index,
                "tokens": estimate_prompt_tokens(value),
                "rendered": any(item_index == index for item_index, _ in prohibited),
            }
            for index, value in enumerate(contract.prohibited_inventions, 1)
        ]
        component_values = {
            "objective": estimate_prompt_tokens(contract.objective),
            "required_events": required_event_tokens,
            "confirmed_continuity": confirmed_tokens,
            "prohibited_inventions": prohibited_tokens,
            "length_instruction": estimate_prompt_tokens(length_instruction),
            "stop_boundary": estimate_prompt_tokens(
                contract.stop_boundary + "\n" + stop_instruction
            ),
        }
        rendered_content_tokens = (
            component_values["objective"]
            + sum(item["tokens"] for item in required_event_tokens if item["rendered"])
            + sum(item["tokens"] for item in confirmed_tokens if item["rendered"])
            + sum(item["tokens"] for item in prohibited_tokens if item["rendered"])
            + component_values["length_instruction"]
            + component_values["stop_boundary"]
        )
        component_values["wrapper_and_labels"] = max(
            0, estimate_prompt_tokens(rendered) - rendered_content_tokens
        )
        return rendered, component_values

    @staticmethod
    def _unique_render_values(
        values: Sequence[str], *, excluded: Sequence[str] = ()
    ) -> tuple[tuple[int, str], ...]:
        seen = {
            WriterExecutionContractProvider._normalized_exact(value)
            for value in excluded
            if WriterExecutionContractProvider._normalized_exact(value)
        }
        rendered: list[tuple[int, str]] = []
        for index, raw in enumerate(values, 1):
            value = str(raw).strip()
            normalized = WriterExecutionContractProvider._normalized_exact(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rendered.append((index, value))
        return tuple(rendered)

    @staticmethod
    def _normalized_exact(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", str(value)).split())

    @staticmethod
    def contract_hash(contract: WriterExecutionContract) -> str:
        payload = contract.model_dump(
            exclude={"estimated_tokens", "contract_hash"}, mode="json"
        )
        return _sha256_text(_canonical_json(payload))

    @staticmethod
    def _normalize_events(
        required_events: Sequence[Mapping[str, str]],
    ) -> tuple[tuple[str, ...], tuple[ExecutionSourceRef, ...]]:
        events: list[str] = []
        sources: list[ExecutionSourceRef] = []
        seen: set[tuple[str, str]] = set()
        for item in required_events:
            text = str(item.get("text", "")).strip()
            source_id = str(item.get("source_id", "")).strip()
            text_hash = str(item.get("text_hash", "")).strip()
            if not text or not source_id or not text_hash:
                raise ValueError("source_manifest_untraceable")
            key = (source_id, text_hash)
            if key in seen:
                continue
            seen.add(key)
            events.append(text)
            sources.append(ExecutionSourceRef(source_id=source_id, text_hash=text_hash))
        return tuple(events), tuple(sources)

    @staticmethod
    def _stop_boundary(
        spec: SceneSpec, *, objective: str, is_last_subsection: bool
    ) -> str:
        for item in spec.unknowns_and_conflicts:
            if item.predicate == "future_event_status" and item.value.strip():
                return item.value.strip()
        if is_last_subsection:
            return f"完成当前目标后停止，不自行延伸新的后续事件：{objective}"
        raise ValueError("next_outline_missing")

    @staticmethod
    def _merge_sources(
        *,
        scene: SceneSpecBuildResult,
        event_sources: Sequence[ExecutionSourceRef],
    ) -> tuple[ExecutionSourceRef, ...]:
        merged: list[ExecutionSourceRef] = []
        seen: set[tuple[str, str]] = set()
        values = [
            ExecutionSourceRef(
                source_id=str(item.get("source_id", "")),
                text_hash=str(item.get("text_hash", "")),
            )
            for item in scene.source_manifest
        ] + list(event_sources)
        for item in values:
            if not item.source_id or not item.text_hash:
                raise ValueError("source_manifest_untraceable")
            key = (item.source_id, item.text_hash)
            if key not in seen:
                seen.add(key)
                merged.append(item)
        if not merged:
            raise ValueError("source_manifest_untraceable")
        return tuple(merged)


class WriterExecutionContractController:
    """Compile, optionally inject, and observe without affecting generation flow."""

    def __init__(
        self,
        *,
        mode: str = "off",
        provider: WriterExecutionContractProvider | None = None,
        token_cap: int = EXECUTION_CONTRACT_TOKEN_CAP,
    ) -> None:
        requested = str(mode or "off").strip().lower()
        self.mode = requested if requested in VALID_MODES else "off"
        self.provider = provider or WriterExecutionContractProvider()
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
        required_events: Sequence[Mapping[str, str]],
        target_characters: int,
    ) -> WriterExecutionContractApplication:
        if self.mode == "off":
            return WriterExecutionContractApplication(prompt, None, None)

        started = time.perf_counter()
        built: WriterExecutionContractBuildResult | None = None
        try:
            built = self.provider.build(
                task_id=task_id,
                section=section,
                current_subsection=current_subsection,
                next_subsection=next_subsection,
                is_last_subsection=is_last_subsection,
                required_events=required_events,
                target_characters=target_characters,
            )
            self._validate(built)
            injected = self.mode == "canary"
            applied = self._inject(prompt, built) if injected else prompt
        except Exception as exc:
            return self._fallback(
                prompt=prompt,
                task_id=task_id,
                section=section,
                current_subsection=current_subsection,
                error=exc,
                started=started,
                built=built,
            )

        record = self._record(
            task_id=task_id,
            section=section,
            subsection=built.contract.subsection,
            compiled=True,
            injected=injected,
            contract=built.contract,
            component_token_breakdown=built.component_token_breakdown,
            fallback_reason=None,
            started=started,
        )
        self._log(record)
        return WriterExecutionContractApplication(applied, record, built.contract)

    def observe_output(
        self,
        application: WriterExecutionContractApplication | None,
        *,
        output: str,
        mandatory_observation: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if application is None or application.record is None:
            return None
        contract = application.contract
        target = contract.target_characters if contract else None
        output_characters = count_chinese_chars(output)
        record = {
            "task_id_hash": application.record["task_id_hash"],
            "section": application.record["section"],
            "subsection": application.record["subsection"],
            "mode": self.mode,
            "contract_hash": contract.contract_hash if contract else None,
            "output_sha256": _sha256_text(output),
            "output_characters": output_characters,
            "output_to_target_ratio": (
                round(output_characters / target, 4) if target else None
            ),
            "below_soft_min": (
                output_characters < contract.soft_min_characters if contract else None
            ),
            "above_soft_max": (
                output_characters > contract.soft_max_characters if contract else None
            ),
            "mandatory_would_have_retried": (
                mandatory_observation.get("would_have_retried")
                if mandatory_observation
                else None
            ),
            "actual_retry_count": (
                mandatory_observation.get("actual_retry_count")
                if mandatory_observation
                else None
            ),
            "production_effect": bool(application.record["injected"]),
        }
        logger.info(
            "writer_execution_contract_output=%s",
            _canonical_json(record),
        )
        return record

    def _validate(self, built: WriterExecutionContractBuildResult) -> None:
        contract = built.contract
        if not built.rendered.strip() or not contract.ordered_required_events:
            raise ValueError("execution_contract_empty")
        if contract.estimated_tokens > self.token_cap:
            raise ValueError("contract_over_budget")
        if not contract.source_manifest:
            raise ValueError("source_manifest_untraceable")
        if self.provider.contract_hash(contract) != contract.contract_hash:
            raise ValueError("contract_hash_unstable")

    @staticmethod
    def _inject(
        prompt: PromptArtifact, built: WriterExecutionContractBuildResult
    ) -> PromptArtifact:
        messages = copy.deepcopy(prompt.messages)
        user_indices = [
            index for index, item in enumerate(messages) if item.get("role") == "user"
        ]
        if not user_indices:
            raise ValueError("user_message_missing")
        messages[user_indices[-1]]["content"] += (
            EXECUTION_CONTRACT_HEADER + built.rendered + EXECUTION_CONTRACT_FOOTER
        )
        token_by_source = dict(prompt.token_by_source)
        token_by_source["writer_execution_contract"] = built.contract.estimated_tokens
        source_manifest = list(prompt.source_manifest)
        source_manifest.extend(
            item.model_dump(mode="json") for item in built.contract.source_manifest
        )
        content_payload = "\n".join(item["content"] for item in messages)
        return PromptArtifact(
            messages=messages,
            messages_hash=messages_hash(messages),
            content_hash=_sha256_text(content_payload),
            estimated_tokens=sum(
                estimate_prompt_tokens(item["content"]) for item in messages
            ),
            token_by_source=token_by_source,
            source_manifest=source_manifest,
            prompt_version=prompt.prompt_version,
        )

    def _fallback(
        self,
        *,
        prompt: PromptArtifact,
        task_id: str,
        section: int,
        current_subsection: Mapping[str, Any],
        error: Exception,
        started: float,
        built: WriterExecutionContractBuildResult | None,
    ) -> WriterExecutionContractApplication:
        try:
            subsection = int(current_subsection.get("subsection", 0))
        except Exception:
            subsection = 0
        safe_reasons = {
            "current_outline_missing",
            "next_outline_missing",
            "current_subsection_invalid",
            "source_id_missing",
            "current_target_missing",
            "next_outline_not_consecutive",
            "next_target_missing",
            "target_characters_invalid",
            "execution_contract_empty",
            "source_manifest_untraceable",
            "contract_over_budget",
            "contract_hash_unstable",
            "user_message_missing",
        }
        value = str(error) if isinstance(error, ValueError) else ""
        reason = value if value in safe_reasons else type(error).__name__
        record = self._record(
            task_id=task_id,
            section=section,
            subsection=subsection,
            compiled=False,
            injected=False,
            contract=built.contract if built else None,
            component_token_breakdown=(
                built.component_token_breakdown if built else None
            ),
            fallback_reason=reason,
            started=started,
        )
        self._log(record)
        return WriterExecutionContractApplication(prompt, record, None)

    def _record(
        self,
        *,
        task_id: str,
        section: int,
        subsection: int,
        compiled: bool,
        injected: bool,
        contract: WriterExecutionContract | None,
        component_token_breakdown: dict[str, Any] | None,
        fallback_reason: str | None,
        started: float,
    ) -> dict[str, Any]:
        return {
            "task_id_hash": _sha256_text(task_id),
            "section": section,
            "subsection": subsection,
            "mode": self.mode,
            "compiled": compiled,
            "injected": injected,
            "contract_hash": contract.contract_hash if contract else None,
            "scene_spec_hash": contract.scene_spec_hash if contract else None,
            "required_event_count": (
                len(contract.ordered_required_events) if contract else 0
            ),
            "overplanned_contract": (
                contract.overplanned_contract if contract else False
            ),
            "target_characters": contract.target_characters if contract else None,
            "soft_min_characters": (
                contract.soft_min_characters if contract else None
            ),
            "soft_max_characters": (
                contract.soft_max_characters if contract else None
            ),
            "estimated_tokens": contract.estimated_tokens if contract else 0,
            "attempted_estimated_tokens": (
                contract.estimated_tokens if contract else None
            ),
            "component_token_breakdown": component_token_breakdown,
            "characters_per_required_event": (
                round(
                    contract.target_characters
                    / len(contract.ordered_required_events),
                    3,
                )
                if contract and contract.ordered_required_events
                else None
            ),
            "source_ids": (
                [item.source_id for item in contract.source_manifest]
                if contract
                else []
            ),
            "fallback_reason": fallback_reason,
            "compile_elapsed_ms": round(
                (time.perf_counter() - started) * 1000, 3
            ),
            "production_effect": injected,
        }

    @staticmethod
    def _log(record: dict[str, Any]) -> None:
        logger.info(
            "writer_execution_contract_observation=%s",
            _canonical_json(record),
        )
