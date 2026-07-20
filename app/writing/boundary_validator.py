"""Deterministic, non-blocking validation of generated subsection boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .contracts import SceneSpec, StateAssertion


VALIDATOR_VERSION = "phase4r-r5-boundary-v1"
MAX_EXCERPT = 140


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")


@dataclass(frozen=True)
class EvidenceSpan:
    start: int
    end: int
    excerpt: str
    source_id: str
    text_hash: str
    rule_id: str


@dataclass(frozen=True)
class RequiredEventResult:
    rule_id: str
    event_id: str
    passed: bool
    observed_state: str
    reason: str
    confidence: str
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    source_refs: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class BoundaryViolation:
    rule_id: str
    event_id: str
    observed_state: str
    reason: str
    confidence: str
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    source_refs: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class UnsupportedFactWarning:
    rule_id: str
    fact_type: str
    observed_state: str
    reason: str
    confidence: str
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    source_refs: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    candidate_id: str
    section: int
    subsection: int
    output_sha256: str
    validation_status: str
    required_event_results: tuple[dict[str, Any], ...]
    boundary_violations: tuple[dict[str, Any], ...]
    unsupported_fact_warnings: tuple[dict[str, Any], ...]
    evidence_spans: tuple[dict[str, Any], ...]
    source_refs: tuple[dict[str, str], ...]
    validator_version: str
    query_index: int | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ValidationResult":
        return cls(
            candidate_id=value["candidate_id"], section=value["section"],
            subsection=value["subsection"], output_sha256=value["output_sha256"],
            validation_status=value["validation_status"],
            required_event_results=tuple(value["required_event_results"]),
            boundary_violations=tuple(value["boundary_violations"]),
            unsupported_fact_warnings=tuple(value["unsupported_fact_warnings"]),
            evidence_spans=tuple(value["evidence_spans"]),
            source_refs=tuple(value["source_refs"]),
            validator_version=value["validator_version"], query_index=value.get("query_index"),
        )


@dataclass(frozen=True)
class ValidationContract:
    query_index: int | None
    section: int
    subsection: int
    intent: str
    spec_hash: str
    source_refs: tuple[dict[str, str], ...]
    scene_id: str = ""
    assertions: tuple[StateAssertion, ...] = ()
    contract_hash: str = ""
    rule_profile: str | None = None

    @property
    def executable(self) -> bool:
        return self.query_index in {4, 6, 7, 8} or self.rule_profile in {"relative_unknown", "delete_and_face", "future_boundary"}

    @classmethod
    def from_scene_spec(cls, spec: SceneSpec) -> "ValidationContract":
        assertions = tuple(
            spec.planned_events + spec.hard_constraints + spec.forbidden_inferences + spec.unknowns_and_conflicts
        )
        if any(a.status == "unknown" and a.predicate in {"relative", "family_history", "unverified_character_fact"} for a in assertions):
            profile = "relative_unknown"
        elif any("删除" in a.value for a in spec.planned_events) and any("周野" in a.value or "面对" in a.value for a in spec.planned_events):
            profile = "delete_and_face"
        elif any(a.predicate == "future_event_status" or "截止" in a.value or "不得推进" in a.value for a in assertions):
            profile = "future_boundary"
        else:
            profile = None
        evidence_by_id = {item.evidence_id: item for item in spec.evidence}
        refs: list[dict[str, str]] = []
        for assertion in assertions:
            for evidence_id in assertion.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is not None:
                    ref = {"source_id": evidence.source_id, "text_hash": evidence.text_hash, "role": assertion.status}
                    if ref not in refs:
                        refs.append(ref)
        payload = {
            "scene_id": spec.scene_id, "section": spec.section, "subsection": spec.subsection,
            "spec_hash": spec.spec_hash, "assertions": [a.model_dump(mode="json") for a in assertions],
            "source_refs": refs, "rule_profile": profile,
        }
        contract_hash = _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return cls(
            query_index=None, section=spec.section, subsection=spec.subsection,
            intent="scene_spec", spec_hash=spec.spec_hash, source_refs=tuple(refs),
            scene_id=spec.scene_id, assertions=assertions, contract_hash=contract_hash,
            rule_profile=profile,
        )


# R5's rules remain byte-for-byte compatible; production SceneSpecs select the
# same profiles from typed assertion predicates rather than benchmark scene IDs.
class BoundaryValidator:
    def validate(self, contract: ValidationContract, candidate_id: str, text: str, output_hash: str) -> dict[str, Any]:
        text = normalize_text(text)
        if _sha256(text) != output_hash:
            raise AssertionError(f"{candidate_id}: candidate hash mismatch")
        required = self._required_events(contract, text, output_hash)
        boundary = self._boundary_violations(contract, text, output_hash)
        unsupported = self._unsupported_facts(contract, text, output_hash)
        spans = [span for collection in (required, boundary, unsupported) for item in collection for span in item["evidence_spans"]]
        status = "fail" if boundary or any(not item["passed"] for item in required) else "warn" if unsupported else "pass"
        return {
            "query_index": contract.query_index, "candidate_id": candidate_id,
            "section": contract.section, "subsection": contract.subsection,
            "output_sha256": output_hash, "validation_status": status,
            "required_event_results": required, "boundary_violations": boundary,
            "unsupported_fact_warnings": unsupported, "evidence_spans": spans,
            "source_refs": list(contract.source_refs), "validator_version": VALIDATOR_VERSION,
        }

    def validate_typed(self, contract: ValidationContract, candidate_id: str, text: str, output_hash: str) -> ValidationResult:
        return ValidationResult.from_mapping(self.validate(contract, candidate_id, text, output_hash))

    def _required_events(self, contract: ValidationContract, text: str, output_hash: str) -> list[dict[str, Any]]:
        profile = contract.rule_profile or ("delete_and_face" if contract.query_index == 7 else None)
        if profile == "delete_and_face":
            return [self._q7_deletion(contract, text, output_hash), self._q7_confrontation(contract, text, output_hash)]
        if contract.query_index not in {4, 6, 8}:
            return []
        patterns = {
            4: (r"面包婚礼", r"(?:仪式|见证|婚礼)"),
            6: (r"(?:邀请|来店)", r"(?:翻开|读|阅读)[^。！？\n]{0,20}(?:书|随笔)"),
            8: (r"(?:微咸|咸味)", r"(?:分享|尊重|边界|记录)[^。！？\n]{0,20}(?:反思|意义|消费|曝光)?"),
        }[contract.query_index]
        matches = [_first_match(text, (pattern,)) for pattern in patterns]
        present = all(matches)
        spans = [_span(text, m.start(), m.end(), output_hash=output_hash, rule_id=f"required:q{contract.query_index:02d}") for m in matches if m]
        return [_result(
            rule_id=f"required:q{contract.query_index:02d}:intent", event_id="current_scene_intent",
            passed=present, state="current" if present else "missing",
            reason="Multiple independent intent anchors are present." if present else "Required intent anchors are incomplete.",
            spans=spans, source_refs=contract.source_refs, confidence="medium",
        )]

    def _q7_deletion(self, contract: ValidationContract, text: str, output_hash: str) -> dict[str, Any]:
        past = _first_match(text, (r"五天前[^。！？\n]{0,45}(?:删|删除)", r"当晚就删"))
        backup = _first_match(text, (r"(?:草稿|备份)[^。！？\n]{0,90}(?:按下删除|删除|删了)", r"(?:按下删除|删除|删了)[^。！？\n]{0,45}(?:草稿|备份)"))
        current = _first_match(text, (r"(?:视频|照片和视频|记录)[^。！？\n]{0,28}(?:全删了|删除|删了)", r"按下删除"))
        if past and backup:
            matches, state, passed = [past, backup], "conflicted_past_original_and_current_backup", False
            reason = "The original publication is described as deleted in the past while the current action targets a draft or backup."
        elif current:
            matches, state, passed, reason = [current], "current", True, "A current deletion action targets the recorded publication."
        elif past:
            matches, state, passed, reason = [past], "past", False, "Deletion is only described as a past event."
        else:
            matches, state, passed, reason = [], "missing", False, "No current deletion action was found."
        spans = [_span(text, m.start(), m.end(), output_hash=output_hash, rule_id="required:q07:delete_record") for m in matches]
        return _result(rule_id="required:q07:delete_record", event_id="delete_record", passed=passed, state=state, reason=reason, spans=spans, source_refs=contract.source_refs)

    def _q7_confrontation(self, contract: ValidationContract, text: str, output_hash: str) -> dict[str, Any]:
        future = _first_match(text, (r"(?:下个周六|下周六|以后|下一次)[^。！？\n]{0,90}(?:直面|面对|找|见)[^。！？\n]{0,20}(?:周野|他)", r"(?:下个周六|下周六)[^。！？\n]{0,100}(?:周野|他)"))
        current = _first_match(text, (r"(?:推开操作间的门|走到柜台前|进入操作间)[\s\S]{0,180}[“\"]?周野", r"[“\"](?:帖子|照片|那些照片和视频)[^”\"\n]{0,30}(?:删了|全删了)[”\"]?[\s\S]{0,100}周野", r"周野[\s\S]{0,100}[“\"](?:帖子|备份|照片)[^”\"\n]{0,30}(?:删了|全删了)"))
        if future:
            match, state, passed, reason = future, "future", False, "Facing Zhou Ye is explicitly deferred to a future Saturday."
        elif current:
            match, state, passed, reason = current, "current", True, "The candidate enters Zhou Ye's space and directly addresses the deletion in the current scene."
        else:
            match, state, passed, reason = None, "missing", False, "No direct current-scene confrontation with Zhou Ye was found."
        spans = [] if match is None else [_span(text, match.start(), match.end(), output_hash=output_hash, rule_id="required:q07:face_zhou")]
        return _result(rule_id="required:q07:face_zhou", event_id="face_zhou", passed=passed, state=state, reason=reason, spans=spans, source_refs=contract.source_refs)

    def _boundary_violations(self, contract: ValidationContract, text: str, output_hash: str) -> list[dict[str, Any]]:
        if contract.query_index != 8 and contract.rule_profile != "future_boundary":
            return []
        rules = (
            ("delete_publication", (r"(?:删帖|删除[^。！？\n]{0,12}(?:帖子|图文|视频)|(?:帖子|图文|视频)[^。！？\n]{0,12}(?:删除|删了))",)),
            ("ask_zhou", (r"(?:第三个问题|问周野|向周野[^。！？\n]{0,16}问)",)),
            ("return_to_store", (r"(?:次日|第二天|明天|下周六)[^。！？\n]{0,90}(?:到店|面包店|再来|店员)",)),
            ("store_participation", (r"(?:来当店员|作为店员|当店员|店员身份)",)),
            ("current_photograph", (r"(?:她|林晚|随后)[^。！？\n]{0,14}按下快门",)),
            ("publish_content", (r"(?:实际发布|发布[^。！？\n]{0,16}(?:内容|切片|帖子)|发上网|发出去)",)),
        )
        result = []
        for event_id, patterns in rules:
            match = _first_match(text, patterns)
            if not match or _is_negated(text, match.start(), match.end()):
                continue
            context = text[max(0, match.start() - 28):min(len(text), match.end() + 28)]
            state = "planned" if re.search(r"明天|次日|下周|会|准备|计划", context) else "occurred_or_advanced"
            span = _span(text, match.start(), match.end(), output_hash=output_hash, rule_id=f"boundary:q08:{event_id}")
            result.append({"rule_id": f"boundary:q08:{event_id}", "event_id": event_id, "observed_state": state, "reason": "The narrative advances into an event after the frozen reflection boundary.", "confidence": "high", "evidence_spans": [span], "source_refs": list(contract.source_refs)})
        return result

    def _unsupported_facts(self, contract: ValidationContract, text: str, output_hash: str) -> list[dict[str, Any]]:
        if contract.query_index != 4 and contract.rule_profile != "relative_unknown":
            return []
        patterns = (r"(?:老刘|我)[^。！？\n]{0,16}(?:父亲|母亲|爹|妈)[^。！？\n]{0,65}(?:去世|走了|留下|烤|种)", r"周野父亲", r"父子俩")
        result, seen = [], set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                key = match.start() // 80
                if key in seen:
                    continue
                seen.add(key)
                span = _span(text, match.start(), match.end(), output_hash=output_hash, rule_id="unknown:q04:relative_fact")
                result.append({"rule_id": "unknown:q04:relative_fact", "fact_type": "unverified_relative_or_family_history", "observed_state": "asserted_as_fact", "reason": "A relative or family-history assertion appears although the frozen state marks unlisted attendee relatives as unknown.", "confidence": "medium", "evidence_spans": [span], "source_refs": list(contract.source_refs)})
        return result


def _span(text: str, start: int, end: int, *, output_hash: str, rule_id: str) -> dict[str, Any]:
    excerpt = text[max(0, start - 28):min(len(text), end + 48)].replace("\n", " ").strip()[:MAX_EXCERPT]
    return {"start": start, "end": end, "excerpt": excerpt, "source_id": "generated_candidate", "text_hash": output_hash, "rule_id": rule_id}


def _first_match(text: str, patterns: Iterable[str], flags: int = 0) -> re.Match[str] | None:
    matches = [match for pattern in patterns if (match := re.search(pattern, text, flags))]
    return min(matches, key=lambda item: item.start()) if matches else None


def _is_negated(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 8):min(len(text), end + 8)]
    return bool(re.search(r"(?:没|没有|未|不再|别|不要)[^。！？\n]{0,6}$", text[max(0, start - 10):start])) or bool(re.search(r"(?:没|没有|未|不再|别|不要).{0,8}(?:删|拍|发|去|到)", window))


def _result(*, rule_id: str, event_id: str, passed: bool, state: str, reason: str, spans: list[dict[str, Any]], source_refs: tuple[dict[str, str], ...], confidence: str = "high") -> dict[str, Any]:
    return {"rule_id": rule_id, "event_id": event_id, "passed": passed, "observed_state": state, "reason": reason, "confidence": confidence, "evidence_spans": spans, "source_refs": list(source_refs)}
