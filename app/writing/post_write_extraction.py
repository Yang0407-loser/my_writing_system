"""One-call typed extraction contract for committed subsection text."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..utils.json_parser import parse_json
from ..utils.llm_client import cost_label
from .contracts import PostWriteEvidence, PostWriteStateBundle, PostWriteStateChange


EXTRACTOR_VERSION = "post-write-state-v1"
MAX_INPUT_CHARACTERS = 8000
ALLOWED_CATEGORIES = {
    "handover",
    "character_state",
    "relationship",
    "temporal_state",
    "location_state",
    "character_presence",
    "event",
    "experience",
    "foreshadowing",
}
ALLOWED_STATUSES = {"confirmed", "unknown", "conflicted"}

POST_WRITE_EXTRACTION_PROMPT = """从下面已经完成的小节正文中提取可供后续写作使用的状态变化。

已知角色和待承接事件（只用于ID对齐，不代表正文已经完成）：
{known_context}

正文：
{text}

只输出 JSON：
{{
  "changes": [
    {{
      "category": "handover|character_state|relationship|temporal_state|location_state|character_presence|event|experience|foreshadowing",
      "subject": "状态主体",
      "predicate": "稳定、明确的英文snake_case谓词",
      "value": "状态值或事件概述",
      "status": "confirmed|unknown|conflicted",
      "confidence": 0.0,
      "evidence_text": "必须逐字复制自正文的最短证据"
    }}
  ]
}}

规则：
1. 只提取正文明确支持、且会影响后续连续性的状态；不要推测。
2. unknown表示正文明确说未知，conflicted表示正文自身给出冲突信息；不得把不确定内容写成confirmed。
3. evidence_text必须是正文中的连续原文；找不到逐字证据就不要输出该项。
4. 普通修辞、气氛、重复描述和没有状态变化的对话不要输出。
5. 没有值得记录的变化时返回{{"changes": []}}。
"""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(payload)


def _string_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sanitize_source_manifest(items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in items or []:
        source_id = str(item.get("source_id", ""))
        text_hash = str(item.get("text_hash", ""))
        if source_id or text_hash:
            result.append({"source_id": source_id, "text_hash": text_hash})
    return result


class SharedPostWriteExtractor:
    """Call one extractor and accept only changes with exact source evidence."""

    def __init__(self, llm_client) -> None:
        self.llm = llm_client

    def extract(
        self,
        *,
        task_id: str,
        section: int,
        subsection: int,
        text: str,
        output_hash: str,
        source_manifest: list[dict[str, Any]] | None = None,
        known_context: dict[str, Any] | None = None,
    ) -> PostWriteStateBundle:
        if _sha256(text) != output_hash:
            raise ValueError("output_hash_mismatch")
        if not text.strip():
            raise ValueError("empty_committed_text")

        input_text = text[:MAX_INPUT_CHARACTERS]
        known_context = known_context or {}
        known_context_json = json.dumps(
            known_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with cost_label("post_write_extraction"):
            response = self.llm.chat_completion(
                [
                    {"role": "system", "content": "你是一位严谨的小说状态记录员，只输出JSON。"},
                    {"role": "user", "content": POST_WRITE_EXTRACTION_PROMPT.format(
                        text=input_text,
                        known_context=known_context_json,
                    )},
                ],
                temperature=0.2,
                max_tokens=1800,
                json_mode=True,
                prompt_name="post_write_state_extraction",
            )
        parsed = parse_json(response)
        if not isinstance(parsed, dict):
            raise ValueError("invalid_extraction_shape")

        warnings: list[str] = []
        if len(text) > MAX_INPUT_CHARACTERS:
            warnings.append("input_truncated")
        changes: list[PostWriteStateChange] = []
        source_id = f"writer-output:{task_id}:{section}:{subsection}"

        for index, raw in enumerate(parsed.get("changes", [])):
            if not isinstance(raw, dict):
                warnings.append(f"change_{index}:not_object")
                continue
            category = str(raw.get("category", "")).strip()
            status = str(raw.get("status", "")).strip()
            subject = str(raw.get("subject", "")).strip()
            predicate = str(raw.get("predicate", "")).strip()
            value = _string_value(raw.get("value", ""))
            evidence_text = str(raw.get("evidence_text", "")).strip()
            if category not in ALLOWED_CATEGORIES:
                warnings.append(f"change_{index}:invalid_category")
                continue
            if status not in ALLOWED_STATUSES:
                warnings.append(f"change_{index}:invalid_status")
                continue
            if not subject or not predicate or not value or not evidence_text:
                warnings.append(f"change_{index}:missing_required_field")
                continue
            start = text.find(evidence_text)
            if start < 0:
                warnings.append(f"change_{index}:evidence_not_found")
                continue
            end = start + len(evidence_text)
            confidence = raw.get("confidence", 0.0)
            try:
                confidence = min(1.0, max(0.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = 0.0
                warnings.append(f"change_{index}:invalid_confidence")
            identity = {
                "category": category,
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "status": status,
                "start": start,
                "end": end,
            }
            change_id = f"pws:{_canonical_hash(identity)[:20]}"
            evidence = PostWriteEvidence(
                evidence_id=f"evidence:{change_id}",
                source_id=source_id,
                text_hash=output_hash,
                span_start=start,
                span_end=end,
                excerpt=evidence_text[:140],
            )
            changes.append(PostWriteStateChange(
                change_id=change_id,
                category=category,
                subject=subject,
                predicate=predicate,
                value=value,
                status=status,
                confidence=confidence,
                evidence=[evidence],
            ))

        manifest = sanitize_source_manifest(source_manifest)
        if known_context:
            manifest.append({
                "source_id": (
                    f"post-write-known-context:{_sha256(task_id)[:12]}:"
                    f"{section}:{subsection}"
                ),
                "text_hash": _sha256(known_context_json),
            })
        bundle_body = {
            "task_id": task_id,
            "section": section,
            "subsection": subsection,
            "output_hash": output_hash,
            "source_manifest": manifest,
            "changes": [item.model_dump(mode="json") for item in changes],
            "extraction_warnings": warnings,
            "schema_version": EXTRACTOR_VERSION,
        }
        return PostWriteStateBundle(
            **bundle_body,
            bundle_hash=_canonical_hash(bundle_body),
        )
