# Phase 4R Batch R2：StoryStateView 与 SceneSpec shadow

> 日期：2026-07-20
> 状态：完成并停止，等待 R3 另行授权
> 生产行为：未切换，继续 `legacy_full`

## 结论

R2 的机械门槛通过。项目现在有一套只读、可追溯的状态投影和确定性 SceneSpec 编译边界，但它仍是 shadow 资产，不进入 Writer messages，也没有生成质量结论。

四个冻结风险场景 Q4/Q6/Q7/Q8 的 SceneSpec 平均 273.0 estimated token，最小 225、最大 311；所有被引用字段都能回溯到 source ID、text hash 和可选字符区间，unknown/conflicted 没有被提升为 confirmed。

机器结果：[phase4r-batch-r2-scene-spec-shadow.json](phase4r-batch-r2-scene-spec-shadow.json)。

## 实现边界

- `SourceEvidence`：保存 source ID/type、hash、章节、小节和 span；报告排除正文 excerpt。
- `StateAssertion`：只允许 `confirmed`、`planned`、`unknown`、`conflicted` 四种认识状态。
- `StoryStateView`：通过现有查询接口读取当前 outline、WorldState 事实/冲突、EventGraph 弧线里程碑、规则、关系、伏笔和 handover；不消费 warning，不写任何 store。
- `SceneCompiler`：分离 confirmed、planned、open loops、hard constraints、forbidden inferences 和 unknown/conflicts；确定性输出 hash 与 token 估算。
- Writer 没有导入 `StoryStateView`、`SceneCompiler` 或 `SceneSpec`；R2 不调用 LLM。

EventGraph 继续只按 `arc_milestone` 使用，没有被描述成全量事实权威源。未验证的 WorldState 记录进入 unknown，冲突记录保持 conflicted。

## 四场景诊断

| 场景 | 机械保护重点 | SceneSpec token |
|---|---|---:|
| Q4 | 未确认的人物亲属、生死和经历不得补成事实 | 253 |
| Q6 | 人物未到店不推出店铺停业或其他人物不在 | 303 |
| Q7 | 当前星期锚点不得改写为矛盾日期；未闭合回应不自行解决 | 311 |
| Q8 | 后续计划事件不得提前写成已经发生 | 225 |

这些规则来自当前 outline/state 的认识状态，不读取 `must_recall_facts`、gold sections、human labels 或人工评估答案。它们只是 SceneSpec 的 shadow 编译结果，不是正文质量判决。

## 行为冻结

- R1 的 10 条冻结 production messages hash：10/10 不变；
- Writer generation calls：0；
- LLM calls：0；
- Redis/SQLite/Chroma 写入：0；
- 生产 Writer 对 R2 模块的导入：0；
- R1 暂留 `_legacy_generate_with_retry` oracle 已删除，避免两份生成实现漂移。

## 测试

| 测试组 | 结果 |
|---|---:|
| unit | 213 passed |
| integration | 8 passed |
| quality | 68 passed |
| compileall | passed |

新增测试覆盖不可变契约、span 校验、缺失 provenance 拒绝、unknown/conflict 保留、缺席不推出停业、确定性 hash、只读 store 调用、生产 Writer 禁止导入和四场景报告一致性。

## 限制与决策

1. R2 没有调用 Writer，因此不能证明 SceneSpec 会改善或至少不损害正文。
2. 四个冻结场景验证的是风险类型与契约，不代表覆盖所有叙事状态。
3. StoryStateView 没有复制数据库；运行时 store 不可用时只能保留 unknown，而不能补造事实。
4. ContextBroker 与生产 `legacy_full` 保持原状；Phase 4 仍为 `paused_by_generation_evaluation_infrastructure`。

R2 完成后停止。下一步只有在用户明确授权且具备合规生成环境时，才可执行 R3 的 legacy / Broker / Broker+SceneSpec 生成质量对照；不得自动开始 Phase 5 或 Phase 6。
