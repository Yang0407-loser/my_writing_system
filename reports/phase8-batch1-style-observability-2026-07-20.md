# Phase 8 Batch 1: deterministic style observability baseline

Status: **baseline complete; no Writer or Prompt behavior changed**

This report analyses the frozen 18-section golden story offline. It makes deterministic observations, not automatic literary-quality verdicts. No LLM was called and no prose was rewritten.

## Corpus baseline

| Metric | Value |
|---|---:|
| Chapters / subsections | 18 / 52 |
| Visible characters | 58963 |
| Dialogue ratio | 12.30% |
| Sentence length mean / median / p90 | 16.11 / 14.0 / 29.0 |
| Paragraph length mean / median / p90 | 49.34 / 36 / 109.0 |
| Mechanical time/ordinal/numeric starts | 7.79% |
| Character-name starts | 496 |
| Sensory terms per 1k chars | 22.54 |
| Psychological exposition terms per 1k chars | 2.48 |
| Consecutive short-sentence runs | 191 |
| Consecutive structural-signature runs | 73 |
| Exact repeated sentence groups | 124 |
| Exact repeated paragraph groups | 5 |

## Chapter observations

| Section | Title | Dialogue | Sentence p50 | Paragraph p50 | Mechanical starts | Sensory /1k | Psychological /1k | Short/structural runs | Distribution anomalies |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 第一卷 | 2.4% | 14 | 38 | 6.9% | 33.11 | 0.0 | 8/4 | none |
| 2 | 第1章：客户说「感觉不对」的那天 | 1.2% | 14.0 | 62.0 | 11.4% | 25.76 | 2.68 | 10/6 | none |
| 3 | 第2章：凌晨三点半的陌生人 | 21.5% | 13.0 | 31 | 10.7% | 30.11 | 2.3 | 25/3 | none |
| 4 | 第3章：三个问题 | 9.8% | 14.0 | 28.0 | 8.1% | 25.02 | 2.47 | 9/4 | none |
| 5 | 第4章：菠萝包与老爷爷 | 19.1% | 14 | 45 | 7.0% | 15.31 | 2.04 | 7/1 | none |
| 6 | 第5章：意外的流量 | 8.1% | 17 | 41.5 | 7.1% | 14.81 | 2.12 | 6/4 | none |
| 7 | 第6章：删帖与邀请 | 16.5% | 12.0 | 23 | 10.8% | 18.36 | 4.3 | 14/3 | none |
| 8 | 第7章：面粉与模具 | 13.6% | 12 | 28.5 | 1.0% | 37.49 | 3.41 | 18/9 | sensory_terms_per_1k |
| 9 | 第8章：无名童谣 | 7.6% | 13 | 43 | 7.5% | 13.31 | 2.39 | 13/5 | none |
| 10 | 第9章：失恋的可颂 | 8.5% | 14.0 | 26 | 15.7% | 18.19 | 2.98 | 16/6 | mechanical_start_ratio |
| 11 | 第10章：父亲来了 | 4.5% | 14 | 45 | 4.0% | 15.42 | 2.57 | 5/2 | none |
| 12 | 第11章：那篇深夜文章 | 0.1% | 15.0 | 77 | 6.0% | 23.43 | 3.53 | 7/3 | none |
| 13 | 第12章：一袋吐司 | 6.6% | 14 | 31 | 9.1% | 20.25 | 5.88 | 8/5 | none |
| 14 | 第13章：流量的背面 | 24.9% | 13.0 | 40.5 | 5.7% | 18.39 | 0.84 | 8/4 | none |
| 15 | 第14章：社区生活墙 | 9.8% | 14 | 36 | 3.3% | 26.44 | 4.74 | 6/10 | none |
| 16 | 第15章：面包边角料的戒指 | 15.8% | 14.0 | 40.5 | 9.1% | 25.78 | 1.11 | 13/2 | none |
| 17 | 第16章：按时吃饭 | 22.0% | 15.0 | 37.0 | 8.0% | 19.17 | 1.21 | 14/1 | none |
| 18 | 最终章：一个普通周六 | 9.9% | 18.0 | 56 | 3.8% | 25.55 | 1.37 | 3/1 | none |

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
| `mechanical_counting` | `mechanical_start_ratio`, `sentence_starts` | `human_required` |
| `repetitive_sentence_patterns` | `exact_sentence_groups`, `exact_paragraph_groups`, `consecutive_isomorphic_sentence_runs` | `human_required_for_quality_impact` |
| `insufficient_emotional_layering` | none | `human_or_llm_required` |

`insufficient_emotional_layering` is deliberately not assigned a keyword heuristic or automatic score. It remains an explicit human/LLM review item. Mechanical counting and repetitive patterns have deterministic locators, but whether an occurrence is deliberate rhythm or harmful repetition still requires reading.

## Phase status

- Phase 4: `paused_by_generation_evaluation_infrastructure`; this is not an architecture-failure verdict.
- Production: unchanged `legacy_full` Writer input.
- ContextBroker, ContinuityRiskGuard and all Phase 4 experiment records remain preserved.
- Phase 5 and Phase 6 remain paused. Phase 8 Batch 1 changes no production path.

## Verification

- Unit: 189 passed, 0 failed.
- Integration: 8 passed, 0 failed.
- Quality: 63 passed, 0 failed.
- `compileall`: passed.
