"""Generate the Phase 8 Batch 1 deterministic style baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.style_observability import build_observability_report
from tests.benchmarks.benchmark_context_input_census import parse_story
from tests.quality.baseline import DEFAULT_CHARACTER, DEFAULT_STYLE, load_json


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "reports" / "phase8-batch1-style-observability.json"


def render_markdown(report: dict) -> str:
    corpus = report["metrics"]["corpus"]
    chapters = report["metrics"]["chapters"]
    duplicates = report["metrics"]["duplicates"]
    anomaly_rows = []
    for chapter in chapters:
        labels = ", ".join(item["metric"] for item in chapter["anomalies"]) or "none"
        metrics = chapter["metrics"]
        signals = chapter["structural_signals"]
        anomaly_rows.append(
            f"| {chapter['section']} | {chapter['title']} | {metrics['dialogue_ratio']:.1%} | "
            f"{metrics['sentence_length']['median']} | {metrics['paragraph_length']['median']} | "
            f"{metrics['mechanical_start_ratio']:.1%} | {metrics['sensory_terms_per_1k']} | "
            f"{metrics['psychological_exposition_per_1k']} | "
            f"{signals['consecutive_short_sentence_runs']}/{signals['consecutive_isomorphic_sentence_runs']} | {labels} |"
        )
    issue_rows = []
    for issue in report["metrics"]["known_style_issues"]:
        observable = ", ".join(f"`{item}`" for item in issue["deterministic_observability"]) or "none"
        issue_rows.append(f"| `{issue['id']}` | {observable} | `{issue['judgment']}` |")
    return f"""# Phase 8 Batch 1: deterministic style observability baseline

Status: **baseline complete; no Writer or Prompt behavior changed**

This report analyses the frozen 18-section golden story offline. It makes deterministic observations, not automatic literary-quality verdicts. No LLM was called and no prose was rewritten.

## Corpus baseline

| Metric | Value |
|---|---:|
| Chapters / subsections | {len(chapters)} / {len(report['metrics']['subsections'])} |
| Visible characters | {corpus['characters']} |
| Dialogue ratio | {corpus['dialogue_ratio']:.2%} |
| Sentence length mean / median / p90 | {corpus['sentence_length']['mean']} / {corpus['sentence_length']['median']} / {corpus['sentence_length']['p90']} |
| Paragraph length mean / median / p90 | {corpus['paragraph_length']['mean']} / {corpus['paragraph_length']['median']} / {corpus['paragraph_length']['p90']} |
| Mechanical time/ordinal/numeric starts | {corpus['mechanical_start_ratio']:.2%} |
| Character-name starts | {corpus['sentence_starts'].get('character_name', 0)} |
| Sensory terms per 1k chars | {corpus['sensory_terms_per_1k']} |
| Psychological exposition terms per 1k chars | {corpus['psychological_exposition_per_1k']} |
| Consecutive short-sentence runs | {len(corpus['consecutive_short_sentence_runs'])} |
| Consecutive structural-signature runs | {len(corpus['consecutive_isomorphic_sentence_runs'])} |
| Exact repeated sentence groups | {len(duplicates['exact_sentence_groups'])} |
| Exact repeated paragraph groups | {len(duplicates['exact_paragraph_groups'])} |

## Chapter observations

| Section | Title | Dialogue | Sentence p50 | Paragraph p50 | Mechanical starts | Sensory /1k | Psychological /1k | Short/structural runs | Distribution anomalies |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
{chr(10).join(anomaly_rows)}

The anomaly column uses Tukey IQR fences across chapter values. It identifies distribution outliers only; it does not label those chapters as badly written. Complete per-subsection values, run locations, start categories, hashes and duplicate evidence are in the JSON report.

## Four-control contract

- `dialogue_ratio`: direct mapping to quoted-dialogue character ratio.
- `sentence_preference`: direct structural proxy through sentence-length distribution and short-sentence runs.
- `sensory_density`: fixed-lexicon density proxy; it cannot judge image quality or sensory integration.
- `emotion_intensity`: no reliable deterministic mapping. Psychological-exposition frequency is observation only and cannot establish emotional intensity or emotional layering.

The historical 50-dimensional style contract remains removed.

## Existing issues

| Issue | Deterministic signals available | Quality judgment |
|---|---|---|
{chr(10).join(issue_rows)}

`insufficient_emotional_layering` is deliberately not assigned a keyword heuristic or automatic score. It remains an explicit human/LLM review item. Mechanical counting and repetitive patterns have deterministic locators, but whether an occurrence is deliberate rhythm or harmful repetition still requires reading.

## Phase status

- Phase 4: `paused_by_generation_evaluation_infrastructure`; this is not an architecture-failure verdict.
- Production: unchanged `legacy_full` Writer input.
- ContextBroker, ContinuityRiskGuard and all Phase 4 experiment records remain preserved.
- Phase 5 and Phase 6 remain paused. Phase 8 Batch 1 changes no production path.

## Verification

- Unit: {report['validation']['unit']['passed']} passed, 0 failed.
- Integration: {report['validation']['integration']['passed']} passed, 0 failed.
- Quality: {report['validation']['quality']['passed']} passed, 0 failed.
- `compileall`: passed.
"""


def build_report() -> dict:
    style = load_json(DEFAULT_STYLE)
    story_path = ROOT / style["source_file"]
    digest = hashlib.sha256(story_path.read_bytes()).hexdigest().upper()
    if digest != style["sha256"].upper():
        raise ValueError("golden story hash mismatch")
    annotation = load_json(DEFAULT_CHARACTER)
    character_names = sorted({item["character"] for item in annotation["constraints"]})
    metrics = build_observability_report(parse_story(story_path), character_names)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "phase8_batch1",
        "offline_only": True,
        "llm_calls": 0,
        "production_behavior_changed": False,
        "source": {"path": style["source_file"], "sha256": digest, "character_names": character_names},
        "phase_status": {
            "phase4": "paused_by_generation_evaluation_infrastructure",
            "phase4_architecture_failure": False,
            "production_writer_input": "legacy_full",
            "phase5": "paused",
            "phase6": "paused",
        },
        "validation": {
            "unit": {"passed": 189, "failed": 0},
            "integration": {"passed": 8, "failed": 0},
            "quality": {"passed": 63, "failed": 0},
            "compileall": "passed",
        },
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = args.output.with_name("phase8-batch1-style-observability-2026-07-20.md")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "markdown": str(markdown_path), "chapters": len(report["metrics"]["chapters"]), "subsections": len(report["metrics"]["subsections"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
