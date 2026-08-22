# V2.1 真实四小节 Demo — 执行手册

> 授权范围：**唯一一次**真实四小节 Demo。跑完即收口，无论通过与否都不允许同配置重跑。
> 当前默认：`WRITER_HANDOVER_CONTRACT_VERSION=v1`，Demo 结束后必须改回。

---

## 0. 为什么这一步不能由 Claude 代跑

Demo 需要同时满足：DeepSeek API 出网、本机 Redis（`E:\Redis`）、Celery worker、FastAPI。
云端沙箱三者都不可达（`api.deepseek.com` DNS 解析失败、`127.0.0.1:6379/8000` 连接被拒），
所以正文生成必须在你的 Windows 机器上跑。**审计和收口报告我来做**——你把日志和 task_id 给我即可。

---

## 1. Preflight（约 2 分钟）

### 1.1 建运行时目录并加 gitignore

```bat
cd /d E:\writer\my_writing_system
mkdir .v21_demo_runtime
echo .v21_demo_runtime/>> .gitignore
```

> 与 `.handover_content_audit_runtime/` 等目录同样处理，避免私有日志进 git。

### 1.2 打开 V2.1

编辑 `.env`，加入（或修改）这一行：

```
WRITER_HANDOVER_CONTRACT_VERSION=v2.1
```

### 1.3 确认配置真的生效

```bat
uv run python -c "from app.config import settings; print('VERSION =', settings.WRITER_HANDOVER_CONTRACT_VERSION)"
```

必须打印 `VERSION = v2.1`。打印 `v1` 说明 `.env` 没被读到——**先停下**，不要开始 Demo。
（`app/config.py:110` 对非法值会静默回落到 `v1`，这是最容易踩的坑。）

### 1.4 确认没有其他实验开关被顺带打开

```bat
uv run python -c "from app.config import settings; import re; print({k:v for k,v in vars(settings).items() if re.search(r'SCENE_SPEC|BOUNDARY|EXECUTION_CONTRACT|STATE_FRAME|SHARED_EXTRACT|CONTEXT_BROKER|ARC_CONTRACT', k)})"
```

期望：SceneSpec off、BoundaryValidator false、ExecutionContract off、StateFrame 未注入、
Shared Extractor off、`CHARACTER_ARC_CONTRACT_VERSION=v1`。
**任何一项被打开都会污染归因**，必须先关掉。

---

## 2. 启动（关键：worker 必须重启才会加载 V2.1 代码）

先把 `start.bat` 起的旧 Celery / FastAPI 窗口**全部关掉**，然后：

```bat
cd /d E:\writer\my_writing_system

:: Redis
"E:\Redis\redis-cli.exe" ping || start "Writer-Redis" /min "E:\Redis\redis-server.exe"

:: Celery —— 带日志落盘，审计脚本靠这个文件
start "Writer-Celery" /d "E:\writer\my_writing_system" cmd /k ^
  "uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing,celery --logfile=.v21_demo_runtime\celery.log"

:: FastAPI（注意去掉 --reload，避免中途重载导致代码版本漂移）
start "Writer-FastAPI" /d "E:\writer\my_writing_system" cmd /k ^
  "uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

启动后确认日志真的在写：

```bat
dir .v21_demo_runtime\celery.log
```

文件为空说明 `--logfile` 没接管 app logger。改用 PowerShell 兜底重启 worker：

```powershell
uv run celery -A app.celery_app worker --loglevel=info -P solo -Q writing,celery 2>&1 |
  Tee-Object -FilePath .v21_demo_runtime\celery.log
```

---

## 3. 跑 Demo

打开 http://localhost:8000/write-ui-v2 ，提交一个**正常的**四小节写作任务。

要求：

- 4 个小节，和 V2 那次（`cd830826…`，4 小节 / 242.7s / 36,525 token）量级相当；
- 用真实项目，不要为 Demo 专门构造极端大纲；
- 中途**不要**改大纲、不要改风格、不要手动重试、不要点任何 SceneSpec / Validator 相关按钮；
- 任务跑完后记下 **task_id**（UI 或 `/docs` 都能拿到）。

Handover 失败是 fail-open 的——正文、checkpoint、Review 会照常完成。
**看到 handover 报错不要中断任务**，那正是需要被记录的数据。

---

## 4. 审计（跑完立刻做）

```bat
uv run python tests\benchmarks\audit_handover_v21_demo.py ^
  --log .v21_demo_runtime\celery.log ^
  --task-id <你的TASK_ID> ^
  --expected-subsections 4
```

脚本是纯 stdlib、只读的，不会碰 Redis、不会写数据库、不会读正文或 Prompt。
它交叉核对两个独立证据源：

1. Celery 日志里的 `handover_v21_observation={...}` 遥测行；
2. `tasks.db → task_history.analysis_json → subsection_handover_history_v1` 的落盘记录。

产出 `reports/handover-contract-v21-real-demo.{json,md}`。

### 九道门槛

| 门槛 | 含义 |
|---|---|
| `G1_one_call_per_subsection` | 每小节恰好 1 次 handover 调用 |
| `G2_no_extra_body_retry` | mandatory event 实际重试 = 0 |
| `G3_no_output_truncation` | **V2 就死在这**：finish_reason 不能是 `length` |
| `G4_typed_contract_built` | 四次都成功构建 typed contract |
| `G5_output_headroom` | 输出峰值 < 600，留有余量 |
| `G6_claims_restored_locally` | source ID/hash/excerpt 确实由本地 registry 恢复 |
| `G7_handover_chain_persisted` | 四条 v2.1 记录 `committed` 落盘 |
| `G8_fail_open_task_completed` | 任务 `completed`，正文未受影响 |
| `G9_source_registry_stable` | 每小节有独立 registry hash（全同说明 registry 没随小节更新） |

---

## 5. 收口

### 5.1 无论结果如何

```
.env 改回 WRITER_HANDOVER_CONTRACT_VERSION=v1
```

V2.1 **不是**生产默认，Demo 只是取证。

### 5.2 通过时

只能得出「输出契约容量与工程隔离通过」。**不能**据此宣称 handover 内容质量通过，
也**不能**直接接 StateFrame / OutcomeBundle / Writer 输入——那需要另行授权的内容有效性审计。

### 5.3 失败时（按项目既有停止规则）

- 不扩 Prompt、不加关键词、不提高 600 上限、不重复调用；
- 不同配置重跑；
- 把失败层级写清楚：是**容量层**（又截断）、**解析层**（JSON 结构不合法）、
  还是**语义层**（解析成功但 claim 被 validator 大量拒绝）——这三层的下一步完全不同。

### 5.4 交给我

把这两样发我，我来写符合项目格式的收口报告、更新
`plans/2026-07-17-context-consistency-refactor.md` 的执行记录表，并给出下一步建议：

- `reports/handover-contract-v21-real-demo.json`
- task_id
