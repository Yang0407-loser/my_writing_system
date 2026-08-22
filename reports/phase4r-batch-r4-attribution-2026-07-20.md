# Phase 4R Batch R4：SceneSpec 失败归因与 Writer 职责边界审计

> 日期：2026-07-20
> 状态：已完成并停止；生产继续 `legacy_full`
> 模式：只读 R3 资产，Writer/LLM 调用均为 0

## 结论

现有证据不能证明 token 减少直接造成 Q4 或 Q8 退化。Q4 的相关性值得继续观察，但 C 臂已经收到“不得补写未知亲属”的明确 SceneSpec，仍然补造家庭事实；Q8 的 A/B/C 三臂全部越过小节边界，因此删减上下文不是必要原因。

SceneSpec 的主要问题是执行不稳定，而不是整体缺少约束：Q7 中 C 是唯一在当前小节同时完成“删除记录”和“直接面对周野”的候选；Q4 和 Q8 中，C 又分别违反明确的未知亲属限制和截止边界。约 9k token 的 budgeted 上下文仍值得作为 shadow 候选保留，但不具备生产晋级条件。

## 输入规模

| 配置 | 平均 estimated token | 相对 legacy |
|---|---:|---:|
| `legacy_full` | 12428.75 | 基线 |
| `budgeted_broker` | 8591.25 | -30.88% |
| `broker_scene_spec` | 8880.50 | -28.55% |

盲审产生 22 条分类标签；合并同一候选中对同一问题的重复分类后为 15 个概念缺陷。以下数字按概念缺陷计数，避免把同一越界同时记作 hard 与 event-order 后重复放大。

| 归因 | 概念缺陷数 |
|---|---:|
| `missing_scene_spec_fact` | 1 |
| `unrelated_generation_variance` | 8 |
| `writer_instruction_noncompliance` | 4 |
| `writing_request_boundary_ambiguity` | 2 |

## 关键归因

- Q4：B 补造的老刘亲属、请柬时间和人数矛盾，没有被已删除小节确定性约束；C 的周野家庭补写则直接违反 SceneSpec。旧小节包含部分周野家庭史，但不能据此把所有缺陷归因为删除依赖。
- Q7：A、B 都没有在当前小节完成完整动作；C 的 SceneSpec 明确写出当前时间、删除和直面要求，并成功完成，证明结构化职责拆分有局部收益。
- Q8：A/B 的基础写作请求只陈述当前目标，没有明确的负向停止契约，归为边界表达含糊；C 已明确写出截止点仍继续推进，归为 Writer 不服从明确指令。
- Q8 的“三周照片却列出四周”来自输入参考本身的局部不一致；C 的 SceneSpec 没有解析这个事实，记为缺少局部事实，不是 Broker 删除损失。

## 职责边界

- Context Broker：事实回忆与连续状态恢复，提供最小、可追溯的事实包。
- Scene Planner：场景计划与事件顺序，把本节目标和停止边界结构化。
- Writer：集中负责 prose 和风格，只消费事实与计划，不承担事实检索或自检。
- Validator：负责小节边界、未支持事实、角色关系及事件顺序检查。
- Repair：只在 Validator 的检测可靠性得到验证后，对局部缺陷修复；不得与 Validator 首批实验同时引入。

## 下一步

下一批应只测试生成后 `boundary_validator`，使用现有 12 份输出先测检测能力，不重新生成、不恢复旧小节、不修改 SceneSpec。只有检测精度足以区分 Q7 的合格推进和 Q8 的越界后，才另行授权局部 Repair。

本批不切生产，不启动 Phase 5/6，也不以 4 个样本宣称全面质量结论。机器报告不包含候选正文或 Writer messages；私有正文仍仅存在 gitignored runtime。

全量回归：unit 218 passed、integration 8 passed、quality 76 passed、compileall passed。
