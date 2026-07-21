# Foreshadowing resolve_chapter normalization

Date: 2026-07-21
Task: `3454d80e-02b9-48b6-b160-da614768da9b`

## Root cause

The table declares `resolve_chapter` as `INTEGER`, but SQLite affinity does not reject a non-numeric string. The handover archive path wrote `to_section` without normalization, and `get_foreshadowing_summary()` later compared the raw string with an integer. That produced `TypeError: '<=' not supported between instances of 'int' and 'str'`.

The task itself completed and saved its draft. Only the foreshadowing-health subsection of review failed; it did not trigger a Writer retry and is unrelated to Character Arc V2.

## Fix

`normalize_resolve_chapter()` is now the single boundary rule for reads and writes:

- positive integers remain integers;
- ASCII digit strings become positive integers;
- null and blank strings become null;
- booleans, floats, zero, negatives, chapter names and other strings become null.

Create and update persist only a positive integer or null. Invalid writes emit a warning containing only the operation and input type. Reads normalize without rewriting the database. Health summaries skip invalid legacy values in overdue/upcoming comparisons and expose `invalid_resolve_chapter_count`.

No migration, bulk rewrite, deletion, Chroma change, prompt change or LLM repair was performed.
