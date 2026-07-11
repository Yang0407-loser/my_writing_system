# 开发进度

> 最后更新: 2026-06-02

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
