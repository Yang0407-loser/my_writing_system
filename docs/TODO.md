# TODO

## 待修复

- [x] **coordinator 撤掉 sync_outline_to_sqlite 调用** — 启动时写 SQLite 会覆盖用户手动设置的 subsection status（`done`/`queued`）。前端 fallback to Redis 已够解决"重启丢大纲" (2026-07-15)
- [x] **Celery worker 重启后 Reviewer 采样代码不生效** — ~~需重启 worker 才能加载新代码，确认 `every-3rd-section` 是否生效~~ 添加日志：采样模式/全审模式标注 + 跳过节号列表 + 实际审阅计数 (2026-07-15)
- [x] **Writer 中途不刷新 status 变更** — line 187 只检测 `len(updated) > len(outline)`（新增节），不检测已有节的 status 变化。已扩展为同时检测 subsection 级别的 status 变更 (2026-07-15)
- [x] **大纲存储迁移 SQLite → Redis** — 删除 `project_store.py`，`routers/outline.py` 改为纯 Redis 读写，前端去掉三层 fallback 和重复 sync 调用。根除了写作队列失效问题（SQLite/Redis 双写不同源） (2026-07-15)

## 待验证

- [ ] **风格模板测试** — `ENABLE_STYLE_BEHAVIOR=true`，3000 字，跑 `style-drift` 对比漂移是否从 32pp 下降
- [ ] **style-drift + density + perf + contradiction + token-cost** — 数据收齐
- [ ] **消融对比** — `ENABLE_STYLE_BEHAVIOR=false` 跑一轮，对比 style-drift

## 方向级决策

- [ ] **风格仿写台** — 放弃 50 维参数路线，改为"上传参考文本→蒸馏→生成风格卡→few-shot 注入 prompt"。需验证 few-shot 风格模仿效果是否优于参数注入
- [ ] **RAG reranker** — Precision@5 现在 75-86%，加 BGE-Reranker 预期到 85%+。改 ~30 行，低风险高回报

## 已完成的近期工作

- [x] RAG Recall@5/Precision@5 自动评估 + LLM judge 人工校准 (Kappa 0.71)
- [x] 风格可复现性测试 (余弦 0.99)
- [x] ContinuityEditor 矛盾检测统计埋点
- [x] Agent token 分账 (Writer 67% / Reviewer 33%)
- [x] 每节叙事节奏注入 (beat_reminder)
- [x] expand_causal 边关系填充
- [x] 大纲丢失修复 (前端 SQLite → Redis fallback)
- [x] undo TTL 1h → 24h
- [x] Reviewer 间隔抽样 + 文本截断
- [x] style-drift / info-density / style-repro / token-cost eval 命令
- [x] 风格模板示例文本 (style_mapping.py → build_style_examples)
- [x] ENABLE_STYLE_BEHAVIOR 风格消融开关
