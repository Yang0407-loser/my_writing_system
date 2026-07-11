# 角色库系统 — 设计文档 (v2)

> 日期: 2026-05-18
> 项目: 多智能体协作写作系统 (my_writing_system)
> 依赖: 人物设定系统 (2026-05-18-character-system-design.md)
> 版本: v2 — 基于 6 项反馈优化

## 1. 概述

为现有角色系统增加持久化角色库。用户通过结构化表单（支持精简/专业模式 + LLM 辅助提取）创建/编辑角色，数据存储在 SQLite 数据库中。写作时通过固定按钮唤出侧边栏勾选角色，一键注入写作流程。Writer 写作后进行角色一致性检查。角色库独立于单次写作任务，支持跨任务复用。

### 用户故事

1. 点击右下角"角色库"按钮，侧边栏滑出，浏览已有角色，勾选加入本次写作
2. 点击"+ 新建角色"打开模态框，默认精简模式（5 字段），可切换到专业模式（17 字段）
3. 粘贴人物小传 → 一键提取多个角色 → 勾选保存 → 重名时选择覆盖/合并/跳过
4. 在"人物"标签页看到 Phase 0 提取的角色，点击"保存到角色库"或"全部导入角色库"
5. Writer 每节写完后，角色一致性检查器对比生成内容与角色库设定，发现偏差记录到 continuity_issues
6. 系统根据角色动机/秘密主动建议情节走向

---

## 2. 数据模型

### 2.1 SQLite 表结构

**characters 主表**（单值字段 + 复杂 JSON 字段）：

```sql
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    age TEXT DEFAULT '',
    motivation TEXT DEFAULT '',
    background TEXT DEFAULT '',
    appearance TEXT DEFAULT '',
    catchphrase TEXT DEFAULT '',
    secret TEXT DEFAULT '',
    world_position TEXT DEFAULT '',
    symbolism TEXT DEFAULT '',
    key_lines TEXT DEFAULT '[]',         -- JSON 数组，结构复杂
    relationships TEXT DEFAULT '[]',     -- JSON 数组 [{"target":"李四","relation":"宿敌"}]
    custom TEXT DEFAULT '{}',            -- JSON 字典，用户自定义扩展
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

**character_traits 关联表**（多值枚举属性，可索引检索）：

```sql
CREATE TABLE IF NOT EXISTS character_traits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL,
    trait_type TEXT NOT NULL,    -- 'personality' | 'strength' | 'weakness'
    trait_value TEXT NOT NULL,   -- 单个标签值，如 '内向'、'坚韧'
    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_traits_type_value
    ON character_traits(trait_type, trait_value);

CREATE INDEX IF NOT EXISTS idx_traits_character
    ON character_traits(character_id);
```

**检索示例：**

```sql
-- 查询所有性格包含"内向"的角色
SELECT DISTINCT c.* FROM characters c
JOIN character_traits t ON c.id = t.character_id
WHERE t.trait_type = 'personality' AND t.trait_value LIKE '%内向%';

-- 统计性格分布
SELECT trait_value, COUNT(*) as cnt
FROM character_traits WHERE trait_type = 'personality'
GROUP BY trait_value ORDER BY cnt DESC;
```

### 2.2 CharacterProfile 扩展

```python
class CharacterProfile(BaseModel):
    # 单值字段（存 characters 表）
    id: str = ""
    name: str
    gender: str = ""
    age: str = ""
    motivation: str = ""
    background: str = ""
    appearance: str = ""
    catchphrase: str = ""
    secret: str = ""
    world_position: str = ""
    symbolism: str = ""

    # 多值标签字段（存 character_traits 表）
    personality: list[str] = []
    strengths: list[str] = []
    weaknesses: list[str] = []

    # JSON 字段（结构复杂，存 characters 表 JSON 列）
    key_lines: list[str] = []
    relationships: list[dict] = []   # [{"target":"李四","relation":"宿敌"}]

    # 扩展字段
    custom: dict = {}                # 用户自定义属性
```

### 2.3 数据分层总结

| 存储位置 | 字段 | 原因 |
|----------|------|------|
| `characters` 表普通列 | name, gender, age, motivation, background, appearance, catchphrase, secret, world_position, symbolism | 单值文本，直接 WHERE LIKE 检索 |
| `character_traits` 表 | personality, strengths, weaknesses | 多值枚举，需索引、聚合、精确匹配 |
| `characters` 表 JSON 列 | key_lines, relationships, custom | 结构复杂（台词含上下文、关系含对象+类型、自定义字段不可预知），JSON 更灵活 |

---

## 3. CharacterStore

**文件**: `app/character_store.py`

```python
class CharacterStore:
    def __init__(self, db_path: str = "./characters.db"):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_tables()

    def _ensure_tables(self): ...  # 创建 characters + character_traits 表

    # CRUD
    def list_all(self, search: str = "", limit: int = 50) -> list[dict]:
        """搜索角色。search 匹配 name/motivation/background 字段
           以及 character_traits 表中的 trait_value。"""

    def get(self, char_id: str) -> dict | None:
        """查询单个角色，同时加载 traits 填充 personality/strengths/weaknesses。"""

    def create(self, char: dict) -> dict:
        """创建角色：写入 characters 表 + 拆分标签写入 character_traits 表。
           自动生成 UUID，返回完整角色数据。"""

    def update(self, char_id: str, char: dict) -> dict | None:
        """更新角色：只更新提供的字段。标签字段先删后插。"""

    def delete(self, char_id: str) -> bool:
        """删除角色（CASCADE 自动清理 traits）。"""

    def find_by_name(self, name: str) -> list[dict]:
        """精确/模糊查找同名角色，用于保存前查重。"""

    def stats(self) -> dict:
        """返回角色库统计：总数、性格分布 top 10、动机关键词。"""
```

---

## 4. API 端点

所有端点注册在 `app/main.py`，路径前缀 `/api/characters`。

### 4.1 GET /api/characters — 列出角色

```
Query: ?search=关键词&limit=50&trait=personality:内向
Response: { "characters": [...], "total": N, "stats": {...} }
```

`search` 匹配 name、motivation、background 及 traits 表。`trait` 过滤器支持 `personality:内向` 精确匹配。

### 4.2 GET /api/characters/{id} — 角色详情

```
Response: { "character": {...} }
404: { "detail": "角色不存在" }
```

### 4.3 POST /api/characters — 新建角色

```
Body: CharacterProfile JSON (name 必填)
Response: { "character": {...} }
400: 验证失败
409: { "detail": "同名角色已存在", "existing": {...} }
```

创建前自动查重（`find_by_name`），如果同名则返回 409 + 已有角色数据，由前端决定合并策略。

**合并策略 query param**：`?on_conflict=skip|overwrite|merge`

- `skip`（默认）：拒绝创建，返回 409
- `overwrite`：覆盖已有角色（PUT 语义）
- `merge`：保留已有角色非空字段，仅用新数据填充空字段

### 4.4 PUT /api/characters/{id} — 更新角色

同现有设计，部分字段更新。

### 4.5 DELETE /api/characters/{id} — 删除角色

同现有设计。

### 4.6 POST /api/characters/extract — 自然语言提取

```
Body: { "text": "张三和李四是兄弟..." }
Response: {
    "characters": [
        {"client_id": "temp_1", "name": "张三", "personality": ["勇敢"], ...},
        {"client_id": "temp_2", "name": "李四", "personality": ["狡猾"], ...}
    ]
}
```

- 每个提取的角色带临时 `client_id`（前端用）
- 返回**完整列表**而非仅第一个
- 不自动保存

### 4.7 POST /api/characters/batch-save — 批量保存

```
Body: { "characters": [...], "on_conflict": "skip" }
Response: { "saved": [...], "skipped": [...], "merged": [...] }
```

批量保存提取的多个角色。

### 4.8 GET /api/characters/stats — 角色库统计

```
Response: { "total": 12, "top_traits": [...], "motif_keywords": [...] }
```

---

## 5. Web UI

### 5.1 角色库侧边栏

**触发方式**：右下角固定浮动按钮"角色库"

- 位置：`position: fixed; bottom: 24px; right: 24px; z-index: 100`
- 样式：圆形按钮，图标为人物剪影，显示角色库中角色总数角标
- 点击 → 侧边栏从右侧滑入（`transform: translateX(0)`，默认 `translateX(100%)`）
- 再次点击或点击遮罩层 → 侧边栏滑出

**侧边栏**：

```
┌─ 角色库 ─────────────────── [×] ─┐
│  [搜索角色...]               [+ 新建] │
│  [性格: 全部 ▼] [按时间排序 ▼]       │  ← 过滤 + 排序
│  ┌────────────────────────────────┐ │
│  │ ☑ 张三  固执·温柔             │ │
│  │   复仇者，口头禅"习惯了"       │ │
│  ├────────────────────────────────┤ │
│  │ ☐ 李四  冷静·腹黑             │ │
│  │   商界精英，暗中操控一切       │ │
│  └────────────────────────────────┘ │
│                                      │
│  [已选 1 个角色]  [导入当前任务角色] │  ← 底部操作栏
└──────────────────────────────────────┘
```

- 侧边栏 `width: 320px; height: 100vh; position: fixed; right: 0; top: 0; z-index: 99`
- 使用 `transform: translateX(100%)` → `translateX(0)` 动画，**不挤压主内容**
- 背景遮罩层仅在侧边栏展开时显示（`position: fixed; inset: 0; background: rgba(0,0,0,0.5)`）
- 角色卡片：左侧勾选框，中间姓名+标签+简介，右侧展开箭头（点击查看详情）

### 5.2 角色编辑模态框

**模式切换**：顶部精简/专业模式开关

**精简模式**（默认，适合快速创建"路人甲"）：

```
┌──────────────────────────────────────┐
│  新建角色                    [精简 ●] │  ← 精简/专业切换
│                                      │
│  快速提取：                           │
│  [粘贴人物小传，一键提取填充...]       │
│  [提取并填充]  [从模板创建 ▼]         │  ← 模板：士兵/商人/法师/自定义
│                                      │
│  姓名*：[张三____]  性别：[男]  年龄：[28] │
│  性格标签：[固执, 温柔___________]    │  ← 逗号分隔或回车添加
│  一句话背景：[被背叛的退伍军人_______] │
│                                      │
│  ── 展开更多设定 ──                   │  ← 可折叠，点击展开全16字段
│                                      │
│           [保存到角色库]              │
└──────────────────────────────────────┘
```

**专业模式**（切换到专业模式时展开全部字段）：

全部 17 字段（含 custom），分组为：
1. 基本信息（name/gender/age/personality）
2. 核心设定（motivation/background/appearance/catchphrase）
3. 优缺点（strengths/weaknesses）
4. 深度设定（secret/world_position/symbolism）
5. 台词与关系（key_lines/relationships）
6. 自定义（custom 键值对）

**标签输入**：单个 input，支持逗号分隔（"固执, 温柔, 内向"）或回车添加。下方显示已添加的标签（可 × 删除）。

**模板系统**：点击"从模板创建"下拉菜单，选择预设模板（士兵/商人/法师/书生/流浪者），自动填充 strengths、weaknesses 等典型字段，用户微调。

### 5.3 LLM 提取增强流程

1. 用户在提取区粘贴人物小传
2. 点击"提取并填充" → `POST /api/characters/extract`
3. 返回角色列表（可能多个），展示为可勾选卡片列表：
   ```
   提取了 3 个角色：
   ☑ 张三（勇敢、固执）— 复仇者
   ☑ 李四（冷静、腹黑）— 商界精英
   ☐ 王五（懦弱、善良）— 张三的旧友
   [全部保存] [逐个保存]
   ```
4. 点击角色卡片 → 填充到右侧编辑表单（可逐个修改后保存）
5. 逐个保存时，每个角色保存前自动调用 `GET /api/characters?search=张三` 查重
   - 无重复 → 直接保存
   - 有重复 → 弹出选项："覆盖 / 合并（保留非空字段）/ 跳过"
6. "全部保存"按钮 → `POST /api/characters/batch-save`

### 5.4 写作表单集成

在 `debug_ui.html` 提交表单中：
- "角色设定"区域新增 "从角色库选择" 按钮 → 展开侧边栏
- 侧边栏中勾选的角色实时显示在提交表单中（标签形式，可 × 移除）
- 提交时 characters 字段使用勾选角色的完整数据
- 如果同时提供了 character_text，则已勾选角色 + LLM 提取角色合并（去重：同 name 视为同一角色，以角色库数据优先）

### 5.5 角色卡片详情

在侧边栏点击角色卡片，展开详情面板（非模态框，覆盖在侧边栏内容之上）：

```
┌─ 张三 ───────────────── [编辑] [×] ┐
│  男 · 28岁                        │
│  性格：固执 温柔                   │
│  动机：为战友复仇                  │
│  背景：被背叛的退伍军人            │
│  口头禅："习惯了"                  │
│  当前状态：愤怒中 (来自上次写作任务)│
│  ── 关系 ──                       │
│  宿敌 → 李四                      │
│  ── 出场记录 ──                   │
│  城市里的孤独 (2026-05-15)        │
│  英雄归来 (2026-05-10)            │
└────────────────────────────────────┘
```

`[编辑]` 按钮打开模态框加载该角色数据。

---

## 6. 写作流程集成

### 6.1 Coordinator 修改

```python
# Phase 0: 角色加载
if characters:
    # 角色来自角色库（用户勾选），已结构化，跳过 LLM 提取
    bb.set(task_id, "characters", characters)
    _add_timeline(bb, task_id, "character", "system",
                  f"从角色库加载 {len(characters)} 个角色")
elif character_text:
    # 自然语言输入，LLM 提取
    cm = CharacterManager()
    characters = cm.extract_characters(character_text)
    bb.set(task_id, "characters", characters)
```

### 6.2 Writer 角色库引用 + 一致性检查

**Prompt 增强**：角色上下文中，来自角色库的角色标注 `[角色库: 上次出场《英雄归来》，状态=愤怒]`，提示 LLM 保持跨故事一致性。

**角色一致性检查器**（Phase 3.5，每节写完后，轻量 LLM 调用）：

```python
# 在 Writer 每节写完后，调用一致性检查
def _check_character_consistency(section_text, characters):
    """对比生成段落与角色库设定，发现不一致。"""
    prompt = CHARACTER_CONSISTENCY_PROMPT.format(
        section_text=section_text[:4000],
        characters_json=json.dumps(characters, ensure_ascii=False),
    )
    # LLM 返回: [{"character": "张三", "issue": "对话中表现外向，但设定为内向", "severity": "minor"}]
    return consistency_issues
```

不一致问题存入 `continuity_issues` 列表，供 ContinuityEditor 在 Phase 4 汇总。

### 6.3 情节建议

Writer 在规划小节时，可检索角色库中角色的 `motivation` 和 `secret` 字段，在 prompt 中注入：

```
## 可用的情节线索
- 张三（复仇者）：他的秘密（抛弃战友）可能在本节被李四发现，引发信任危机
```

这作为一个可选的增强提示段落，由环境变量 `ENABLE_PLOT_SUGGESTIONS=true` 控制开关。

---

## 7. 数据迁移

### 7.1 Phase 0 角色 → 角色库

在 "人物"标签页的每张角色卡片右上角，增加 "保存到角色库" 按钮。点击：
1. 调 `GET /api/characters?search=张三` 查重
2. 无重复 → `POST /api/characters` 直接保存
3. 有重复 → 弹窗："角色库中已存在张三。覆盖 / 合并 / 取消"

### 7.2 批量导入

在侧边栏底部 "导入当前任务角色" 按钮：
- 一键将黑板中 `task_id:characters` 的所有角色导入角色库
- 逐个查重，有重复的跳过或提示用户选择

---

## 8. 文件变更清单

| 文件 | 操作 | 变更 |
|------|------|------|
| `app/character_store.py` | **新建** | SQLite CRUD + traits 关联表 + 查重 + 统计 |
| `app/models.py` | 修改 | CharacterProfile 新增 `custom: dict = {}` |
| `app/main.py` | 修改 | 注册 8 个 API 端点 + CharacterStore 单例 |
| `app/coordinator.py` | 修改 | 角色库加载；Phase 3.5 一致性检查 |
| `app/utils/prompt_templates.py` | 修改 | 新增 CHARACTER_CONSISTENCY_PROMPT |
| `debug_ui.html` | 修改 | 浮动按钮 + 侧边栏（滑出动画）+ 编辑模态框（精简/专业模式 + 模板 + 逗号分隔标签）+ 详情面板 + 提取多角色流程 + 保存/导入按钮 |

**新建 1 文件，修改 5 文件。**

---

## 9. 测试

| 测试 | 说明 |
|------|------|
| `test_character_store_crud()` | SQLite 增删改查 + traits 表读写 |
| `test_character_store_search()` | 模糊搜索 + trait 过滤 |
| `test_character_store_duplicate()` | 同名检测 |
| `test_api_characters_crud()` | GET/POST/PUT/DELETE 端点 |
| `test_api_characters_extract()` | 多角色提取 + client_id |
| `test_api_characters_batch_save()` | 批量保存 + on_conflict 策略 |
| `test_api_characters_stats()` | 统计端点 |
| `test_character_consistency_check()` | 角色一致性检查 LLM 调用 |

---

## 10. 自审清单

- [x] 无 TBD / TODO
- [x] 多值标签字段拆入 character_traits 表，支持索引检索和聚合统计
- [x] 侧边栏使用固定按钮 + transform 动画，不挤压主内容
- [x] 编辑表单支持精简/专业模式切换 + 逗号分隔输入 + 预设模板
- [x] LLM 提取支持多角色 + client_id + 批量保存 + 查重合并策略
- [x] Writer 集成：角色库引用标注 + 一致性检查器 + 情节建议（可选）
- [x] 数据迁移：单角色保存 + 批量导入 + 查重提示
- [x] 向后兼容（角色库为空时功能不受影响）
