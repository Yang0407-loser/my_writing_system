# StateFrame V1 小节级快照持久化修复

状态：`engineering_complete_real_demo_pending`

## 根因

原有 StateFrame API 会在读取时把任务当前 Blackboard、checkpoint、人物/关系/伏笔存储和 post-write shadow 记录临时拼成 before/after。任务历史只保存最终 `analysis_json`，checkpoint 也没有小节级 StateFrame 工件。因此任务完成、Worker 重启或 Redis 丢失后，系统只能看到最终状态，无法恢复生成当时的小节边界；用这些最终值重建早期小节会产生未来信息泄漏。

## 修复后的捕获顺序

每小节在 `PromptBuilder` 组装 messages 前冻结 `StateFrameBefore`，组装后只把真实 messages hash 绑定到已经冻结的记录，不重建状态。原有正文生成、handover effects、校验和长度流程保持不变。`StateCommitter.commit_subsection` 成功后，先运行原有 Boundary 与 post-write observers，再冻结 `StateFrameAfter` 并用已冻结的 Before 计算 `StateDelta`。随后才进入原有节尾 Character State 更新和 section handover 合并。

因此，节尾来源不会回填早期小节。handover 标记为 `section_level_only`，人物/关系/伏笔即时权威存储标记为 `current_store_snapshot`，当前小节 typed post-write bundle 才标记为 `subsection_exact`。typed extraction 关闭或失败时只记 unavailable/partial，不会为 StateFrame 新增模型调用。

## 三层持久化

- 运行时：同一逻辑工件保存在任务 Blackboard 字段 `state_frame_history_v1`。
- checkpoint：Blackboard 保存检查点时镜像同名可选字段；旧 checkpoint 没有该字段仍正常恢复。验证兼容性时排除该新字段，legacy projection 保持不变。
- 最终历史：Coordinator 在现有 `analysis_json` 内合并 `state_frame_history_v1` 命名空间，不覆盖已有 analysis，不新增数据库、表或列。

正式记录 ID 由 task hash、小节位置和已提交正文 output SHA-256 确定。同一输出重放幂等；不同输出互不覆盖；未提交候选不会形成 After。

## 恢复与旧任务

只读 API 依次尝试 Blackboard、checkpoint、TaskStore `analysis_json`。Redis 工件不存在时使用 SQLite 只读连接恢复，不初始化或迁移数据库。三处均不存在时返回 `historical_state_frame_unavailable`。仍有在线旧来源时，只允许标记为 `reconstructed=true` 的 partial 临时重建，不能冒充原生历史工件。

## 四小节 fixture

- Before：4/4
- After：4/4
- Delta：4/4
- 重复正式记录：0
- TaskStore 恢复：100%
- 模拟 Redis 丢失后的 API 恢复：100%
- 已持久化事实的 source/hash 追溯率：100%
- 完整正文、完整 Prompt、messages：均未进入工件

## 三项质量指标的边界

本轮没有修改 QualityEvaluator。它现在可以读取真实冻结边界，但只有来源本身足够结构化时才能评估：

- 交接连续性：需要 Before 中的明确待承接项与 After/Delta 的可追溯结果；非结构化或缺失 handover 仍不可评估。
- 人物状态转变：需要明确人物 expectation 和小节精确状态来源；节尾汇总状态不用于早期小节。
- 伏笔健康度：需要同一 foreshadow ID 的可比较生命周期快照；最终 store 快照不能倒推小节历史。

因此，本修复只解决“历史工件会消失”，不表示三个指标已成为质量真值，也不授权 StateFrame 注入 Writer 或删除 legacy 上下文。

## 失败隔离

捕获、序列化或存储异常只写脱敏错误类型、阶段、位置、耗时和 hash。异常不会回滚正文、改变返回值、触发重试、改变 Mandatory Event、阻止 checkpoint 或任务 completed。

## 验证

定向 Blackboard、TaskStore、Writer/Coordinator pipeline、StateFrame 单元/集成/API 与原 V1 契约回归共 80 项通过，受影响模块 compileall 通过。本环境没有调用 Writer 或其他 LLM，也没有运行真实 Demo。

工程门槛通过后，下一步最多允许用户另行授权并运行一个正常四小节任务，验证真实 TaskStore 落盘与重启后恢复；不得自动开始 Writer shadow 注入。
