# Phase 4R Batch R1：Writer 职责边界提取

> 日期：2026-07-20
> 状态：完成，等待 R2 授权
> 生产行为：未切换，继续 `legacy_full`

## 目标

在不改变 Writer Prompt、模型调用参数、RAG、ContextManager、存储模型和公开接口的前提下，为单个小节写作建立可测试的软件边界。本批不实现 StoryStateView、SceneCompiler、SceneSpec 或新的验证规则。

## 修改前职责审计

`Writer.run` 同时承担：

1. 章节/小节循环、断点与动态大纲状态；
2. legacy RAG 查询、因果扩展、利用率日志与 Phase 3 shadow trace；
3. 角色、弧线、handover、WorldState、规则、伏笔、地图、物品、势力、支线和经历上下文查询；
4. mandatory events、进度、风格和 Prompt 组装；
5. LLM streaming/fallback、硬事件重试、角色违规重试和重复检测；
6. 自动续写、句尾补全和超长精简；
7. handover 提取、弧线/世界事实回写和 backref 累积；
8. 规则告警、AI 痕迹检查和异步分节审阅；
9. Chroma 入库、ContextManager 更新、token 日志、完成事件和 checkpoint。

外部依赖包括 LLMClient、Blackboard/Redis、VectorStore/Chroma、ContextManager、WorldStateManager、EventGraph、各领域 store、stream callback 和 section callback。

公开接口保持：

- `Writer.run(...) -> dict`
- `Writer.revise_subsection(original_text, instruction) -> str`

## 提取后的边界

- `PromptBuilder`：纯模板渲染，输出 messages/hash/token/source manifest；不读取运行时故事存储，不调用 LLM。
- `GenerationController`：封装现有初始生成、stream fallback、硬约束/角色/重复重试、续写、句尾补全和精简参数。
- `StateCommitter`：按现有顺序承接 handover、EventGraph/WorldState、VectorStore、ContextManager、token usage、完成事件和 checkpoint 副作用；不声称提供事务回滚。
- `SubsectionPipeline`：记录 `prepared → assembled → generated → validated → committed`，禁止越级。
- `Writer`：本批仍是兼容 facade 和章节循环所有者；Coordinator 无需感知新模块。

typed artifacts：`SubsectionInput`、`PromptArtifact`、`GenerationArtifact`、`CommitArtifact`、`SubsectionPipelineArtifact`。

## 行为冻结

离线只读重建 Phase 4 的 10 条冻结写作场景，没有调用 Writer LLM：

- content hash 不变：10/10；
- 完整 messages hash 不变：10/10；
- 新 PromptBuilder messages 与 legacy runtime 逐项相等：10/10；
- Writer 公开方法签名不变；
- ContextManager 历史 `running_summary` checkpoint 继续兼容且不恢复摘要；
- production Writer 未导入 ContextBroker、SceneSpec 或新状态源。

机器结果：`reports/phase4r-batch-r1-writer-hash-audit.json`。

## 测试

| 测试组 | 修改前 | 修改后 |
|---|---:|---:|
| unit | 192 passed | 208 passed |
| integration | 8 passed | 8 passed |
| quality | 63 passed | 65 passed |
| compileall | passed | passed |

新增覆盖 Prompt 确定性、生成参数与 legacy oracle 等价、stream fallback、mandatory retry、精简、提交顺序、提前提交拒绝、部分失败、幂等、handover/world/event/checkpoint、副作用依赖边界、Writer facade 和十场景 hash。

## 遗留风险

1. `Writer.run` 仍保留章节循环、上下文查询和部分告警/审阅逻辑；R1 只建立边界，没有完成最终瘦身。
2. 旧 `_generate_with_retry` 实现暂作为 R1 等价性测试 oracle 保留，生产路径不再使用；R2 前应删除并由冻结测试接管，避免永久双份逻辑。
3. StateCommitter 记录部分提交，但不伪装成跨 Chroma/Redis/内存的事务；现有失败语义保持不变。
4. source manifest 在 R1 只能追溯到 Writer prompt field；真实领域 source ID 需要 R2 的 StoryStateView 提供。
5. integration 测试保留既有 pytest return-value 与依赖弃用 warning，本批不顺带修复。

## 决策

R1 机械拆分通过，可以建议另行授权 R2 的只读 StoryStateView 和 shadow SceneSpec。R2 不得自动开始，也不得借机切换 ContextBroker、生产 Writer 输入、RAG 或存储模型。
