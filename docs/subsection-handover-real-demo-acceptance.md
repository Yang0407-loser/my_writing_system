# Subsection Handover Artifact V1：唯一真实 Demo 验收

本说明只用于工程修复后的一个真实四小节任务。不要重跑历史任务，也不要增加第二个 Demo。

## 启动

1. 正常停止旧 Celery worker，确保新代码已加载。
2. 在 CMD 中按当前生产配置启动 writing worker：

```cmd
cd /d E:\writer\my_writing_system
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing
```

3. 从 Demo 正常提交一个恰好包含四个新小节的写作任务。
4. 记录 `task_id` 和完整 worker 日志路径；不要在 Git 中保存日志或正文。

## 只读验收

任务结束后仅检查：

- 任务为 `completed`，实际完成小节数恰好为 4；
- `subsection_handover_history_v1.records` 为 4，`pending` 为 0；
- record ID 无重复；
- 每条记录的 section/subsection、execution status、output SHA-256、prompt hash 和 commit idempotency key 完整；
- 每条 output SHA-256 对应已提交小节正文；
- Blackboard、checkpoint、TaskStore `analysis_json` 的 record ID/hash 一致；
- 现有节级 `handover_chain` 仍为一条，内容 hash 与旧聚合逻辑一致；
- handover extractor 仍每小节一次，没有新增 LLM 调用；
- Writer 正文、checkpoint、最终 Review 正常。

随后正常重启 worker，不重新提交写作任务，再次只读检查 record ID/hash 稳定。模拟 Redis 不可用时只能使用隔离测试或只读恢复入口，不得 `flushdb` 或删除真实任务数据；TaskStore 恢复率必须为 100%。

## 通过与停止

全部条件通过后，状态可更新为 `real_demo_accepted`。任何一项失败时只定位 Blackboard、checkpoint 或 TaskStore 镜像层，不得重新生成正文、修改 handover Prompt、回填历史任务或扩大到人物状态、关系、伏笔和经历。
