# P0 Test Baseline

> Date: 2026-08-09
> Branch: `feat/p0-p2-foundation`
> Baseline ancestor: `903c14a`
> Python: 3.14.4, using `E:\writer\my_writing_system\.venv`

## Outcome

Task 1 established a deterministic test process boundary. It did not make the
entire P0 suite green, and this report must not be used as a P0 Gate approval.

- `WRITER_TESTING=1` prevents `app.config` from loading `.env` from cwd.
- Production imports without `WRITER_TESTING=1` still load `.env`.
- Pytest pins Handover v1, World Runtime off, RAG shadow/reranker off, and
  Canonical Commit legacy before any `app.*` import.
- Task, character, Canonical SQLite, and Chroma paths are isolated under a
  unique per-session temporary directory.
- The global `app.dependencies.char_store` connection is closed at session
  finish so Windows can remove the temporary database directory.
- No API key or credential value is recorded in this report.

## Red -> Green evidence

### Dotenv isolation

Before the implementation:

```text
tests/unit/test_test_environment_isolation.py
1 failed, 1 passed
testing mode observed WRITER_HANDOVER_CONTRACT_VERSION=v2.3 from temporary .env
```

After the implementation and cleanup regression:

```text
tests/unit/test_test_environment_isolation.py
3 passed in 0.80s
```

Existing focused regressions:

```text
tests/unit/test_handover_contract_v2.py
tests/unit/test_handover_contract_v21.py
tests/unit/test_writer.py
24 passed, 1 warning in 1.24s
```

## Fresh unit baseline

The exact full command exits during collection:

```text
python -m pytest tests/unit -q
1 collection error, 2 warnings
```

Blocker:

```text
tests/unit/test_world_runtime_reviewer_real_task_side_by_side.py
imports the intentionally excluded sensitive module
experiments/world_runtime_writer_canary/wr310_reviewer_real_task_side_by_side.py
```

The diagnostic run that ignores only this collection blocker produced:

```text
1308 passed, 156 failed, 15 skipped, 2 warnings in 105.87s
```

Dominant failure groups:

1. Experiment tests require an LLM credential even when their asserted path
   should be replay/idempotency-only.
2. Scene Reality tests require the excluded secret-bearing
   `baseline_task_state.json` fixture.
3. World Runtime and Writer Boundary experiments depend on missing reports,
   runtime artifacts, or frozen source/provenance hashes.
4. `test_writer_public_signatures_remain_frozen` reports a public signature
   contract mismatch.

These failures remain Red inputs for Task 2 and the later secret/golden-fixture
tasks. They were not skipped, rewritten, or made green with developer secrets.

## Fresh integration baseline

The 48 local integration nodes outside `test_full_workflow.py` passed:

```text
48 passed, 2 warnings in 2.15s
```

`test_full_workflow.py::test_embedding_provider` constructs the real default
`SentenceTransformer("BAAI/bge-m3")`. The first full-suite attempt waited while
the Hugging Face cache was prepared; this was not a pytest or application
deadlock. With the now-available cache and network disabled, the node passed in
6.33s.

Final full integration verification used `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` to prohibit downloads:

```text
55 passed, 6 warnings in 12.54s
```

The warnings are not Gate failures yet, but three tests in
`test_full_workflow.py` return non-`None` values and therefore can appear to
pass without asserting their reported false result. The suite also warns about
deprecated jieba/pkg_resources, Chroma asyncio inspection, and the renamed
SentenceTransformer dimension method.

## Gate interpretation

- Task 1 environment-isolation behavior: Green.
- Local integration baseline: Green with the documented offline model-cache
  prerequisite and warning debt.
- Full unit baseline: Red (`156 failed` plus one collection blocker).
- P0 Gate: not approved.
