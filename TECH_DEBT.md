# WriterAgent 技术债清单

> 2026-07-09 · v0.9.0 · 独立开发

---

## 1. 基础设施层

### 1.1 Redis 中间态 Hash 无 TTL

- **起因**：MVP 阶段以跑通流程为优先，中间态数据（style/outline/draft/characters/world_state）通过 `bb.set(task_id, key, value)` 写入 Redis Hash 后，未设置过期时间。没有任务级生命周期管理机制。
- **现状**：每次写作任务在 Redis 中产生一套 Hash key，任务完成后永久保留，直到 Redis 重启或手动 `DEL`。单用户场景下内存增长极慢，暂未触发问题。
- **位置**：`app/blackboard.py:20-23` — `set()` 方法只做 `HSET`，无 `EXPIRE`
- **严重程度**：低（单用户永远不触发；多用户 + 高频提交场景下会成为内存泄漏源）
- **复杂场景解决**：给每个 `task_id` 的 Hash key 加统一 `EXPIRE`（如 7 天），或在 `coordinator.py` 任务完成/失败分支中调用 `bb.delete(task_id)` 显式清理。注意区分 checkpoint（需保留 24h）和普通中间态的 TTL。产品化后可引入 Redis `SCAN` + 定时任务回收僵尸 task_id。

### 1.2 Redis 承担五种数据角色

- **起因**：MVP 阶段架构收敛——broker、backend、状态黑板、Stream 推送、检查点/决策队列全部走 Redis，避免引入 RabbitMQ/PostgreSQL/WebSocket 等额外基础设施，一套 `docker-compose up` 搞定。
- **现状**：Redis 在项目中同时充当消息队列、结果存储、Agent 通信中间件、流式推送通道、断点恢复快照。单机单用户无问题，但缺乏各层的专业保障（消息无持久化确认、状态无查询能力、推送是轮询非长连接）。
- **位置**：全项目 — Celery 配置走 `app/celery_app.py`，黑板走 `app/blackboard.py`，Stream 走 `blackboard.py:54-87`，检查点走 `blackboard.py:134-165`
- **严重程度**：中（产品化后每层需要独立中间件）
- **复杂场景解决**：渐进替换——① broker 切 RabbitMQ（消息持久化 + ACK）；② 状态存储切 PostgreSQL（查询 + 事务）；③ 推送切 WebSocket（真实时 + 减少空转请求）；④ Redis 只保留缓存 + 分布式锁 + 轻量队列的原生角色。

### 1.3 Celery solo worker 无并发

- **起因**：Windows 不支持 `fork`，Celery 必须用 `-P solo`。同时单用户场景串行已足够。
- **现状**：`celery -A app.celery_app worker -P solo`，同一时间只能执行一个 task。10 个用户同时提交 → 第 10 个要等前面 9 个全跑完（每个几分钟到十几分钟）。
- **位置**：`app/celery_app.py` — 启动参数 `-P solo`；README.md Windows 启动说明
- **严重程度**：中（多用户场景必须改）
- **复杂场景解决**：Linux 部署切 `-P prefork` 或 `-P gevent`，worker 数 = CPU 核数。加上任务优先级队列（付费/高级用户优先）、并发提交数限制（超过 N 直接拒绝）、异步通知（webhook/邮件告知完成）。

### 1.4 Celery broker 用 Redis List，无消息持久化确认

- **起因**：Redis 作为 Celery broker 是标配，配置简单。
- **现状**：Redis List 做 broker 无 `acks_late` 之外的消息确认机制，Redis 重启/宕机会丢失队列中未消费的任务。
- **位置**：`app/celery_app.py` — `broker_url = settings.REDIS_BROKER_URL`
- **严重程度**：中
- **复杂场景解决**：切 RabbitMQ broker，原生支持消息持久化 + consumer ACK + 死信队列。Redis 只保留为 result backend。

---

## 2. 数据层

### 2.1 tasks.db 与 Redis Hash 部分数据重复存储

- **起因**：`TaskStore` 在任务完成时将中间产物（style_json、outline_json、handover_json、characters_json、review_json）从 Redis 复制到 SQLite 做归档，方便任务历史浏览。但 Redis 端的原始 Hash 未清理，形成双写。
- **现状**：同一份 style JSON 同时存在于 Redis Hash `task_id:style` 和 SQLite `task_history` 表的 `style_json` 列。前者无 TTL 永久保留，后者持久化归档。冗余但不影响功能。
- **位置**：`app/task_store.py:65-113` — `save()` 方法将全部字段写 SQLite；`app/blackboard.py:20-23` — Redis 端无对应清理
- **严重程度**：低
- **复杂场景解决**：明确生命周期——Redis Hash 设 7 天 TTL 作为热数据，SQLite 作为冷归档。任务完成时写入 SQLite 后显式清理 Redis Hash 或依赖 TTL 自然过期。避免维护两份热数据。

### 2.2 draft 只在 Redis 存完整版，SQLite 只存前 2000 字

- **起因**：`draft_preview` 的设计意图是任务列表页展示摘要，不需要完整草稿。完整 draft 在 Redis Hash 中。
- **现状**：Redis 过期/重启后，历史任务的完整草稿丢失，只剩下 SQLite 中的 2000 字预览。
- **位置**：`app/task_store.py:93` — `"draft_preview": draft_text[:2000]`
- **严重程度**：低
- **复杂场景解决**：完整 draft 写入 `output/` 目录的 Markdown 文件（已有导出功能），或 SQLite 中存 `draft_full` TEXT 列（不受 Redis TTL 影响）。`draft_preview` 保留用于列表页。

### 2.3 11 个独立 SQLite 文件，无外键约束

- **起因**：每个业务域（角色、伏笔、规则、事件、支线等）独立建库，便于 MVP 阶段快速迭代——增删表不影响其他域。
- **现状**：`characters.db`、`events.db`、`foreshadowings.db`、`rules.db`、`subplots.db`、`maps.db`、`projects.db`、`tasks.db`、`experience.db`、`items.db`、`factions.db` 共 11 个独立文件。角色 ID 在 `characters.db` 中被引用但 `events.db` 无法做外键约束，数据完整性靠应用层保证。
- **位置**：项目根目录 `*.db` 文件 + `app/config.py` 各 DB 路径配置
- **严重程度**：中
- **复杂场景解决**：迁移到单个 PostgreSQL 数据库，各域用 schema 隔离（`characters.*` / `events.*` 等），加外键约束和联合查询能力。SQLite 保留为离线/测试环境方案。

### 2.4 数据库迁移用 try/except 硬加列

- **起因**：快速迭代阶段改表结构，用 `ALTER TABLE ADD COLUMN` + `try/except` 跳过已存在的列，不引入 migration 工具。
- **现状**：`task_store.py` 的 `_MIGRATIONS_DONE` 用内存 set 记录已迁移的 db_path，`ALTER TABLE` 失败静默吞错。
- **位置**：`app/task_store.py:21-34`
- **严重程度**：中
- **复杂场景解决**：引入 Alembic 做结构化迁移，或至少把 `try/except sqlite3.OperationalError` 改为检查 `sqlite_master` 表里的列是否存在后再 ALTER，避免吞掉真正的 SQL 错误。

---

## 3. 代码质量

### 3.1 writer.py 1308 行 / coordinator.py 1137 行 / prompt_templates.py 922 行

- **起因**：v0.1→v0.9 快速迭代中，功能持续追加到核心文件，未做模块拆分。Writer 承载了写作 + 交接提取 + 字数控制 + RAG + 流式 fallback + 审阅触发 等全部职责。
- **现状**：三个文件合计 3367 行，理解和调试成本高。但单人开发下全局搜索修改方便，无跨模块 ref 传递的认知开销。
- **位置**：`app/agents/writer.py` (1308L) · `app/coordinator.py` (1137L) · `app/utils/prompt_templates.py` (922L)
- **严重程度**：高（团队协作/长期维护必拆）
- **复杂场景解决**：Writer 拆分为 `writer_core.py`（prompt 构建 + LLM 调用 + 流式）+ `writer_handover.py`（交接提取）+ `writer_wordctrl.py`（字数控制）+ `writer_rag.py`（RAG 检索入库）。Coordinator 按 Phase 拆分：`phase_writing.py` / `phase_outline.py` 等。Prompt 模板按 Agent 拆文件。

### 3.2 47 处 `except Exception:` 裸捕获

- **起因**：快速开发中为保不崩，广泛使用 `except Exception` 兜底。大部分场景是"某个非关键步骤失败不应阻塞主流程"。
- **现状**：writer.py 最密集（17 处），coordinator.py（5 处），card_drawer.py（4 处）。大部分有 `logger.warning/error` 记录，但异常对象 `e` 未绑定，排查时看不到具体错误类型和堆栈。
- **位置**：全项目，典型 `except Exception:` 在 `app/agents/writer.py:323,328,333,640,654`
- **严重程度**：中
- **复杂场景解决**：全局替换为 `except Exception as e:` + `logger.warning("xxx 失败: %s", e, exc_info=True)`。关键路径（写作、状态持久化）加 Sentry 或结构化日志上报。

### 3.3 4 处 `except Exception: pass` 静默吞错

- **起因**：非关键路径上为确保流程不中断，直接 `pass` 跳过。
- **现状**：`character_store.py:60`（get 失败时静默返回 None）、`writer.py:323/328/333`（世界状态/角色/规则提取失败时静默跳过）。失败无日志，问题无法追溯。
- **位置**：`app/character_store.py:60` · `app/agents/writer.py:323,328,333`
- **严重程度**：高（生产环境无法定位问题）
- **复杂场景解决**：至少加 `logger.warning("xxx 提取失败，已跳过", exc_info=True)`。如果某个静默跳过的步骤频繁失败，说明有隐藏 bug，没日志永远发现不了。

### 3.4 LLM 调用的 temperature/max_tokens 硬编码在各 Agent 中

- **起因**：每个 Agent 在调 LLM 时直接写死 `temperature=0.3, max_tokens=4000` 等参数，未统一收敛到配置层。
- **现状**：coordinator.py、reviewer.py、character_manager.py、card_drawer.py、continuity_editor.py 各自硬编码。调参需改多个文件。
- **位置**：`app/coordinator.py:492,826,1095` · `app/agents/reviewer.py:33,90,179` · `app/agents/character_manager.py:39,83,148` · `app/agents/card_drawer.py:71,88,120,125` · `app/agents/continuity_editor.py:39`
- **严重程度**：低
- **复杂场景解决**：在 `config.py` 中按 Agent 定义 LLM 参数常量（如 `WRITER_TEMP=0.5` / `REVIEWER_TEMP=0.3`），所有 Agent 从统一入口读取。方便批量调参和 A/B 测试。

### 3.5 LLM 重试/流式调用模式在多个文件中重复实现

- **起因**：各 Agent 独立开发时各自实现了相似的重试 + 流式 fallback 逻辑。
- **现状**：writer.py、card_drawer.py、coordinator.py、reviewer.py 中存在相似的 retry loop + stream/non-stream switch 代码。`writer.py` 的 `_generate_with_retry()` 和 `card_drawer.py` 的生成逻辑有重叠但未共享。
- **位置**：`app/agents/writer.py:974-1002` · `app/agents/card_drawer.py` · `app/coordinator.py`
- **严重程度**：中
- **复杂场景解决**：提取 `LLMClient.generate_with_retry()` 统一方法，支持 stream callback 注入和 fallback 策略配置。各 Agent 只需要调 `self.llm.generate_with_retry(messages, config)`。

### 3.6 coordinator.py 内联 fallback 字典 hack

- **起因**：某个异常路径下临时用硬编码字典兜底，未提取为常量或配置。
- **现状**：`coordinator.py:927` — `{"_fallback": True}` 内联字典，语义不清。
- **位置**：`app/coordinator.py:927`
- **严重程度**：低
- **复杂场景解决**：定义 `FallbackResult` 命名元组或 dataclass，语义明确，便于全局搜索和重构。

---

## 4. 前端

### 4.1 main.js 1063 行单文件

- **起因**：v0.9 重构前端时选择 ES Modules + Vue 3 CDN + 零构建工具，所有组件/状态/UI 写在 `main.js` 中方便快速迭代。引入组件拆分需要解决跨文件状态共享和 import 依赖拓扑，拖慢迭代速度。
- **现状**：1063 行单文件承载了三栏布局、大纲树编辑器、流式草稿渲染、风格控制台、抽卡面板、精修面板、规则/伏笔/支线/地图管理、AI 痕迹检测等全部 UI 逻辑。
- **位置**：`app/static/js/main.js` (1063L)
- **严重程度**：高（团队协作/长期维护必拆）
- **复杂场景解决**：按面板/功能拆分为独立 Vue 组件文件，通过 ES Module import 加载。Store 用 `store.js` 已有基础，组件间通过 store 共享状态。拆分为 `OutlinePanel.js` / `DraftPanel.js` / `StylePanel.js` / `CardPanel.js` / `RefinePanel.js` 等，`main.js` 只做根组件组装。

### 4.2 错误处理只到 toast 级别

- **起因**：前端以功能实现为优先，catch 块中统一用 `toast('xxx失败', 'error')` 提示用户，未传递后端返回的具体错误信息。
- **现状**：大部分 API 调用 catch 中 `toast('操作失败','error')` 丢失 `e.message`。用户只知道"失败了"，不知道原因（是网络断了、API key 过期、还是 DeepSeek 限流了）。
- **位置**：`app/static/js/main.js` — 大量 `catch(e) { toast('xxx失败', 'error') }`
- **严重程度**：中
- **复杂场景解决**：toast 增加 `e.message` 展示，或在 debug 模式下展开。后端 API 返回结构化错误 `{"error": "code", "detail": "xxx"}`，前端根据 error code 给出可操作的提示（如"API Key 无效，请前往设置更新"）。

### 4.3 Stream 轮询出错静默重试，用户无感知

- **起因**：Stream 轮询设置了 300ms 间隔，如果后端暂时不可达（网络波动/Redis 瞬断），`console.warn` 记录后继续下次轮询。设计意图是轮询不应因单次失败而终止。
- **现状**：`pollStream()` 中 catch 只打 `console.warn('Stream polling error:', e)`，用户看不到任何提示。如果后端一直不可达，前端静静等待直到用户手动刷新。
- **位置**：`app/static/js/stream.js:105-107`
- **严重程度**：低
- **复杂场景解决**：连续失败 N 次（如 10 次 = 3 秒）后 toast 提示"连接中断，正在重连"，连续失败 M 次（如 100 次 = 30 秒）后 toast 提示"连接超时，请刷新页面"。增加重连状态指示器（状态栏颜色变化）。

### 4.4 无全局错误边界

- **起因**：Vue 3 CDN 模式下未配置 `app.config.errorHandler`。
- **现状**：未捕获的 JS 异常可能导致页面白屏或部分组件卡死。
- **位置**：`app/static/js/main.js` — Vue 3 应用初始化处
- **严重程度**：中
- **复杂场景解决**：`app.config.errorHandler = (err, instance, info) => { toast('页面出错: ' + err.message, 'error', 5000); console.error(err, info); }`。关键面板加 `v-if` + fallback 占位防止单个面板崩溃拖垮整个页面。

---

## 5. Prompt 工程

### 5.1 全部 prompt 为字符串常量，无版本管理

- **起因**：Prompt 集中在 `prompt_templates.py` 并作为 Python 字符串常量管理，修改后直接替换旧值，无法追溯历史版本。
- **现状**：36KB 的 prompt 模板，改了就改了。无法对比不同版本的生成效果差异。
- **位置**：`app/utils/prompt_templates.py` (36KB, 922L)
- **严重程度**：中
- **复杂场景解决**：每个 prompt 模板加版本号注释 + 修改日志。引入结构化 prompt 注册表（`PromptRegistry`），按 `{name, version}` 索引。A/B 测试框架：同时跑两个版本，人工/自动评分对比效果。

### 5.2 `format()` 注入变量缺参数直接抛异常

- **起因**：Prompt 模板用 Python 字符串 `{variable}` 占位，`format()` 时若漏传参数直接 `KeyError`。
- **现状**：所有 Agent 在构建 prompt 时手动保证参数齐全，无编译期/运行时 schema 校验。
- **位置**：全项目 — 所有 `.format()` 调用点
- **严重程度**：低
- **复杂场景解决**：用 `string.Template` + `safe_substitute()` 替代 `.format()`，缺参数时保留占位符而非抛异常。或定义 Pydantic model 校验 prompt 参数完整性。

---

## 6. 运维

### 6.1 无 CI/CD

- **起因**：单人开发 + 手动部署，CI/CD 在 MVP 阶段优先级最低。
- **现状**：部署靠手写 `DEPLOY.md` 文档：SSH 到 ECS → `git pull` → 重启 Celery + Nginx。测试靠手动 `python tests/test_basic.py`。
- **位置**：无 — 不存在
- **严重程度**：中
- **复杂场景解决**：GitHub Actions — push → pytest → 通过后 SSH 到 ECS 自动 deploy。测试覆盖率 >60% 后才触发自动部署。至少先加一个 lint 步骤。

### 6.2 无健康检查端点

- **起因**：FastAPI 自带 `/docs` 和 `/openapi.json`，但无专门的 health check 端点。
- **现状**：无法被负载均衡器或监控系统探活。
- **位置**：`app/main.py` — 无 `/health` 路由
- **严重程度**：低
- **复杂场景解决**：加 `GET /health` 返回 `{"status":"ok","redis":true,"chromadb":true}`，检测下游依赖连通性。

### 6.3 无日志聚合/告警

- **起因**：日志打 stdout + `StreamHandler`，开发阶段够用。
- **现状**：无结构化日志、无持久化、无聚合查询。Celery worker 和 FastAPI 的日志各自打 stdout，无关联。
- **位置**：`app/config.py:105-114` — `StreamHandler` only
- **严重程度**：低
- **复杂场景解决**：引入 `structlog` 输出 JSON 格式日志 → Filebeat/Loki 采集 → Grafana 可视化。Celery task_id 已通过 `ContextVar` 注入日志，具备关联基础。

### 6.4 无自动化测试集成

- **起因**：测试文件 `tests/test_basic.py` 手动运行，未集成到开发流程。
- **现状**：测试覆盖了 embedding 验证、LLM 连通性、完整写作流程、角色数据模型。但每次改代码后不会自动跑。
- **位置**：`tests/test_basic.py`
- **严重程度**：中
- **复杂场景解决**：装 `pytest-watch` 或配置 VS Code 保存时自动跑测试。GitHub Actions 加 `pytest` job。

---

## 总结

| 层级 | 高 | 中 | 低 | 合计 |
|------|----|----|----|------|
| 基础设施 | 0 | 3 | 1 | 4 |
| 数据层 | 0 | 2 | 2 | 4 |
| 代码质量 | 1 | 3 | 2 | 6 |
| 前端 | 1 | 2 | 1 | 4 |
| Prompt 工程 | 0 | 1 | 1 | 2 |
| 运维 | 0 | 2 | 2 | 4 |
| **合计** | **2** | **13** | **9** | **24** |

**核心结论**：24 条技术债全部是 MVP 阶段"能跑就行"的合理取舍，没有一条是设计失误。每条都有清晰的产品化偿还路径。当前单用户部署场景下，高严重度的两条（writer.py/coordinator.py 大文件、main.js 单文件）是架构层面的优先重构项，其余可随产品化进程渐进修复。
