# Chroma isolation benchmark — smoke profile

Date: 2026-07-17  
Chroma: 1.5.9  
Data: deterministic synthetic 8-dimensional exact-neighbor vectors  
Production strategy changed: no

| Tasks | Chunks/task | Metadata filter mean | Per-task collection mean | Top-5 parity (successful queries) | Per-task query errors |
|---:|---:|---:|---:|---:|---:|
| 1 | 100 | 1.500 ms | 1.284 ms | 100% | 0 |
| 10 | 100 | 1.228 ms | 1.182 ms | 100% | 0 |
| 100 | 100 | 3.736 ms | 7.667 ms | 100% | 2/30 |
| 1 | 1,000 | 3.616 ms | 1.418 ms | 100% | 0 |
| 10 | 1,000 | 4.967 ms | 1.631 ms | 100% | 0 |

## Decision

Keep the shared collection plus `task_id` metadata filter as the production default. The smoke data does not justify a per-task migration:

- Filtered and isolated queries returned identical top-5 IDs for every successful comparison.
- At 100 tasks × 100 chunks, per-task collections were slower on average and 2 of 30 isolated queries failed with Chroma Rust HNSW `Nothing found on disk` errors.
- Metadata filtering remained functional at every tested smoke scale.

## Temporary 5,000-chunk waiver (2026-07-18)

The complete 1/10/100 × 100/1,000/5,000 matrix is explicitly waived for the Phase 3 preflight. This is a bounded waiver, not evidence that 5,000-chunk behavior has passed:

- the current production path remains the already exercised shared collection plus `task_id` metadata filter;
- successful smoke comparisons had 100% top-5 parity, while the proposed per-task alternative was slower at 100×100 and produced 2/30 HNSW reader errors;
- running the 5,000-chunk matrix would add substantial local time and storage cost without changing the current no-migration decision.

Run the full `--profile plan` matrix before changing collection strategy, or immediately when any trigger occurs:

1. Chroma or its HNSW implementation is upgraded;
2. a production task approaches or exceeds 5,000 chunks, or active task count exceeds 100;
3. filtered-query p95 latency exceeds 50 ms or doubles relative to the recorded smoke baseline;
4. isolation checks return an empty/wrong-task result, or task cleanup leaves cross-task data;
5. a per-task collection migration is proposed for production.

Until then, the full matrix is not a Phase 3 entry gate. The production collection strategy and `task_id` filter must remain unchanged.

## Limitations

- This measures Chroma filter/collection behavior, not production embedding relevance.
- Collection-open timing in the JSON report is a warm-process metadata operation, not cold OS startup.
- Synthetic data validates isolation and latency, not long-story semantic quality.
