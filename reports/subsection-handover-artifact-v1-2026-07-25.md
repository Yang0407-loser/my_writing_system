# Subsection Handover Artifact V1

状态：`engineering_complete_real_demo_pending`

## 根因

Writer 已经在每个小节完成后调用一次既有 handover extractor，但结果只进入进程内的 `section_handover_parts`。节尾汇总会把多个小节压成一条 `handover_chain`，因此 Blackboard、checkpoint 和 TaskStore 都无法恢复原生的小节边界。历史节级汇总不能可靠反推出各小节结果。

## 修复

新增失败隔离的 `subsection_handover_history_v1` sidecar。正式记录严格位于：

`commit_subsection` 成功 → 既有 post-commit observers → `StateFrame capture_after` → `SubsectionHandoverHistoryRecorder.capture_committed`

工件随后继续执行既有节级 handover 汇总。未提交候选不会产生正式记录；sidecar 写入失败不会回滚正文、触发重试或改变任务状态。

执行状态明确区分：

- `completed_with_changes`
- `completed_no_change`
- `skipped`
- `error`

旧 `_extract_handover` 仍只返回原有 note 或 `None`。内部 observation 不改变 Prompt、调用参数、返回内容或异常 fallback。

## 持久化与兼容性

- Blackboard：`subsection_handover_history_v1`
- checkpoint：同名可选字段
- TaskStore：`analysis_json.subsection_handover_history_v1`

未新增数据库、表或列；未覆盖 `handover_chain`、`handover_notes`、`state_frame_history_v1` 或其他 analysis 字段。下一节 Writer、Review、伏笔协调、handover penetration 和前端继续消费原节级链，新工件本轮没有生产消费者。

修复前任务不迁移、不重新提取，也不把节级汇总复制成小节记录；缺少原生 history 时明确返回 `historical_subsection_handover_unavailable`。

## Synthetic 验收

- 小节：4
- commit 前正式记录：0
- commit 后正式记录：4/4
- pending：0
- 重复 record ID：0
- source/hash 追溯率：100%
- Blackboard/checkpoint/TaskStore 镜像：通过
- TaskStore 只读重启恢复：通过
- legacy checkpoint projection：不变
- 现有节级 `handover_chain`：不变
- 新增 Writer/LLM 调用：0

公开产物不包含 handover 文本、正文、Prompt/messages、数据库内容或密钥。

## 决策

工程门槛通过，只允许下一步运行一个真实四小节 Demo。真实 Demo 完成前不得宣称生产验收通过。本任务不授权 StateFrame 注入、SubsectionOutcomeBundle hook、PostWriteOrchestrator、Validator、Repair、Phase 5 或 Phase 6。

真实验收说明见 `docs/subsection-handover-real-demo-acceptance.md`。
