"""WR3.9+ key-level semantic mapping: legacy StateFrame facts -> WR canonical keys.

Offline, read-only.  Two layers:

1. ``map_legacy_fact`` / ``semantic_compare``: the original v1 aggregate
   comparison (legacy predicate -> WR key + value normalization).
2. ``map_legacy_key`` / ``compare_values`` / ``key_level_compare``: the key-level
   upgrade.  Every legacy fact gets one matrix row with its mapping kind
   (exact/approximate) and value-comparison status; every WR key gets reverse
   coverage (covered / wr_only).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from experiments.world_runtime_writer_canary.semantic_projector_wr2c513r6 import (
    _parse_clock,
)


SUBJECT_ALIASES = {
    "林晚": "character:lin-wan",
    "周野": "character:zhou-ye",
    "季晴": "character:ji-qing",
    "老吴": "character:coworker",
    "当前时间": "world_clock",
    "面包店": "bakery:wild-bread",
    "文章": "article:lin-wan",
}


def _extract_time(value: Any) -> str | None:
    text = str(value)
    match = re.search(r"\d{1,2}[:：]\d{2}", text)
    if match:
        hour, minute = (int(part) for part in re.split(r"[:：]", match.group(0)))
        return f"{hour:02d}:{minute:02d}"
    return _parse_clock(text)


def _extract_time_range(value: Any) -> dict[str, str] | None:
    """Normalize a natural-language time range to ``{start, end}`` HH:MM."""
    text = str(value)
    times = []
    for segment in re.split(r"[至到~～\-—–]", text):
        parsed = _parse_clock(segment)
        if parsed is not None:
            times.append(parsed)
    if len(times) >= 2:
        times = sorted(times)
        return {"start": times[0], "end": times[-1]}
    return None


def _publication_value(value: Any) -> str:
    text = str(value)
    if "发布" in text and "未发布" not in text and "已保存但未发布" not in text:
        return "published"
    return "draft"


def _comment_count(value: Any) -> str | None:
    text = str(value)
    digits = re.search(r"\d+", text)
    if digits:
        return ">0" if int(digits.group(0)) > 0 else "0"
    if any(marker in text for marker in ("有", "新增", "出现")):
        return ">0"
    if any(marker in text for marker in ("没有", "无", "零")):
        return "0"
    return None


def _employment_status(value: Any) -> str | None:
    text = str(value)
    if any(marker in text for marker in ("已辞", "已离职", "正式辞职", "辞了")):
        return "resigned"
    if any(marker in text for marker in ("未定", "考虑", "推迟", "还没", "暂不")):
        return "employed"
    return None


def _resignation_lifecycle(value: Any) -> str | None:
    text = str(value)
    if any(marker in text for marker in ("推迟", "未定", "考虑")):
        return "private_draft"
    if any(marker in text for marker in ("提交", "递交")):
        return "submitted"
    if "撤回" in text:
        return "withdrawn"
    return None


KEY_SPECS: dict[str, dict[str, Any]] = {
    "has_written_article": {
        "spec_id": "article_publication_state",
        "mapping_kind": "exact",
        "subjects": ("林晚",),
        "wr_key": ("continuity_state", "article:lin-wan", "publication_state"),
        "normalize": _publication_value,
        "compare": "exact",
        "note": "legacy 已保存但未发布 -> draft",
    },
    "published_article": {
        "spec_id": "article_publication_state",
        "mapping_kind": "exact",
        "subjects": ("林晚",),
        "wr_key": ("continuity_state", "article:lin-wan", "publication_state"),
        "normalize": _publication_value,
        "compare": "exact",
        "note": "legacy 发布了文章 -> published",
    },
    "is_five_am": {
        "spec_id": "world_clock_time",
        "mapping_kind": "exact",
        "subjects": ("当前时间",),
        "wr_key": ("temporal_state", "world_clock", "time"),
        "normalize": _extract_time,
        "compare": "exact",
        "note": "legacy 五点整 -> 05:00",
    },
    "published_at_5_59": {
        "spec_id": "world_clock_time",
        "mapping_kind": "exact",
        "subjects": ("林晚",),
        "wr_key": ("temporal_state", "world_clock", "time"),
        "normalize": _extract_time,
        "compare": "exact",
        "note": "legacy 在五点五十九分发布文章 -> 05:59",
    },
    "is_early_morning": {
        "spec_id": "world_clock_time_range",
        "mapping_kind": "approximate",
        "subjects": ("当前时间",),
        "wr_key": ("temporal_state", "world_clock", "time"),
        "normalize": _extract_time_range,
        "compare": "range_contains",
        "note": "legacy 时间范围包含 WR 时钟点即视为兼容",
    },
    "has_article_comments": {
        "spec_id": "article_public_comment_count",
        "mapping_kind": "approximate",
        "subjects": ("林晚",),
        "wr_key": ("continuity_state", "article:lin-wan", "public_comment_count"),
        "normalize": _comment_count,
        "compare": "count_compatible",
        "note": "legacy 有评论 -> >0；与 WR 计数按正/零比较",
    },
    "has_quit_job": {
        "spec_id": "employment_status",
        "mapping_kind": "approximate",
        "subjects": ("林晚",),
        "wr_key": ("character_state", "employment:lin-wan", "status"),
        "normalize": _employment_status,
        "compare": "exact",
        "note": "legacy 辞职状态未定/考虑推迟 -> employed（未实际离职）",
    },
    "has_decided_to_delay_quitting": {
        "spec_id": "resignation_lifecycle",
        "mapping_kind": "approximate",
        "subjects": ("林晚",),
        "wr_key": ("continuity_state", "resignation:lin-wan", "lifecycle_state"),
        "normalize": _resignation_lifecycle,
        "compare": "exact",
        "note": "legacy 决定推迟辞职 -> private_draft（未提交）",
    },
}


def map_legacy_key(fact: dict[str, Any]) -> dict[str, Any] | None:
    """Return the key-level mapping (spec, kind, WR key) or None."""
    predicate = str(fact.get("predicate", ""))
    spec = KEY_SPECS.get(predicate)
    if spec is None:
        return None
    subject = str(fact.get("subject", ""))
    if subject not in spec.get("subjects", (subject,)):
        return None
    wr_fact_type, wr_subject, wr_predicate = spec["wr_key"]
    return {
        "spec_id": spec["spec_id"],
        "mapping_kind": spec["mapping_kind"],
        "legacy_key": [
            str(fact.get("fact_type", "")),
            subject,
            predicate,
        ],
        "canonical_legacy_subject": SUBJECT_ALIASES.get(subject, subject),
        "wr_key": [wr_fact_type, wr_subject, wr_predicate],
        "note": spec.get("note", ""),
    }


def map_legacy_fact(fact: dict[str, Any]) -> dict[str, Any] | None:
    """Map one legacy StateFrame fact to a WR canonical key, or None."""
    mapping = map_legacy_key(fact)
    if mapping is None:
        return None
    spec = KEY_SPECS[str(fact.get("predicate", ""))]
    normalize = spec.get("normalize")
    value = normalize(fact.get("value")) if normalize is not None else fact.get("value")
    if value is None:
        return None
    wr_fact_type, wr_subject, wr_predicate = mapping["wr_key"]
    return {
        "fact_type": wr_fact_type,
        "subject": wr_subject,
        "predicate": wr_predicate,
        "value": value,
    }


def map_wr_fact(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_type": fact["fact_type"],
        "subject": fact["subject"],
        "predicate": fact["predicate"],
        "value": fact["value"],
    }


def _key(fact: dict[str, Any]) -> tuple[str, str, str]:
    return (fact["fact_type"], fact["subject"], fact["predicate"])


def semantic_compare(
    legacy_facts: list[dict[str, Any]],
    wr_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    mapped_legacy = []
    unmapped_legacy = 0
    for fact in legacy_facts:
        mapped = map_legacy_fact(fact)
        if mapped is None:
            unmapped_legacy += 1
        else:
            mapped_legacy.append(mapped)
    wr_by_key = {_key(map_wr_fact(fact)): map_wr_fact(fact) for fact in wr_facts}
    legacy_by_key = {_key(fact): fact for fact in mapped_legacy}
    matched = []
    value_mismatch = []
    for key, legacy_fact in legacy_by_key.items():
        wr_fact = wr_by_key.get(key)
        if wr_fact is None:
            continue
        if json_safe(legacy_fact["value"]) == json_safe(wr_fact["value"]):
            matched.append(key)
        else:
            value_mismatch.append({
                "key": list(key),
                "legacy": legacy_fact["value"],
                "wr": wr_fact["value"],
            })
    matched_keys = set(matched)
    value_mismatch_keys = {tuple(item["key"]) for item in value_mismatch}
    covered_legacy_keys = matched_keys | value_mismatch_keys
    legacy_only = [list(key) for key in legacy_by_key.keys() - covered_legacy_keys]
    wr_only = [list(key) for key in wr_by_key.keys() - covered_legacy_keys]
    return {
        "legacy_fact_count": len(legacy_facts),
        "legacy_mapped_count": len(mapped_legacy),
        "legacy_unmapped_by_design_count": unmapped_legacy,
        "wr_fact_count": len(wr_facts),
        "matched_fact_keys": len(matched),
        "value_mismatch_count": len(value_mismatch),
        "value_mismatches": value_mismatch,
        "wr_only_fact_keys": wr_only,
        "legacy_only_mapped_fact_keys": legacy_only,
    }


def compare_values(
    spec: dict[str, Any],
    legacy_value: Any,
    wr_value: Any,
) -> dict[str, Any]:
    """Compare one mapped legacy value against the WR value."""
    normalize = spec.get("normalize")
    normalized = normalize(legacy_value) if normalize is not None else legacy_value
    mode = spec.get("compare", "exact")
    if normalized is None:
        return {
            "status": "legacy_value_unparseable",
            "normalized_legacy_value": None,
            "wr_value": wr_value,
            "detail": "legacy value could not be normalized to a WR-comparable value",
        }
    if mode == "exact":
        same = json_safe(normalized) == json_safe(wr_value)
        return {
            "status": "matched" if same else "value_mismatch",
            "normalized_legacy_value": normalized,
            "wr_value": wr_value,
            "detail": (
                ""
                if same
                else f"legacy={json_safe(normalized)} wr={json_safe(wr_value)}"
            ),
        }
    if mode == "range_contains":
        start, end = normalized["start"], normalized["end"]
        wr_text = str(wr_value)
        inside = start <= wr_text <= end
        return {
            "status": "compatible" if inside else "value_mismatch",
            "normalized_legacy_value": normalized,
            "wr_value": wr_value,
            "detail": (
                f"{start} <= {wr_text} <= {end}"
                if inside
                else f"{wr_text} outside [{start}, {end}]"
            ),
        }
    if mode == "count_compatible":
        try:
            wr_positive = int(wr_value) > 0
        except (TypeError, ValueError):
            wr_positive = str(wr_value) not in ("0", "0.0", "", "none", "null")
        legacy_positive = normalized == ">0"
        same = legacy_positive == wr_positive
        return {
            "status": "matched" if same else "value_mismatch",
            "normalized_legacy_value": normalized,
            "wr_value": wr_value,
            "detail": (
                f"legacy={'positive' if legacy_positive else 'zero'} "
                f"wr={'positive' if wr_positive else 'zero'}"
            ),
        }
    return {
        "status": "unrecognized_compare_mode",
        "normalized_legacy_value": normalized,
        "wr_value": wr_value,
        "detail": f"unknown compare mode {mode!r}",
    }


def key_level_compare(
    legacy_facts: list[dict[str, Any]],
    wr_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Key-level matrix: every legacy fact row + reverse WR coverage."""
    wr_by_key = {_key(map_wr_fact(fact)): fact for fact in wr_facts}
    matrix = []
    covered_wr_keys: set[tuple[str, str, str]] = set()
    for fact in legacy_facts:
        row: dict[str, Any] = {
            "legacy_key": [
                str(fact.get("fact_type", "")),
                str(fact.get("subject", "")),
                str(fact.get("predicate", "")),
            ],
            "legacy_value": fact.get("value"),
        }
        mapping = map_legacy_key(fact)
        row["mapping"] = mapping
        if mapping is None:
            row["status"] = "unmapped_by_design"
            row["detail"] = "legacy-only narrative/carrier field without a WR key"
            matrix.append(row)
            continue
        wr_key = tuple(mapping["wr_key"])
        wr_fact = wr_by_key.get(wr_key)
        if wr_fact is None:
            row["status"] = "wr_key_absent"
            row["detail"] = "mapped WR key absent from this subsection's WR frame"
            matrix.append(row)
            continue
        covered_wr_keys.add(wr_key)
        spec = KEY_SPECS[str(fact.get("predicate", ""))]
        comparison = compare_values(spec, fact.get("value"), wr_fact["value"])
        row["status"] = comparison["status"]
        row["detail"] = comparison["detail"]
        row["normalized_legacy_value"] = comparison.get("normalized_legacy_value")
        row["wr_value"] = wr_fact["value"]
        matrix.append(row)

    wr_coverage = []
    for key, wr_fact in sorted(wr_by_key.items()):
        covered_by = [
            row["legacy_key"]
            for row in matrix
            if row["mapping"] is not None
            and tuple(row["mapping"]["wr_key"]) == key
        ]
        wr_coverage.append({
            "wr_key": list(key),
            "wr_value": wr_fact["value"],
            "covered": key in covered_wr_keys,
            "covered_by_legacy_keys": covered_by,
        })

    status_counts = dict(Counter(row["status"] for row in matrix))
    mapped_rows = [row for row in matrix if row["mapping"] is not None]
    summary = {
        "legacy_fact_count": len(matrix),
        "legacy_mapped_count": len(mapped_rows),
        "legacy_unmapped_by_design_count": status_counts.get(
            "unmapped_by_design", 0
        ),
        "wr_fact_count": len(wr_facts),
        "status_counts": status_counts,
        "wr_covered_key_count": len(covered_wr_keys),
        "wr_only_key_count": len(wr_by_key) - len(covered_wr_keys),
        "wr_only_keys": sorted(
            list(key) for key in wr_by_key.keys() - covered_wr_keys
        ),
    }
    return {
        "schema_version": "wr39-key-level-semantic-v2",
        "summary": summary,
        "matrix": matrix,
        "wr_coverage": wr_coverage,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)
