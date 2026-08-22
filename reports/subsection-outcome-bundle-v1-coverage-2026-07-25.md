# SubsectionOutcomeBundle V1 现有资产覆盖审计

状态：`adapter_ready_existing_assets_insufficient_for_shadow_hook`

本轮只读取固定真实任务的既有 TaskStore、StateFrame history 及三个权威数据库，构建只读统一视图。没有修改 Writer、Coordinator、StateCommitter、StateFrame、Prompt、调用顺序或生产存储，也没有调用 Writer/LLM。

## 真实资产

固定任务可恢复的工件为：

- 4 条已提交的小节 StateFrame history；
- 1 条节级 handover 汇总；
- 5 条任务最终人物记录；
- 0 条关系记录；
- 12 条伏笔当前快照；
- 5 条 `subsection=0` 的节级经历事件。

handover 虽在 Writer 小节循环中产生，但可恢复的正式工件是节尾汇总；Character State 在节尾更新；关系与伏笔表是读取时的当前快照；Experience 提取器接收整节正文，固定任务中的 5 条事件均没有小节边界。

## Bundle 结果

4/4 小节均生成了确定性 Bundle，重复 Bundle ID 为 0。20 个 component 实例中：

- available：0；
- partial：4；
- unavailable：16；
- conflicted：0；
- error：0；
- subsection-exact：0。

五类 component 的完整覆盖率和 subsection-exact 覆盖率均为 0%。

只有最后一个小节暴露粗粒度资产：

- handover：`partial + section_aggregate`；
- character state：`partial + task_final_snapshot`；
- foreshadow：`partial + current_store_snapshot`；
- experience：`partial + section_aggregate`。

前三个小节不复制这些节级或最终快照。关系记录在该任务中完全 unavailable。

## 时序保护

Adapter 明确阻止了四类容易出现的误分类：

1. 节级 handover 没有复制给每个小节；
2. 任务最终人物状态没有倒推早期小节；
3. 伏笔当前快照没有冒充 planted/advanced/resolved delta；
4. `subsection=0` 的经历事件没有冒充小节精确变化。

所有 available/partial 声明均带有 source ID/hash，23/23 source refs 可追溯，追溯率 100%。其中 52.17% 是 current-store snapshot，47.83% 是 section aggregate 或 task-final snapshot；unknown granularity 为 0。所有来源均可由 TaskStore 或权威数据库在 Worker 重启后恢复。

## 对 StateFrame 和质量评估的意义

当前没有任何 component 可以作为 StateFrame After 的可靠 subsection-exact 新来源：

- handover 与 experience 只能作为节级辅助信息；
- character state 与 foreshadow 只能证明任务最终状态或当前快照；
- relationship 在本任务中不可用。

因此：

- handover continuity 仍不可评估；
- character state transition 仍只属于 partial；
- foreshadow health 仍不可评估。

`unavailable` 不计为 Writer 质量失败，本报告也不宣称这些指标已经成为质量真值。

## 工程与隐私

Writer、Coordinator、StateCommitter、StateFrame persistence 和原 post-write extractor 的基线文件 hash 全部保持不变。四个数据库文件在审计前后 hash 不变；Blackboard、checkpoint、TaskStore 和数据库写入均为 0。公开报告只保留状态、计数、source ID/hash 和模块信息，不含正文、Prompt/messages、人工答案或数据库内容。

## 结论

只读 Adapter 的机械门槛全部通过，但现有真实资产不足以支撑生产 shadow hook：加入 hook 只会持久化 unavailable 或粗粒度快照，没有新增小节级事实价值。

本轮不建议实现 shadow hook，不开始 PostWriteOrchestrator，也不把 Bundle 注入 StateFrame 或 Writer。若继续拆分 Writer，应先选择一类现有结果补齐小节边界；不能同时重构五个提取器。

定向 unit、integration、quality 测试共 19 项通过，失败 0；受影响模块 `compileall` 通过。没有运行历史 Phase 3/4 大矩阵。
