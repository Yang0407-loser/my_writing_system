# Phase 4R Batch R5：生成后 BoundaryValidator 离线检测基线

> 日期：2026-07-20
> 状态：已完成并停止
> 生产行为：未改变，继续 `legacy_full`

## 隔离

预测阶段只读取冻结 SceneSpec、当前写作需求、匿名生成清单和 12 份候选正文。预测先写入 gitignored runtime 并冻结 SHA-256，独立 evaluator 随后才读取盲审结果。Writer/LLM 调用均为 0，没有重新生成正文，也没有实现 Repair。

## 指标

| 能力 | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `boundary` | 3 | 0 | 0 | 9 | 100.00% | 100.00% | 100.00% |
| `required_event_q7` | 1 | 0 | 0 | 2 | 100.00% | 100.00% | 100.00% |
| `unsupported_fact_q4_exploratory` | 2 | 0 | 0 | 1 | 100.00% | 100.00% | 100.00% |

unsupported-fact 是探索性指标，不参与晋级门槛。人工 hard/event-order 的重复标签已经在候选级概念缺陷上合并，没有重复计数。

## 机械门槛

| 门槛 | 结果 |
|---|---|
| `boundary_recall_100` | 通过 |
| `boundary_precision_at_least_80` | 通过 |
| `q7_all_states_correct` | 通过 |
| `q8_all_boundary_violations_detected` | 通过 |
| `evidence_traceability_100` | 通过 |
| `production_messages_hash_unchanged` | 通过 |
| `prediction_answer_fields_unused` | 通过 |
| `writer_llm_calls_zero` | 通过 |

证据字符区间与 source/hash 追溯率为 100.00%。整体机械门槛：通过。

## 决策

本批只能建议另行授权 Validator shadow 接入；不代表已获准接入。

本批没有修改 ContextBroker、SceneSpec、Writer、Prompt、RAG 或生产调用链；不恢复旧 recent originals，不实现自动重写，不开始 Phase 5/6。四场景只能支持定向下一步，不能宣称通用生产质量。

全量回归：unit 223 passed、integration 8 passed、quality 81 passed、compileall passed。
