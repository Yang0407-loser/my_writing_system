# Phase 4R Batch R3：生成实验准备

> 日期：2026-07-20
> 状态：执行包已准备，尚未生成
> 生产行为：未切换，继续 `legacy_full`

## 结果

R3 的 A/B/C 可移交执行包已经完成，覆盖 Q4/Q6/Q7/Q8：

- A：`legacy_full`
- B：`budgeted_broker`
- C：`budgeted_broker + SceneSpec`

计划调用 12 次，当前 Writer/LLM 调用均为 0。四场景的冻结 A/B messages hash 与已完成 Batch 2 逐项一致，生产 messages 未改变。C 只在 B 的最后一个 user message 后追加确定性 SceneSpec；system prompt、RAG、规则、风格与生成参数不变。

## Token 预检

| 配置 | 4场景输入 token 合计 |
|---|---:|
| legacy_full | 49,715 |
| budgeted_broker | 34,365 |
| broker_scene_spec | 35,522 |

C 相对 A 预计下降 28.55%。最大输出预算为 96,000 tokens（12 × 8,000），这是上限而非预计实际输出。当前客户端不支持固定 seed，已在 manifest 中明确记录；匿名顺序使用固定本地随机种子。

## 隔离与安全

- 私有 messages、匿名映射和未来正文只写入 `.phase4r_r3_runtime/`；该目录已加入 `.gitignore`。
- 公开 manifest 只包含 messages hash、token、ContextItem trace 和 SceneSpec source/hash/span，不包含 Prompt、query 或正文。
- `run` 必须显式提供 `--confirm-private-inputs`，否则在读取 API key 或调用模型前中止。
- `import` 同时校验 messages hash 与输出 SHA-256。
- `evaluate` 默认只生成匿名空白复核模板，不自动代替人工确认。
- 运行时未读取 `must_recall_facts`、gold sections、human relevance 或事实支持答案。

机器清单：[phase4r-batch-r3-package-manifest.json](phase4r-batch-r3-package-manifest.json)。

外部执行说明：[phase4r-batch-r3-external-agent-instructions-2026-07-20.md](phase4r-batch-r3-external-agent-instructions-2026-07-20.md)。

## 当前决策

本批只完成准备，不产生正文质量结论。外部 Agent 完成 12 次生成并通过 import 后，才能进行匿名质量评估。当前不得切换生产，不开始 Phase 5/6。

全量回归：unit 218 passed、integration 8 passed、quality 71 passed、compileall passed。
