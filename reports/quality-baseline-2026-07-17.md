# Context consistency baseline — 2026-07-17

This report is deterministic and offline; it does not call an LLM.

## Baseline metrics

| Metric | Value |
|---|---:|
| RAG Precision@5 | 68.0% |
| RAG Recall@5 | 66.7% |
| Late-chapter Precision@5 | 40.0% |
| Observed character hard-constraint violation rate | 10.5% |
| Human-reviewed hard-rule violation rate | 10.5% |
| Human character-label coverage | 100.0% |
| Style range violation rate | 0.0% |
| Style normalized range deviation | 0.0000 |
| Per-subsection context-token coverage | 0.0% |

## Existing tests

- Unit: 127 passed, 0 failed, 1 warning.
- Integration: 7 passed, 0 failed, 3 warnings.

## Golden story

- Source: `output/周六面包店与凌晨三点半_20260715_143857.md`
- SHA-256: `0B1E3153D81E1CE1A1BAA8D23BDB6A8629BABD29EF29CA381D4922EBB7B42F96`
- Fixed style samples: 6
- Full-story objective stats: `{"short_sentence_ratio": 0.531, "medium_sentence_ratio": 0.3618, "long_sentence_ratio": 0.1073, "dialogue_ratio": 0.1207, "paragraph_length_avg": 53.32, "paragraph_length_median": 36, "sensory_terms_per_1k": 27.11, "characters": 68580, "sentences": 3776, "paragraphs": 1239}`

## Known gaps

- Redis was unavailable, so historical per-subsection input/output/context tokens and rewrite counts could not be recovered.
- All 19 hard character rules were human-reviewed: 17 satisfied and 2 current-draft violations.
- The qualitative style issues require dedicated metrics and human sampling; this baseline does not attribute them to the four-control style contract.
- The RAG set contains 10 queries; metrics are descriptive and not statistically significant.
