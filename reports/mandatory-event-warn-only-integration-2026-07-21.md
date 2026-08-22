# Mandatory Event 自动重写降级接入

> 日期：2026-07-21
> 状态：工程接入完成，默认 `warn`，尚无降级后的真实写作样本

## 结论

Mandatory Event 字面检测不再默认拥有自动整节重写权限。默认模式由原先的隐式强制重试改为 `warn`：正常小节仍只进行一次初始 Writer 正文调用，检测只针对经过角色约束和重复检查后的最终候选，记录脱敏结构化观察，不追加“强制重写”消息，也不改变生成参数或返回正文。

本轮冻结了检测语义：mandatory 来源、`_extract_lock_keywords`、最多 5 个关键词、50% 字面阈值及现有 set 无序行为均未修改。角色弧预编排、EventGraph、角色弧 post-check、SceneSpec、Writer Prompt、RAG、ContextManager 和风格系统也未修改。

## 模式

- `off`：不执行 mandatory 检测，不记录 observation，不触发重写。
- `warn`：对最终候选执行一次检测并记录哈希、计数和模式信息；检测异常 fail-open；mandatory 导致的额外 Writer 调用恒为 0。
- `retry`：仅完整、规范 UUID 与 `WRITER_MANDATORY_EVENT_RETRY_TASK_IDS` 精确匹配时，保留旧版最多 2 次重写；空白名单、缺失 task ID、前缀、部分 UUID 和通配符均降级为 `warn`。

默认配置为：

```text
WRITER_MANDATORY_EVENT_MODE=warn
WRITER_MANDATORY_EVENT_RETRY_TASK_IDS=
```

非法 mode 自动按 `warn` 处理并记录一次配置警告，绝不回退到 `retry`。旧 `.env` 不需要新增字段，旧 checkpoint 和 `GenerationArtifact` 不需要迁移。

## 调用顺序

默认路径现在为：

`PromptBuilder -> 可选 SceneSpec 注入 -> 初次 Writer 生成 -> 角色约束检查 -> 重复检查 -> 最终候选 mandatory observation -> 长度处理 -> StateCommitter`

白名单 `retry` 路径保留原先的位置与参数：初次生成后最多两次 mandatory 重写，再进入角色与重复检查。角色弧 post-check 是终稿后的另一条告警链，不进入 mandatory observation，也不会被计为 mandatory 重试。

对没有其他重写原因的小节，默认 Writer 正文调用由最坏 3 次降为固定 1 次，mandatory 额外调用为 0。若角色约束或重复检查触发原有重写，只计入对应原因；mandatory 仍不会增加调用。

## 可观测性

每条 `warn` observation 记录 task ID hash、section/subsection、请求与生效模式、最终输出 SHA-256、约束 hash、事件和关键词 hash、命中数、阈值、`would_have_retried`、实际 mandatory 重试次数、耗时、检测器版本和 `production_effect=false`。

记录明确排除完整正文、mandatory 事件原文、关键词原文、Writer messages、Prompt、SceneSpec 文本、API key、数据库和 Chroma 内容。检测异常只记录短错误类型，并继续返回原正文。

## 验证边界

本轮没有调用 Writer 或其他 LLM，没有重新生成正文，也没有修改生产 checkpoint、角色弧或 SceneSpec。定向测试覆盖默认与非法模式、UUID 精确白名单、warn/off/retry 调用次数、最终候选绑定、异常 fail-open、输出和提交 hash、生成参数、私有数据排除及 SceneSpec 必要回归。

本轮定向测试结果为 `36 passed, 0 failed`，修改模块 `compileall` 通过。

目前真实 `warn` 样本为 0，因此这里只能确认执行权限与工程不变量，不能评价正文质量。下一次只需正常运行 Demo，无需再次构造大规模 A/B；观察 `would_have_retried` 数量、实际 mandatory retry 必须为 0，以及正文是否仍完成 outline 目标。
