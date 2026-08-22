# Phase 4 Batch 3：Context Broker 连续性风险保护 shadow

> 2026-07-20 Batch 3.5 校正：本报告的机械数据仍有效，但“完整旧小节整项选择路线失败、下一候选只能是节级摘要”的架构结论证据不足。Batch 2 的同 hash 真实生成只在 Q4、Q6、Q7 观察到 B 相对 A 的净退化；Q8 是 A/B 共同越界。19/19 是 Guard 的理论保护判定，不是 19 项实测退化。当前只判定“任一信号即恢复全文”的保守 Guard 失败，选择性整项恢复尚未被否证。详见 `phase4-batch35-guard-empirical-audit.json`。

## 结论

`ContinuityRiskGuard` 成功保护了 Batch 2 暴露问题的 Q4、Q6、Q7 旧小节，也识别并保护了 Q8 的相关旧小节；但 C 配置平均输入为 11,871.6 estimated tokens，相对 legacy_full 仅下降 4.31%，远低于 20% 门槛。10/10 场景均超过 8,500 token 软预算。

按预设停止规则，C 配置的“任一风险信号即恢复完整旧小节”策略判定失败。本批不调用 Writer LLM、不做 A/C 生成验证、不切生产输入，也不开始 Phase 5。该结果不能单独关闭所有完整小节整项选择方案，也不能直接决定转向节级摘要。

## 固定实验

- A：`legacy_full`。
- B：Batch 2 冻结的 `budgeted_broker`。
- C：B 的所有选择结果加 `ContinuityRiskGuard`；Guard 只能恢复完整旧小节，不能切割、摘要、改写或删除 B 已保留内容。
- 复用 Batch 1 冻结的 10 条 query 和 legacy top-5 source ID，从共享 collection 按 ID 只读取回原文；不重新执行向量查询。
- ContextManager 继续保持“最近3小节原文＋交接笔记”，生产 Writer、Prompt、RAG、模型、角色/关系规则和四个风格旋钮均未改变。
- 所有风险决策完成后才加载人工事实证据和 Batch 2 诊断结果，运行时未使用 `must_recall_facts`、gold sections 或人工审阅结论。

## Token 对照

| 配置 | 平均 | 最小 | 最大 | 相对 A |
|---|---:|---:|---:|---:|
| A：legacy_full | 12,406.4 | 10,511 | 14,480 | — |
| B：budgeted_broker | 8,390.4 | 7,342 | 9,348 | -32.37% |
| C：risk_guarded_broker | 11,871.6 | 10,511 | 13,304 | **-4.31%** |

C 在所有 10 个场景都触发 `continuity_risk_protection_exceeds_soft_budget`。它比 B 平均增加 3,481.2 token，几乎恢复到完整 legacy 输入。

## 风险识别结果

10 个场景共有 19 个较早 recent originals。Guard 对 19/19 都判定需要保护，并全部从 B 恢复：

- `relative_time_anchor`：19 项；
- `handover_explicit_reference`：18 项；
- `unfinished_interaction_chain`：11 项；
- `durable_character_or_world_state`：8 项；
- `unique_current_event_source`：3 项。

自然叙事中的“周六、凌晨、五天前、今早”等时间锚点非常普遍。在“出现任一风险信号就保留全文、无法排除风险也保留”的契约下，Guard 无法删除任何较早小节。这证明当前 Guard 过度保护；在完成规则必要性或最小恢复集合验证前，不能把它上升为完整小节粒度的结构性失败。

问题场景均被识别：

- Q4：恢复 S16.3、S17.1；识别凌晨/周六、辞职状态及交接引用。
- Q6：恢复 S6.1、S6.2；识别五天前、住院、留着及交接引用。
- Q7：恢复 S5.3、S6.1；识别今早、住院、留着及老爷爷事件引用。
- Q8：恢复 S3.3、S4.1；识别周六、小圆面包、住院、待回答问题和交接引用。

## 必需项与追溯

以下均为 100%：

- P0/P1/P2；
- hard 角色与关系约束；
- 紧邻上一小节原文；
- 交接笔记；
- legacy RAG top-5；
- legacy 输入中已有的 4/4 人工事实证据；
- 后期场景必需项；
- source ID、text hash、注入位置和 keep/drop 原因追溯。

生产 Writer Prompt hash 为 10/10 未变化。报告为 shadow 机械结果，不包含新生成正文。

## 机械门槛

唯一失败门槛：C 相对 A 的平均 token 降幅只有 4.31%，要求至少 20%。其余保护、证据、追溯和生产不变门槛全部通过。因此：

- `all_mechanical_gates_passed=false`；
- 不生成下一次 A/C 调用方案；
- 不执行模型调用；
- 不建议 canary；
- 当前保守 Guard 停止；选择性整项恢复是否可行仍待实测。

## 下一候选假设

仅当最小选择性整项恢复仍无法兼顾质量与 20% token 降幅时，才进入“可追溯节级摘要”新假设：

- 摘要必须绑定 source ID、原文 hash、摘要版本及可回溯证据区间；
- 时间锚点、人物持久状态、未完成链和明确因果必须结构化保留；
- 先做离线事实完整性验证，未通过前不得调用 Writer 或切生产；
- 仍受同一核心假设连续三批失败即停止的规则约束。

本批到此停止，等待用户决定是否授权该新实验。
