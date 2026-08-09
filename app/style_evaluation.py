"""Post-write style drift evaluation with deterministic, text-level evidence."""

from __future__ import annotations

import hashlib
import re
from statistics import median
from typing import Any

from .realization_policy import normalize_style_profile
from .style_observability import analyse_text, split_sentences


STYLE_EVALUATION_VERSION = "style-drift-evaluation-v1"
_EXPLANATION = re.compile(
    r"(?:意识到|明白|知道|这意味着|也就是说|换句话说|原来|之所以|这让[^。！？]{0,12}(?:明白|懂得|意识到))"
)
_CLOSURE = re.compile(
    r"(?:终于明白|一切都(?:结束|解决|过去)|所有问题都|从此|这就是|事情已经结束|得到了解决)"
)
_ORDERED = re.compile(r"(?:第一步|第二步|第三步|首先|其次|最后)")


def _evidence(text: str, pattern: re.Pattern[str], limit: int = 3) -> list[dict[str, Any]]:
    items = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - 18)
        end = min(len(text), match.end() + 28)
        items.append(
            {
                "start": match.start(),
                "end": match.end(),
                "excerpt": text[start:end].replace("\n", " ")[:80],
            }
        )
        if len(items) >= limit:
            break
    return items


def _ratio(metrics: dict[str, Any], bucket: str) -> float:
    count = max(int(metrics["sentence_length"]["count"]), 1)
    return round(int(metrics["sentence_length_buckets"][bucket]) / count, 4)


def _target_deviations(
    metrics: dict[str, Any],
    profile: dict[str, Any],
    beat: dict[str, Any],
) -> list[dict[str, Any]]:
    dialogue = float(metrics["dialogue_ratio"])
    dialogue_target = float(profile["dialogue_ratio"])
    dialogue_gap = abs(dialogue - dialogue_target)
    results = [
        {
            "control": "dialogue_ratio",
            "actual": dialogue,
            "target": dialogue_target,
            "tolerance": 0.12,
            "status": "outside_range" if dialogue_gap > 0.12 else "within_range",
            "classification": "unexplained_drift" if dialogue_gap > 0.12 else "aligned",
            "proxy_limit": "Quoted-character ratio does not measure dialogue quality.",
        }
    ]

    preference = profile["sentence_preference"]
    short_ratio = _ratio(metrics, "short_le_12")
    long_ratio = _ratio(metrics, "long_gt_30")
    aligned = (
        short_ratio >= 0.45 if preference == "short"
        else long_ratio >= 0.25 if preference == "long"
        else short_ratio < 0.65 and long_ratio < 0.55
    )
    extreme_beat = int(beat.get("intensity", 5) or 5) in {0, 1, 2, 3, 8, 9, 10}
    results.append(
        {
            "control": "sentence_preference",
            "actual": {"short_ratio": short_ratio, "long_ratio": long_ratio},
            "target": preference,
            "status": "within_range" if aligned else "outside_range",
            "classification": (
                "aligned" if aligned
                else "intentional_modulation" if extreme_beat
                else "unexplained_drift"
            ),
            "proxy_limit": "Length buckets do not determine sentence quality.",
        }
    )

    sensory = float(metrics["sensory_terms_per_1k"])
    sensory_range = {
        "sparse": (0.0, 12.0),
        "medium": (3.0, 24.0),
        "rich": (8.0, 40.0),
    }[profile["sensory_density"]]
    sensory_aligned = sensory_range[0] <= sensory <= sensory_range[1]
    results.append(
        {
            "control": "sensory_density",
            "actual": sensory,
            "target": profile["sensory_density"],
            "expected_proxy_range": list(sensory_range),
            "status": "within_range" if sensory_aligned else "outside_range",
            "classification": "aligned" if sensory_aligned else "unexplained_drift",
            "proxy_limit": "A fixed lexicon measures frequency, not image quality or motivation.",
        }
    )
    return results


def _content_signals(text: str, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    sentences = split_sentences(text)
    tail = "".join(sentences[-2:])
    explanation_evidence = _evidence(text, _EXPLANATION)
    closure_evidence = _evidence(tail, _CLOSURE)
    ordered_evidence = _evidence(text, _ORDERED)
    structural_evidence = [
        {
            "start_sentence": item["start_sentence"],
            "length": item["length"],
            "signature": item.get("signature"),
        }
        for item in metrics["consecutive_isomorphic_sentence_runs"][:3]
    ]

    return [
        {
            "signal_id": "explanation_pressure",
            "status": "observe" if len(explanation_evidence) >= 2 else "clear",
            "count": len(_EXPLANATION.findall(text)),
            "evidence": explanation_evidence,
            "interpretation": "Repeated explicit interpretation may explain meaning already carried by action or dialogue.",
        },
        {
            "signal_id": "summary_closure",
            "status": "observe" if closure_evidence else "clear",
            "count": len(_CLOSURE.findall(tail)),
            "evidence": closure_evidence,
            "interpretation": "A closing sentence may over-resolve meaning; verify against the intended scene outcome.",
        },
        {
            "signal_id": "visible_structure",
            "status": "observe" if ordered_evidence or metrics["mechanical_start_ratio"] >= 0.2 else "clear",
            "count": len(_ORDERED.findall(text)),
            "evidence": ordered_evidence,
            "interpretation": "Ordered markers or repeated mechanical openings may expose the upstream task structure.",
        },
        {
            "signal_id": "isomorphic_sentence_run",
            "status": "observe" if structural_evidence else "clear",
            "count": len(structural_evidence),
            "evidence": structural_evidence,
            "interpretation": "Repeated structural signatures are a review pointer, not an automatic defect.",
        },
    ]


def _history_comparison(
    metrics: dict[str, Any],
    previous_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    tracked = ("dialogue_ratio", "sensory_terms_per_1k", "psychological_exposition_per_1k")
    comparisons = []
    for key in tracked:
        values = [
            float(item["metrics"][key])
            for item in previous_reports
            if isinstance(item.get("metrics"), dict) and key in item["metrics"]
        ]
        if not values:
            continue
        baseline = median(values[-5:])
        actual = float(metrics[key])
        tolerance = 0.12 if key == "dialogue_ratio" else max(3.0, abs(baseline) * 0.75)
        comparisons.append(
            {
                "metric": key,
                "actual": actual,
                "recent_median": round(baseline, 4),
                "absolute_delta": round(abs(actual - baseline), 4),
                "status": "shift" if abs(actual - baseline) > tolerance else "stable",
            }
        )
    return {"baseline_count": len(previous_reports), "window": 5, "metrics": comparisons}


def evaluate_style_drift(
    text: str,
    target_style: dict | None,
    *,
    section: int | None = None,
    subsection: int | None = None,
    beat: dict | None = None,
    previous_reports: list[dict[str, Any]] | None = None,
    character_names: list[str] | None = None,
) -> dict[str, Any]:
    text = text or ""
    beat = beat if isinstance(beat, dict) else {}
    profile = normalize_style_profile(target_style)
    metrics = analyse_text(text, character_names or [])
    deviations = _target_deviations(metrics, profile, beat)
    signals = _content_signals(text, metrics)
    history = _history_comparison(metrics, previous_reports or [])
    unexplained = sum(item["classification"] == "unexplained_drift" for item in deviations)
    observed = sum(item["status"] == "observe" for item in signals)
    historical_shifts = sum(item["status"] == "shift" for item in history["metrics"])
    status = "drift" if unexplained >= 2 or observed >= 3 else "observe" if unexplained or observed or historical_shifts else "aligned"

    metrics.pop("sentence_signatures", None)
    return {
        "schema_version": STYLE_EVALUATION_VERSION,
        "section": section,
        "subsection": subsection,
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "characters": metrics["characters"],
        "target_profile": profile,
        "beat_context": {
            "intensity": beat.get("intensity"),
            "character_focus": beat.get("character_focus", ""),
        },
        "status": status,
        "target_deviations": deviations,
        "history_comparison": history,
        "content_signals": signals,
        "metrics": metrics,
        "manual_dimensions": {
            "emotion_intensity": {
                "status": "human_or_llm_required",
                "observable_only": "psychological_exposition_per_1k",
            },
            "naturalness": {"status": "human_blind_review_required"},
            "character_credibility": {"status": "human_blind_review_required"},
            "emotional_residue": {"status": "human_blind_review_required"},
        },
        "automatic_rewrite_recommended": False,
        "interpretation": "Signals identify text for review; they do not prove human quality or authorize rewriting.",
    }


class StyleDriftTracker:
    def __init__(self, target_style: dict | None, *, character_names: list[str] | None = None):
        self.target_style = target_style
        self.character_names = character_names or []
        self.reports: list[dict[str, Any]] = []

    def observe(
        self,
        text: str,
        *,
        section: int,
        subsection: int,
        beat: dict | None = None,
    ) -> dict[str, Any]:
        report = evaluate_style_drift(
            text,
            self.target_style,
            section=section,
            subsection=subsection,
            beat=beat,
            previous_reports=self.reports,
            character_names=self.character_names,
        )
        self.reports.append(report)
        return report
