# Character State Update 状态传播断链修复

日期：2026-07-21
状态：`engineering_fix_complete_real_demo_pending`

## 原断点

`CharacterManager.update_states` 的结果只替换 Writer 局部变量并写入 Blackboard。Writer 返回结果和自动节级 checkpoint 都没有携带 `character_arcs`；Coordinator 随后继续使用调用前的旧引用，交互 checkpoint 和 final Reviewer 因而可能读取旧状态。

## 修复路径

当前顺序为：小节正文提交完成 → 节末人物状态更新 → 正文与 `character_arcs` 写入同一节级 checkpoint payload → Writer 返回最终状态 → Coordinator 校验并采用 → 后续 phase checkpoint → final Reviewer。

- CharacterManager 对一个调用实行全量接受：缺少任一预期人物、重复 ID、空状态或解析失败时，整批保留旧状态。
- Writer 深拷贝输入，更新异常时 fail-open 到上一份有效状态。
- 自动和交互 checkpoint 均携带对应状态；交互回调不再捕获旧的 Coordinator 状态。
- Coordinator 对旧 Writer 返回、非法结构安全回退，不从 Blackboard 反向拼装持久状态。
- final Reviewer 继续只读取 Coordinator state。

Blackboard 仍负责运行时共享和观测，但不再是人物更新的唯一传播来源。没有新建数据库、状态模型或第二套状态仓库。

## Checkpoint 与恢复

未修改 checkpoint version 和幂等键。旧 checkpoint 不含传播元数据时继续按原有 `character_arcs` 恢复；旧 Writer 返回不含新字段时回退 Coordinator 输入状态。新 checkpoint 将整节已提交正文与对应人物状态放在同一 payload 中。

底层向量库、ContextManager 和 Redis 仍沿用现有“有序副作用、无伪回滚”契约，本修复没有宣称跨存储事务能力。

## 可观测性

人物状态使用规范 JSON 的 SHA-256。记录仅包含 task ID hash、阶段位置、状态 hash、来源、fallback、checkpoint version 和 `production_effect=character_state_propagation_only`，不包含完整状态、正文、Prompt 或 messages。

定向测试满足：

`Writer updated state hash = Coordinator state hash = checkpoint state hash = final Reviewer state hash`

## 验证

- 定向 unit/integration：52 passed；受影响模块 compileall 通过。
- Writer/LLM 新增调用：0。
- Prompt、messages、生成参数、Mandatory Event、SceneSpec、Condense 和 Review 语义均未修改。
- 修改后的真实任务尚未运行。

下一步最多运行一个正常真实任务，只验证 hash 贯通、checkpoint 恢复和 final Reviewer 输入；不得借此启动其他优化。
