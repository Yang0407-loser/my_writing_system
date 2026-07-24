# Phase 4R：Writer 职责拆分总报告

> 日期：2026-07-20
> 当前状态：SceneSpec → BoundaryValidator typed 工件贯通完成，等待唯一一次自然真实 shadow
> 生产行为：继续 `legacy_full`

## 目标

Phase 4R 不把 token 下降本身当成成功，而是逐步把事实恢复、场景计划、边界控制和自检从 Writer 的单体职责中拆出，让 Writer 更集中地负责 prose 与风格。

目标职责链为：

`Context Broker → Scene Planner → Writer → Validator → Repair`

其中 Repair 尚未实现或授权。

## 批次结果

| 批次 | 结果 | 决策 |
|---|---|---|
| R1 | 提取 PromptBuilder、GenerationController、StateCommitter 和 SubsectionPipeline；10/10 冻结 Writer hash 不变 | 软件职责边界成立，生产行为不变 |
| R2 | 建立只读 StoryStateView 和可追溯 SceneSpec；4 场景平均 273 estimated token | 只证明结构契约，不证明生成质量 |
| R3 | 完成 12 次 A/B/C 生成和独立盲审；SceneSpec 臂相对 legacy 输入下降 28.55%，但盲审只获 1/4 首选 | 不进入 canary，继续 `legacy_full` |
| R4 | 15 个概念缺陷中，Writer 明确指令不服从 4、写作边界含糊 2、SceneSpec 局部事实缺失 1、其他生成波动 8；删除上下文依赖 0 | 保留约 9k token shadow 候选，优先验证生成后边界检测 |
| R5 | 冻结预测后独立评估：boundary 3/3、Q7 required-event 3/3 分类正确、Q4 unsupported-fact 2/2；均无 FP/FN，追溯率 100% | 机械门槛通过，只允许建议另行授权 Validator shadow 接入 |
| R6A | 将 R5 v1 规则提取到生产模块，冻结预测 hash 完全不变；新增默认关闭、post-commit、失败隔离的 shadow runner | 接入结构完成但真实样本为 0，只允许另行授权 R6B |
| 工件贯通 | 将生成前的同一 typed SceneSpec 作为小节局部工件传给提交后的 Validator；不二次编译，保留兼容 provider | 原 `scene_spec_provider_unavailable` 断点已修复；默认仍关闭，真实样本为 0，只允许一个自然任务 |

## 当前认识

1. 减少 Writer 输入约 30% 是可行的，但四场景不足以证明全面质量不下降。
2. Q7 说明短小 SceneSpec 能帮助 Writer 完成本节动作；Q4/Q8 说明 Writer 不会稳定服从已经明确的事实和截止约束。
3. Q8 的三组结果都越界，token 减少不是该问题的必要原因。
4. R5 证明冻结契约上的确定性后置检测在当前 12 个样本中可行；R6A 只证明默认关闭接入不会影响 Writer 或提交。规则仍具有场景特化性，且尚无真实 shadow 样本，不能外推成通用语义 Validator。
5. Writer 继续承担 prose 与风格；事实恢复归 Context Broker，场景计划归 Scene Planner，边界和自检归 Validator。Repair 只能在 Validator 扩样本验证后单独实验。

## 当前停止点

- 生产继续 `legacy_full`。
- ContextBroker 和 SceneSpec 未接入生产输入；BoundaryValidator 仅以默认关闭、NoOp sink 的 post-commit shadow hook 存在。
- 不恢复旧 `running_summary`，不恢复旧 50 维风格字段。
- 不开始 Repair、Phase 5 或 Phase 6。
- 下一步只允许一个自然真实写作任务：SceneSpec 使用 shadow、BoundaryValidator 开启 shadow；结果只作非阻断观察，不实现 Repair、阻断或自动重写。

全量回归：unit 229 passed、integration 9 passed、quality 85 passed、compileall passed。
