# Foreshadowing resolve_chapter normalization

Date: 2026-07-21
Latest incident: `3530d835-6b1e-4b46-94cc-94fc856c5cb6`

## Corrected root cause

The first normalization repair, committed earlier on 2026-07-21, correctly normalized persisted `resolve_chapter` values on reads and writes. The latest traceback nevertheless reported `int <= str` at the health comparison. At that expression the left operand was the normalized integer `resolve_chapter`; the right operand was `current_chapter`, derived from string keys in `section_texts`.

The earlier report therefore described an incomplete root cause. It was not an unimplemented fix: write/read normalization existed, but the comparison boundary still accepted a string current chapter. Two other query paths also delegated mixed-type `resolve_chapter` comparisons to SQLite.

The task itself completed and saved both draft and checkpoint. Only the review foreshadowing-health summary failed. It did not trigger a Writer retry and is unrelated to Character State propagation, Character Arc, SceneSpec, or prose generation.

## Normalization contract

`normalize_resolve_chapter()` is the single deterministic chapter-number rule:

- positive integers remain integers;
- ASCII digit strings become positive integers;
- null and blank strings become null;
- booleans, floats, zero, negatives, chapter names, and other strings become null.

Create and update persist only a positive integer or null. Invalid writes emit a redacted warning containing the operation and input type, not the value.

All comparison paths now normalize both the persisted resolve value and the current chapter before comparison. `get_active_for_chapter()` and `get_unresolved_foreshadowings()` no longer compare raw mixed values in SQL. Invalid historical values are excluded from due/upcoming decisions, counted by the summary where applicable, and never rewritten by reads.

## Scope and verification

- No schema change, migration, bulk rewrite, deletion, Chroma change, prompt change, or LLM repair was performed.
- No Writer or LLM call was made.
- Targeted unit and quality tests cover integers, numeric strings, null/blank, bool, float, zero, negatives, non-numeric text, string current chapters, SQL-path filtering, warning redaction, and read-only behavior.
- This is a deterministic store-boundary fix; no new writing Demo is required. A running Celery worker must be restarted before subsequent tasks can load the new code.
