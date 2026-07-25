# Subsection Handover Artifact V1

状态：`real_demo_accepted`

## 根因与修复

Writer 原本会在每个小节完成后调用既有 handover extractor，但结果只进入进程内的 `section_handover_parts`。节尾汇总会把多个小节压成一条 `handover_chain`，Blackboard、checkpoint 和 TaskStore 因而无法恢复原生的小节边界。

修复新增失败隔离的 `subsection_handover_history_v1` sidecar。正式记录顺序为：

`commit_subsection` 成功 → 既有 post-commit observers → `StateFrame capture_after` → `SubsectionHandoverHistoryRecorder.capture_committed`

未提交候选不会产生正式记录；sidecar 写入失败不会回滚正文、触发 Writer 重试或改变任务状态。现有 handover Prompt、返回 note、节级汇总和生产消费者均保持不变。

## 持久化与兼容

- Blackboard：`subsection_handover_history_v1`
- checkpoint：同名可选字段
- TaskStore：`analysis_json.subsection_handover_history_v1`
- 未增加数据库、表或列
- 历史任务不迁移，也不从节级汇总反推小节记录
- 现有 `handover_chain` 继续作为生产消费者输入
- 新工件当前没有 Writer 输入消费者，`production_effect=false`

## Synthetic 验收

- commit 前正式记录：0
- commit 后正式记录：4/4
- pending：0
- 重复 record ID：0
- source/hash 追溯率：100%
- Blackboard、checkpoint、TaskStore 镜像：通过
- TaskStore 只读恢复：通过
- legacy checkpoint projection：不变
- 新增 Writer/LLM 调用：0

## 真实四小节验收

真实任务仅在公开报告中保存脱敏 task hash：

`b598440c9244433ac755e84a1a9f99ed352b7ff3062f09ffbe77acbe72e98870`

结果：

- 任务状态：`completed`
- 实际完成小节：4
- Before/After 主流程没有被 Handover sidecar 改写
- Handover records：4/4
- `completed_with_changes`：4
- pending：0
- errors：0
- 重复正式记录：0
- Blackboard/checkpoint/TaskStore 三层内容完全一致
- 每条 Handover `output_sha256` 与对应 StateFrame 输出 hash 一致
- commit 幂等键仍为 `{task_id}:{section}:{subsection}`
- TaskStore 只读回退：4/4，`reconstructed=false`
- 原有节级 `handover_chain` 保留
- 新增 Writer/LLM 调用：0

任务完成后已重启 Celery Worker，且未重新提交写作任务。重启后再次只读查询，4 条 record ID、正文 hash、Prompt messages hash、handover note hash 和提交幂等键全部保持不变；没有产生重复记录。

## 隐私

公开产物不包含 Handover 字段值、完整正文、Prompt/messages、数据库内容、Chroma 内容、附件日志或密钥，只保存数量、状态和脱敏 hash。

## 决策

Subsection Handover Artifact V1 的小节级持久化与重启恢复正式验收通过。

该结论只证明工件可以按真实小节边界可靠保存和恢复，不证明 Handover 内容本身已经成为质量真值，也不授权 StateFrame/OutcomeBundle 注入、BoundaryValidator、Repair、Phase 5 或 Phase 6。
