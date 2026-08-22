# StateFrame V1 真实四小节持久化验收

状态：`accepted`

固定任务 `6e52740c-c959-4c84-8651-b46ceebfd88b` 已完成，实际完成 4 个小节，正文与 checkpoint 正常保存。运行中没有 Writer 重试、提交失败、Worker 异常或 StateFrame 捕获错误。本次验收没有重新生成正文，也没有增加 Writer/LLM 调用。

## 工件结果

- Before：4/4
- After：4/4
- Delta：4/4
- `pending_before`：0
- 捕获错误：0
- 重复正式 `record_id`：0
- `production_effect=false`：4/4

4 条记录均为 `partial`，这不表示持久化失败。原因是捕获时缺少结构化 handover、关系状态、历史 current-store snapshot 和小节精确 post-write state bundle；已存在的 StateFact 仍全部带有 `source_id` 与 `source_hash`。

## 捕获顺序

静态调用链和真实工件共同确认：

1. Before 在 `PromptBuilder` 与 Writer 调用前冻结；
2. Before 不含当前小节输出；
3. 正文先由 `StateCommitter.commit_subsection` 成功提交；
4. 原有 post-commit observers 随后运行；
5. After 再冻结并与原 Before 计算 Delta；
6. 节级 handover 汇总与 Character State 更新发生在 After 之后。

因此节尾聚合状态没有回填到前面小节，未提交候选也不会产生正式 After。正文提交幂等键仍为 `{task_id}:{section}:{subsection}`。

## 三层一致性

Blackboard、checkpoint 与 TaskStore `analysis_json.state_frame_history_v1` 均包含同一组 4 条记录。三层的 `record_id`、Before/After hash 和 Delta ID 完全一致。checkpoint 原有 `draft`、`handover_chain`、`character_arcs`、`section_texts` 字段保持存在；TaskStore 沿用既有 `analysis_json` 命名空间，没有增加数据库表或列。

## Worker 重启与 Redis 回退

writing Worker 已按 `writing,celery` 队列正常重启，未重新执行任务，也未产生新模型调用。重启后 4 条记录的 `record_id`、Before/After hash 与 Delta ID 全部稳定，重复记录数仍为 0。

随后在全新进程中以隔离的空 Blackboard 模拟 Redis 工件不可用。4/4 小节均从 TaskStore 恢复，API 返回 `source=task_history`、`reconstructed=false`，所有 ID/hash 与原始工件一致；没有删除 Redis 数据，也没有创建或修改数据库。

## 可追溯与隐私

共核对 96 个已持久化 StateFact，96/96 具有 `source_id` 和 `source_hash`，追溯率 100%。公开工件不含完整正文、Prompt、messages、API key、`.env` 内容、数据库或 Chroma 内容；本报告没有保存正文证据片段。

## 三项质量指标当前边界

- handover continuity：当前没有结构化、可追溯的小节级待承接项，仍为 `unassessable`。
- character state transition：存在 2 个预期，但缺少小节精确 post-write 状态来源，0 个可正式评估，因此只算 `partial`。
- foreshadow health：当前 store snapshot 没有小节生命周期历史，仍为 `unassessable`。

`unavailable` 不计为 Writer 质量失败，也没有用最终人物或伏笔状态回填早期小节。本次验收只证明历史边界可靠落盘与恢复，不宣称三项指标已成为质量真值。

## 验证

StateFrame 持久化单元测试、API 回退集成测试及报告一致性测试共 16 项通过，失败 0；相关模块 `compileall` 通过。没有重复运行历史 Phase 3/4 大矩阵。

## 结论

真实四小节持久化的全部机械门槛通过，StateFrame V1 小节级持久化正式验收通过。StateFrame 仍未注入 Writer，本次结论不授权自动开始 Writer shadow 注入或其他优化。
