# 开发进度

## P3A projection recovery (2026-08-12)

- 状态：DONE，真实 PostgreSQL Gate 16 passed / 0 skipped。
- 证据：`reports/p3a/p3a-gate-evidence.json`；摘要：`reports/p3a/p3a-gate-summary.md`。
- 运行手册：`docs/runbooks/p3a-projection-operations.md`。
- P3B 仍是外部 Alpha 的身份、租户隔离、凭据、备份与运维安全门槛。

> 最后更新: 2026-08-10

## P0 + P1/P2 Foundation

- 状态：服务端 Foundation 已完成；批准受控的 `internal_required` 内部新任务 dogfood，不代表外部生产就绪。
- 稳定基线：Foundation 实施落点 `10f4252`；合并基线标签 `narrative-os-foundation-v1`。
- Canonical 数据链路：Candidate → State Transition → 双 Head 原子提交 → Revision/State/Ledger/Idempotency/Outbox → Critical Projection Barrier。
- Python 3.11 全量 Gate：`1903 passed, 2 skipped, 5 warnings`；2 个 skip 仅对应未纳入版本库的私有含密钥夹具，PostgreSQL skip 为 0。
- PostgreSQL 16 Gate：Canonical 集成 `24 passed, 0 skipped`；真实 Golden Slice 为 `backend=postgresql`、`gate_eligible=true`、`phase=ready`。
- 合并后 smoke：在 `foundation/baseline-2026-08-09` 上重新执行核心 Foundation 测试 `103 passed`，并重新生成/验证 PostgreSQL Golden Slice。
- 回滚边界：已提交 Canon 始终保持权威；critical projection 未追平时禁止下一小节生成；legacy 回滚只影响尚未开始的写入。
- 下一阶段：P3A 投影与恢复平台、P3B 鉴权/租户隔离/凭据/CI/可观测性/备份安全门，完成后才可讨论外部生产接入和客户端收尾。

## 架构变更 (v3.x) — 深度一致性 + 精简协作

### v3 核心架构
- NarrativeEvent 统一事件模型（合并弧线、伏笔、世界事实、角色时间线）
- rank_and_fill() 权重排序替代三层预算
- EventGraph 精简为弧线追踪（砍伏笔/事实）
- 交接笔记分离（Writer 纯正文 + _extract_handover 独立提取）
- 情节节奏 narrative_rhythm 阶段（intensity 曲线）

### Agent 角色转变
- 警察→笔记员：记录事实、标注提醒、不做强制裁决
- Prompt 语气："不可违背"→"请注意保持一致"

### 大纲树状编辑器
- 任意深度树、AI 拆分、手动增删改移、全屏模式
- 文本导入（AI 识别层级）
- 节点选中态 + 工具栏"📥 导入"按钮
- globalWordLimit ↔ 叶子 target_words 双向同步

### 精修模式
- 五档强度 (low/mid/high/max) × 8 维度独立开关
- 响应按维度分组返回

### 写后分析
- 人物关系图（SVG 节点连线，颜色=关系类型，粗细=羁绊深度，hover=演化历史）
- 时间链（多角色轨道 SVG，共享事件垂直连线）
- POST /tasks/{task_id}/analyze，一次 LLM 调用

### P0 优化
- MemoryFuser 统一记忆入口
- pre/post check 规则检查（零 LLM 成本）
- 字数兜底（续写 ≤2 次，<60% 接受）

### 前端
- ComfyUI 暗色主题 (#121212 / #1e1e1e / #00bcd4)
- persistence.js 独立持久化模块
- 写作模式切换 UI 优化
- 删除密度滑块、角色时间线面板
- 事件摘要状态栏

### 持久化
- events_json / analysis_json SQLite 列 → 侧栏历史恢复
- v2→v3 localStorage 迁移
- beforeunload 强制保存

## 待办

| 事项 | 优先级 |
|------|--------|
| 休眠伏笔提醒 | 中 |
| 交互模式"加弧线事件"操作 | 设计 |
| 前端仪表盘可视化升级 | 低 |
| 批量大纲拆分/字数设置 | 设计 |
| 前端 UI 全面升级(ComfyUI 画布) | 低 |

## 不修改的文件

`blackboard.py`, `vector_store.py`, `character_store.py`, `context_manager.py`, `reviewer.py`, `planner.py`, `llm_client.py`, `json_parser.py`, `config.py`, `base.py`

## 启动命令

```bash
# 终端 1: Redis
E:\Redis\redis-server.exe

# 终端 2: Celery Worker
cd E:/writer/my_writing_system
uv run celery -A app.celery_app worker --loglevel=info -P solo

# 终端 3: FastAPI
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 浏览器
http://localhost:8000/write-ui
```
