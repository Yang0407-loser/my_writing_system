"""Run the offline Phase 4R R5 deterministic post-generation validator.

The prediction phase intentionally has no dependency on evaluation artifacts or
arm mappings. It reads frozen R2/R3 source manifests and anonymous R3 outputs,
then writes a private prediction file under the existing gitignored runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tests.benchmarks.benchmark_phase4r_r2_scene_spec import SCENES, compile_scene


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".phase4r_r3_runtime"
R3_PUBLIC = ROOT / "reports" / "phase4r-batch-r3-package-manifest.json"
DEFAULT_OUTPUT = RUNTIME / "r5" / "predictions.json"
TASK_ID = "07d1391e-06ff-4af3-8bd7-6a404d2f4fd6"
VALIDATOR_VERSION = "phase4r-r5-boundary-v1"
MAX_EXCERPT = 140


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")


@dataclass(frozen=True)
class Contract:
    query_index: int
    section: int
    subsection: int
    intent: str
    spec_hash: str
    source_refs: tuple[dict[str, str], ...]


def _query_manifest(public: dict[str, Any], query_index: int) -> dict[str, Any]:
    return next(item for item in public["queries"] if int(item["query_index"]) == query_index)


def _context_source(query: dict[str, Any]) -> dict[str, str]:
    item = next(
        item for item in query["arms"]["legacy_full"]["context_items"]
        if item["item_id"] == "current:mandatory_events"
    )
    return {
        "source_id": item["source_id"],
        "text_hash": item["text_hash"],
        "role": "current_writing_requirement",
    }


def build_contract(public: dict[str, Any], query_index: int) -> Contract:
    query = _query_manifest(public, query_index)
    spec, _ = compile_scene(query_index, SCENES[query_index], TASK_ID)
    expected = query["arms"]["broker_scene_spec"]
    if spec.spec_hash != expected["scene_spec_hash"]:
        raise AssertionError(f"q{query_index}: frozen SceneSpec hash changed")
    scene_source = expected["scene_spec_source_manifest"][0]
    refs = (
        {
            "source_id": scene_source["source_id"],
            "text_hash": scene_source["text_hash"],
            "role": "scene_spec_constraint",
        },
        _context_source(query),
    )
    return Contract(
        query_index=query_index,
        section=int(query["section"]),
        subsection=int(query["subsection"]),
        intent=SCENES[query_index]["intent"],
        spec_hash=spec.spec_hash,
        source_refs=refs,
    )


def _span(text: str, start: int, end: int, *, output_hash: str, rule_id: str) -> dict[str, Any]:
    left = max(0, start - 28)
    right = min(len(text), end + 48)
    excerpt = text[left:right].replace("\n", " ").strip()
    if len(excerpt) > MAX_EXCERPT:
        excerpt = excerpt[:MAX_EXCERPT]
    return {
        "start": start,
        "end": end,
        "excerpt": excerpt,
        "source_id": "generated_candidate",
        "text_hash": output_hash,
        "rule_id": rule_id,
    }


def _first_match(text: str, patterns: Iterable[str], flags: int = 0) -> re.Match[str] | None:
    matches = []
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            matches.append(match)
    return min(matches, key=lambda item: item.start()) if matches else None


def _is_negated(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 8):min(len(text), end + 8)]
    return bool(re.search(r"(?:没|没有|未|不再|别|不要)[^。！？\n]{0,6}$", text[max(0, start - 10):start])) or bool(
        re.search(r"(?:没|没有|未|不再|别|不要).{0,8}(?:删|拍|发|去|到)", window)
    )


def _result(
    *, rule_id: str, event_id: str, passed: bool, state: str, reason: str,
    spans: list[dict[str, Any]], source_refs: tuple[dict[str, str], ...], confidence: str = "high",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "event_id": event_id,
        "passed": passed,
        "observed_state": state,
        "reason": reason,
        "confidence": confidence,
        "evidence_spans": spans,
        "source_refs": list(source_refs),
    }


class BoundaryValidator:
    def validate(self, contract: Contract, candidate_id: str, text: str, output_hash: str) -> dict[str, Any]:
        text = normalize_text(text)
        if _sha256(text) != output_hash:
            raise AssertionError(f"{candidate_id}: candidate hash mismatch")
        required = self._required_events(contract, text, output_hash)
        boundary = self._boundary_violations(contract, text, output_hash)
        unsupported = self._unsupported_facts(contract, text, output_hash)
        spans = [
            span
            for collection in (required, boundary, unsupported)
            for item in collection
            for span in item["evidence_spans"]
        ]
        if boundary or any(not item["passed"] for item in required):
            status = "fail"
        elif unsupported:
            status = "warn"
        else:
            status = "pass"
        return {
            "query_index": contract.query_index,
            "candidate_id": candidate_id,
            "section": contract.section,
            "subsection": contract.subsection,
            "output_sha256": output_hash,
            "validation_status": status,
            "required_event_results": required,
            "boundary_violations": boundary,
            "unsupported_fact_warnings": unsupported,
            "evidence_spans": spans,
            "source_refs": list(contract.source_refs),
            "validator_version": VALIDATOR_VERSION,
        }

    def _required_events(self, contract: Contract, text: str, output_hash: str) -> list[dict[str, Any]]:
        if contract.query_index == 7:
            deletion = self._q7_deletion(contract, text, output_hash)
            confrontation = self._q7_confrontation(contract, text, output_hash)
            return [deletion, confrontation]

        patterns = {
            4: (r"面包婚礼", r"(?:仪式|见证|婚礼)"),
            6: (r"(?:邀请|来店)", r"(?:翻开|读|阅读)[^。！？\n]{0,20}(?:书|随笔)"),
            8: (r"(?:微咸|咸味)", r"(?:分享|尊重|边界|记录)[^。！？\n]{0,20}(?:反思|意义|消费|曝光)?"),
        }[contract.query_index]
        matches = [_first_match(text, (pattern,)) for pattern in patterns]
        present = all(matches)
        spans = [
            _span(text, match.start(), match.end(), output_hash=output_hash, rule_id=f"required:q{contract.query_index:02d}")
            for match in matches if match
        ]
        return [_result(
            rule_id=f"required:q{contract.query_index:02d}:intent",
            event_id="current_scene_intent",
            passed=present,
            state="current" if present else "missing",
            reason="Multiple independent intent anchors are present." if present else "Required intent anchors are incomplete.",
            spans=spans,
            source_refs=contract.source_refs,
            confidence="medium",
        )]

    def _q7_deletion(self, contract: Contract, text: str, output_hash: str) -> dict[str, Any]:
        past = _first_match(text, (r"五天前[^。！？\n]{0,45}(?:删|删除)", r"当晚就删"))
        backup_context = _first_match(text, (
            r"(?:草稿|备份)[^。！？\n]{0,90}(?:按下删除|删除|删了)",
            r"(?:按下删除|删除|删了)[^。！？\n]{0,45}(?:草稿|备份)",
        ))
        current = _first_match(text, (
            r"(?:视频|照片和视频|记录)[^。！？\n]{0,28}(?:全删了|删除|删了)",
            r"按下删除",
        ))
        if past and backup_context:
            matches = [past, backup_context]
            state = "conflicted_past_original_and_current_backup"
            passed = False
            reason = "The original publication is described as deleted in the past while the current action targets a draft or backup."
        elif current:
            matches = [current]
            state = "current"
            passed = True
            reason = "A current deletion action targets the recorded publication."
        elif past:
            matches = [past]
            state = "past"
            passed = False
            reason = "Deletion is only described as a past event."
        else:
            matches = []
            state = "missing"
            passed = False
            reason = "No current deletion action was found."
        spans = [
            _span(text, match.start(), match.end(), output_hash=output_hash, rule_id="required:q07:delete_record")
            for match in matches
        ]
        return _result(
            rule_id="required:q07:delete_record", event_id="delete_record",
            passed=passed, state=state, reason=reason, spans=spans, source_refs=contract.source_refs,
        )

    def _q7_confrontation(self, contract: Contract, text: str, output_hash: str) -> dict[str, Any]:
        future = _first_match(text, (
            r"(?:下个周六|下周六|以后|下一次)[^。！？\n]{0,90}(?:直面|面对|找|见)[^。！？\n]{0,20}(?:周野|他)",
            r"(?:下个周六|下周六)[^。！？\n]{0,100}(?:周野|他)",
        ))
        current = _first_match(text, (
            r"(?:推开操作间的门|走到柜台前|进入操作间)[\s\S]{0,180}[“\"]?周野",
            r"[“\"](?:帖子|照片|那些照片和视频)[^”\"\n]{0,30}(?:删了|全删了)[”\"]?[\s\S]{0,100}周野",
            r"周野[\s\S]{0,100}[“\"](?:帖子|备份|照片)[^”\"\n]{0,30}(?:删了|全删了)",
        ))
        if future:
            match, state, passed = future, "future", False
            reason = "Facing Zhou Ye is explicitly deferred to a future Saturday."
        elif current:
            match, state, passed = current, "current", True
            reason = "The candidate enters Zhou Ye's space and directly addresses the deletion in the current scene."
        else:
            match, state, passed = None, "missing", False
            reason = "No direct current-scene confrontation with Zhou Ye was found."
        spans = [] if match is None else [
            _span(text, match.start(), match.end(), output_hash=output_hash, rule_id="required:q07:face_zhou")
        ]
        return _result(
            rule_id="required:q07:face_zhou", event_id="face_zhou",
            passed=passed, state=state, reason=reason, spans=spans, source_refs=contract.source_refs,
        )

    def _boundary_violations(self, contract: Contract, text: str, output_hash: str) -> list[dict[str, Any]]:
        if contract.query_index != 8:
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
            result.append({
                "rule_id": f"boundary:q08:{event_id}",
                "event_id": event_id,
                "observed_state": state,
                "reason": "The narrative advances into an event after the frozen reflection boundary.",
                "confidence": "high",
                "evidence_spans": [span],
                "source_refs": list(contract.source_refs),
            })
        return result

    def _unsupported_facts(self, contract: Contract, text: str, output_hash: str) -> list[dict[str, Any]]:
        if contract.query_index != 4:
            return []
        patterns = (
            r"(?:老刘|我)[^。！？\n]{0,16}(?:父亲|母亲|爹|妈)[^。！？\n]{0,65}(?:去世|走了|留下|烤|种)",
            r"周野父亲",
            r"父子俩",
        )
        result = []
        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                key = match.start() // 80
                if key in seen:
                    continue
                seen.add(key)
                span = _span(text, match.start(), match.end(), output_hash=output_hash, rule_id="unknown:q04:relative_fact")
                result.append({
                    "rule_id": "unknown:q04:relative_fact",
                    "fact_type": "unverified_relative_or_family_history",
                    "observed_state": "asserted_as_fact",
                    "reason": "A relative or family-history assertion appears although the frozen state marks unlisted attendee relatives as unknown.",
                    "confidence": "medium",
                    "evidence_spans": [span],
                    "source_refs": list(contract.source_refs),
                })
        return result


def build_predictions() -> dict[str, Any]:
    public = _read_json(R3_PUBLIC)
    run_manifest = _read_json(RUNTIME / "run_manifest.json")
    if int(run_manifest.get("generation_calls", 0)) != 12:
        raise AssertionError("R5 requires all 12 frozen R3 candidates")
    validator = BoundaryValidator()
    predictions = []
    for query in run_manifest["queries"]:
        query_index = int(query["query_index"])
        contract = build_contract(public, query_index)
        for candidate in query["candidates"]:
            candidate_id = candidate["candidate_id"]
            output_hash = candidate["output_sha256"]
            text = (RUNTIME / f"q{query_index:02d}" / f"{candidate_id}.txt").read_text(encoding="utf-8")
            predictions.append(validator.validate(contract, candidate_id, text, output_hash))
    if len(predictions) != 12:
        raise AssertionError("expected 12 predictions")
    return {
        "schema_version": "phase4r-r5-predictions-v1",
        "mode": "offline_prediction_only",
        "validator_version": VALIDATOR_VERSION,
        "writer_generation_calls": 0,
        "llm_calls": 0,
        "production_behavior_changed": False,
        "production_messages_hash_unchanged": public["production_messages_hash_unchanged"],
        "runtime_answer_fields_used": [],
        "candidate_count": len(predictions),
        "predictions": predictions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_predictions()
    _write_json(args.output, payload)
    print(json.dumps({
        "candidate_count": payload["candidate_count"],
        "prediction_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
