# Handover Contract V2 唯一真实 Demo

本说明只在工程门槛通过、用户明确授权后使用。不要在实现阶段运行。

## 启动独立 V2 Worker（CMD）

```cmd
cd /d E:\writer\my_writing_system
set WRITER_HANDOVER_CONTRACT_VERSION=v2
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing -n writer-handover-v2@%h
```

只运行一个正常的四小节写作任务。验收任务是否 completed、每小节是否仍只有一次 Handover 提取、是否没有新增 Writer 调用，并检查 accepted claim 的 source/hash/span、心理推断、stale fact、无来源 arc progress、critical carryover、下一场景边界、正文、checkpoint 和最终 Review。

真实 Demo 完成前不得晋级 V2。

## 恢复 V1（CMD）

```cmd
set WRITER_HANDOVER_CONTRACT_VERSION=v1
```
