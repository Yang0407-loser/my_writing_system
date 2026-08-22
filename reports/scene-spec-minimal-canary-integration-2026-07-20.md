# SceneSpec 最小生产 Canary 接入

> 日期：2026-07-20
> 状态：工程接入完成，默认关闭，尚无真实 canary 样本

## 结论

Phase 4R 最终真实写作试验的 SceneSpec 语义已迁入生产模块，并以 `off/shadow/canary` 三态接入 Writer。默认配置仍为 `off`，因此当前生产继续使用原始 `legacy_full` messages。没有启用 ContextBroker、StateFrame、BoundaryValidator、Repair、Phase 5 或 Phase 6。

## 接入位置

调用顺序保持为：

`PromptBuilder -> 可选 SceneSpecCanaryController -> GenerationController -> 原有校验/长度处理 -> StateCommitter -> 默认关闭的 BoundaryValidator shadow hook`

- `off`：不调用 provider、不构建 SceneSpec、不记录、不重建 messages；
- `shadow`：只构建 hash/token/source manifest，Writer 继续消费原 messages；
- `canary`：仅当 `task_id` 位于显式白名单时，在最后一条 user message 末尾追加独立 SceneSpec 区块；
- 非法 mode：按 `off` 处理，`Settings.validate()` 返回配置警告。

Prompt 模板、system message、RAG、ContextManager、模型参数、重试、checkpoint 顺序和幂等键均未修改。canary 注入时只有最后一条 user message、对应 messages/content hash、token 账本和 source manifest 发生预期变化。

## 冻结语义

生产 `OutlineSceneSpecProvider` 只读取当前 outline 小节及连续的下一小节，表达：

- 当前小节 planned event；
- 下一小节形成的 `future_event_status` unknown/stop boundary；
- source ID、text hash 和字符区间来源。

没有引入 StateFrame、人工评估答案、历史缺陷标签、关键词扩展或新事实推断。历史最终试验 helper 已改为调用同一个生产 provider，避免双实现漂移。

最终真实试验的四个 frozen SceneSpec 保持原始 hash 和 token：

| Subsection | SHA-256 | Estimated tokens |
|---:|---|---:|
| 1 | `8f59121b834a7587b3e42eb75cc91a976d580d159ef5cce3c828c075cb46a2d8` | 230 |
| 2 | `3d144ed009b624331321c5c40daa519c4bfe4ca92c0b5626e03a07a64a0ba7d2` | 351 |
| 3 | `5150786e3fcd504e42e4bc56282cb4cc8ce15a43d29afebc2098515363249f9b` | 277 |
| 4 | `6e4b3567c391777b8f2736ce5fd5518fbfd95cc6e7f39879c06fc3927e460459` | 93 |

合法末尾小节没有下一小节，仍生成只含当前 planned event 的 SceneSpec；非末尾小节缺少连续下一小节则回退 legacy。

## 回退与记录

以下情况均返回原 PromptArtifact，不阻塞 Writer、不触发额外 retry：非白名单 task、当前 outline 缺失、非末尾下一小节缺失或不连续、目标为空、provider/注入异常、SceneSpec 超过 400 estimated tokens、source manifest 不可追溯。

结构化日志只含 task ID hash、section/subsection、mode、是否注入、SceneSpec hash/token、source IDs、fallback reason、耗时和 `production_effect`。异常字符串不会进入日志；完整正文、messages、Prompt、SceneSpec 私有文本、密钥和存储内容均不记录。

## 配置

默认：

```text
WRITER_SCENE_SPEC_MODE=off
WRITER_SCENE_SPEC_CANARY_TASK_IDS=
```

单任务 canary：

```powershell
$env:WRITER_SCENE_SPEC_MODE = "canary"
$env:WRITER_SCENE_SPEC_CANARY_TASK_IDS = "<task_id>"
```

恢复 legacy：

```powershell
$env:WRITER_SCENE_SPEC_MODE = "off"
$env:WRITER_SCENE_SPEC_CANARY_TASK_IDS = ""
```

修改环境变量后需重启 Writer/Celery 进程，使 `Settings` 重新加载。

## 验证

- SceneSpec/Writer pipeline 定向回归：59 passed；
- Writer facade、mock full workflow、R6A post-commit 隔离：9 passed；合计68项；
- 四个真实 golden hash：4/4 完全一致；
- Writer/LLM 调用：0；
- 私有正文进入 Git/报告：0；
- 真实 canary 写作样本：0。

本批只完成可回退工程接入，不授权全面生产切换。StateFrame 继续处于 `paused_by_upstream_state_contract`；下一步只能由用户显式指定一个真实 task ID 进行有限 canary。
