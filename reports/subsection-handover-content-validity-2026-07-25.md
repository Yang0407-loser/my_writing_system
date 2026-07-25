# Subsection Handover V1 内容有效性验收

状态：`persistence_accepted_content_not_validated`

## 结论

持久化仍然可靠，但内容质量没有通过有限下游使用门槛。四条 Handover 在同章内没有进入下一小节 Writer messages；新增 sidecar 只是持久化镜像。因此本报告只能评价离线内容价值，不能把下一小节结果归因为 Handover。

## 消费链

- producer：`Writer._extract_handover_with_observation`
- 小节内暂存：`section_handover_parts`
- 持久化 sidecar：`subsection_handover_history_v1`
- 章节结束聚合：`handover_chain`
- 同章小节循环内更新 `prev_handover`：`false`
- 三个过渡实际注入：0/3

`section_handover_parts` 只在整章结束后聚合为 `handover_chain`，而同章小节循环内用于 Prompt 的 `prev_handover` 没有被更新。

## 工件与忠实度

- Handover records：4
- fields：24
- 原子 claims：74
- source/hash 追溯率：100.00%
- claim evidence span 追溯率：100.00%
- supported：34
- partially supported：7
- unsupported：3
- ambiguous：15
- unverifiable：15
- strict claim Precision：77.27%（34/44）
- unsupported invention：3
- stale state：1
- boundary leakage：0

三条无来源支持的结论均出现在 S1.4 的人物心理解释中；另有一条既有事件被重新列为 `new_facts`。`arc_progress=pending` 共 15 条，缺少可追溯 milestone 依据，因此单独记为 unverifiable。

## 承接覆盖

- critical：4/9，严格 Recall 44.44%
- supporting：3/7，严格 Recall 42.86%
- optional：0/1
- 未完整覆盖但可由 recent original 或当前 outline 提供：9 项

主要缺口包括节尾地点、事实确认边界、下一小节停止/切换边界，以及对“已保存但未发布”的状态区分。

## 三个真实过渡

- 正确承接：1/3
- 连续性错误：2
- Handover 冲突：0
- 可归因于 Handover 的退化：0
- 因 Handover 未注入而无法归因的错误：2

S1.2→S1.3 存在“前节已经回家写作、后节重新回到店门口”的位置与事件重置；S1.3→S1.4 又在林晚已经离开后回到同一时段和台阶场景。两项都不能归因给未被消费的 sidecar。

## 实际可用性

- directly useful：19
- redundant but correct：14
- unused optional：5
- misleading：7
- unassessable：29

重复但正确的信息不被视为无用；它仍可能作为未来结构化、低成本连续性输入。但当前工件混入解释性心理状态、无来源弧线状态和不完整边界，因此不能晋级。

## 指标边界与下一步

`handover_continuity` 当前只能标记为 `partially_assessable_native_artifact_not_quality_truth`。本任务没有修改 QualityEvaluator，也没有建立全局质量真值。

唯一下一步建议：只做一次最小 Handover extractor 契约修复——输出带 source span 的节尾状态、未完成事件和下一场景边界；禁止无证据心理推断，并在没有可追溯 milestone 来源时排除 `arc_progress`。不得自动接入其他下游消费者。

本结论来自一个真实四小节任务和 Codex 辅助审阅，不是独立人工金标准，也不能外推为通用准确率。

## 验证

- 定向 quality tests：11 passed
- 审计脚本 compileall：passed
- Writer/外部 LLM 调用：0
- 历史 Phase 3/4 测试矩阵：未运行
