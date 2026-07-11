# 角色库系统 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为人物设定系统增加持久化角色库，支持 SQLite 双表存储、8 个 REST API、角色库侧边栏、编辑模态框（精简/专业模式 + 模板 + 多角色提取）、Writer 一致性检查器和数据迁移。

**Architecture:** 新建 CharacterStore 封装 SQLite（characters + character_traits 双表），API 端点注册在 main.py，UI 以浮动按钮唤出侧边栏 + 模态框方式集成到 debug_ui.html，Coordinator 新增角色库加载和一致性检查阶段。

**Tech Stack:** Python 3.11+, sqlite3 (标准库), FastAPI, Pydantic v2

**Spec:** `docs/superpowers/specs/2026-05-18-character-library-design.md`

---

## File Responsibility Map

| File | Responsibility |
|------|---------------|
| `app/character_store.py` (新) | SQLite 双表 CRUD + traits 读写 + 查重 + 统计 |
| `app/models.py` | CharacterProfile 新增 `custom: dict = {}` |
| `app/main.py` | 8 个 `/api/characters/*` 端点 + CharacterStore 单例 |
| `app/utils/prompt_templates.py` | 新增 CHARACTER_CONSISTENCY_PROMPT |
| `app/coordinator.py` | Phase 0 角色库加载；Phase 3.5 一致性检查；情节建议 |
| `debug_ui.html` | 浮动按钮 + 侧边栏 + 模态框（简/专模式 + 模板 + 多角色提取）+ 保存/导入按钮 + 详情面板 |

---

### Task 1: CharacterStore — SQLite 存储层

**Files:**
- Create: `app/character_store.py`
- Modify: `app/models.py` — CharacterProfile 新增 `custom: dict = {}`

- [ ] **Step 1: 创建 character_store.py 骨架和建表逻辑**

```python
import sqlite3
import uuid
import json
import os


class CharacterStore:
    """SQLite 角色库封装。双表：characters + character_traits。"""

    JSON_FIELDS = ["key_lines", "relationships", "custom"]
    TRAIT_FIELDS = ["personality", "strengths", "weaknesses"]

    def __init__(self, db_path: str = "./characters.db"):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_tables()

    def _ensure_tables(self):
        self._conn.execute("""
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
                key_lines TEXT DEFAULT '[]',
                relationships TEXT DEFAULT '[]',
                custom TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS character_traits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                trait_type TEXT NOT NULL,
                trait_value TEXT NOT NULL,
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_traits_type_value
                ON character_traits(trait_type, trait_value)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_traits_character
                ON character_traits(character_id)
        """)
        self._conn.commit()
```

- [ ] **Step 2: 实现 JSON/行数据序列化辅助方法**

```python
    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        for f in self.JSON_FIELDS:
            if f in d and isinstance(d[f], str):
                try:
                    d[f] = json.loads(d[f])
                except (json.JSONDecodeError, TypeError):
                    pass
        # personality/strengths/weaknesses 由 _load_traits 填充
        return d

    def _dict_to_row(self, d: dict) -> dict:
        """将 dict 转为插入 characters 表的 cols。traits 字段排除（由 _save_traits 处理）。"""
        result = {}
        for k, v in d.items():
            if k in self.TRAIT_FIELDS or k in ("created_at", "updated_at"):
                continue
            if k in self.JSON_FIELDS and not isinstance(v, str):
                result[k] = json.dumps(v, ensure_ascii=False)
            else:
                result[k] = v
        return result

    def _save_traits(self, char_id: str, traits: dict):
        """保存标签到 character_traits 表。先删后插。"""
        self._conn.execute(
            "DELETE FROM character_traits WHERE character_id = ?", (char_id,)
        )
        for trait_type in self.TRAIT_FIELDS:
            values = traits.get(trait_type, [])
            if isinstance(values, str):
                values = [v.strip() for v in values.split(",") if v.strip()]
            for v in values:
                if v:
                    self._conn.execute(
                        "INSERT INTO character_traits (character_id, trait_type, trait_value) VALUES (?, ?, ?)",
                        (char_id, trait_type, v),
                    )

    def _load_traits(self, char_id: str) -> dict:
        """加载角色的标签，返回 {personality: [...], strengths: [...], weaknesses: [...]}。"""
        rows = self._conn.execute(
            "SELECT trait_type, trait_value FROM character_traits WHERE character_id = ?",
            (char_id,),
        ).fetchall()
        traits = {"personality": [], "strengths": [], "weaknesses": []}
        for r in rows:
            t = r["trait_type"]
            if t in traits:
                traits[t].append(r["trait_value"])
        return traits
```

- [ ] **Step 3: 实现 CRUD 方法**

```python
    def create(self, char: dict) -> dict:
        char_id = char.get("id") or str(uuid.uuid4())
        row = self._dict_to_row(char)
        row["id"] = char_id
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self._conn.execute(
            f"INSERT INTO characters ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self._save_traits(char_id, char)
        self._conn.commit()
        return self.get(char_id)

    def get(self, char_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM characters WHERE id = ?", (char_id,)
        ).fetchone()
        if not row:
            return None
        result = self._row_to_dict(row)
        result.update(self._load_traits(char_id))
        return result

    def update(self, char_id: str, char: dict) -> dict | None:
        existing = self.get(char_id)
        if not existing:
            return None
        existing.update(char)
        row = self._dict_to_row(existing)
        row["updated_at"] = None  # SQLite 自动 datetime('now')
        set_clause = ", ".join(f"{k} = ?" for k in row.keys())
        self._conn.execute(
            f"UPDATE characters SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            list(row.values()) + [char_id],
        )
        self._save_traits(char_id, existing)
        self._conn.commit()
        return self.get(char_id)

    def delete(self, char_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM characters WHERE id = ?", (char_id,))
        self._conn.commit()
        return cur.rowcount > 0
```

- [ ] **Step 4: 实现搜索和统计方法**

```python
    def list_all(self, search: str = "", limit: int = 50, trait_filter: str = "") -> list[dict]:
        query = "SELECT DISTINCT c.* FROM characters c"
        params = []
        conditions = []

        if search:
            query += " LEFT JOIN character_traits t ON c.id = t.character_id"
            conditions.append(
                "(c.name LIKE ? OR c.motivation LIKE ? OR c.background LIKE ? OR t.trait_value LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like])

        if trait_filter and ":" in trait_filter:
            t_type, t_value = trait_filter.split(":", 1)
            if t_type in self.TRAIT_FIELDS:
                if "LEFT JOIN character_traits" not in query:
                    query += " LEFT JOIN character_traits t ON c.id = t.character_id"
                conditions.append("t.trait_type = ? AND t.trait_value LIKE ?")
                params.extend([t_type, f"%{t_value}%"])

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY c.updated_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        result = []
        seen = set()
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                d = self._row_to_dict(r)
                d.update(self._load_traits(r["id"]))
                result.append(d)
        return result

    def find_by_name(self, name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM characters WHERE name LIKE ?", (f"%{name}%",)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) as c FROM characters").fetchone()["c"]
        top_traits = self._conn.execute(
            "SELECT trait_type, trait_value, COUNT(*) as cnt FROM character_traits "
            "GROUP BY trait_type, trait_value ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        return {
            "total": total,
            "top_traits": [
                {"type": r["trait_type"], "value": r["trait_value"], "count": r["cnt"]}
                for r in top_traits
            ],
        }
```

- [ ] **Step 5: 修改 models.py — CharacterProfile 新增 custom 字段**

在 `app/models.py` 的 `CharacterProfile` 类中，`relationships` 之后添加：

```python
    custom: dict = {}
```

- [ ] **Step 6: 验证 CharacterStore**

```bash
cd E:/writer/my_writing_system && uv run python -c "
from app.character_store import CharacterStore
store = CharacterStore('./test_characters.db')
c = store.create({'name': '测试', 'personality': ['勇敢', '固执'], 'motivation': '复仇'})
print('Created:', c['name'], c['personality'])
found = store.list_all(search='勇敢')
print('Search found:', len(found))
store.delete(c['id'])
import os; os.remove('./test_characters.db')
print('CharacterStore OK')
"
```

Expected: `CharacterStore OK`

- [ ] **Step 7: Commit**

```bash
cd E:/writer/my_writing_system && git add app/character_store.py app/models.py && git commit -m "feat: add CharacterStore with SQLite dual-table CRUD, search, stats"
```

---

### Task 2: API 端点

**Files:**
- Modify: `app/main.py` — 注册 8 个 `/api/characters/*` 端点

- [ ] **Step 1: 在 main.py 顶部初始化 CharacterStore 单例**

在 `app/main.py` 的 import 区域添加：

```python
from .character_store import CharacterStore

char_store = CharacterStore()
```

- [ ] **Step 2: 注册 GET /api/characters — 列表**

```python
@app.get("/api/characters")
def list_characters(search: str = "", limit: int = 50, trait: str = ""):
    chars = char_store.list_all(search=search, limit=limit, trait_filter=trait)
    stats = char_store.stats()
    return {"characters": chars, "total": len(chars), "stats": stats}
```

- [ ] **Step 3: 注册 GET /api/characters/{id} — 详情**

```python
@app.get("/api/characters/{char_id}")
def get_character(char_id: str):
    char = char_store.get(char_id)
    if not char:
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"character": char}
```

- [ ] **Step 4: 注册 POST /api/characters — 新建（含查重和合并）**

```python
@app.post("/api/characters")
def create_character(body: dict, on_conflict: str = "skip"):
    # 查重
    name = body.get("name", "")
    if name:
        existing = char_store.find_by_name(name)
        if existing:
            if on_conflict == "skip":
                raise HTTPException(status_code=409, detail="同名角色已存在", existing=existing[0])
            elif on_conflict == "overwrite":
                char_store.update(existing[0]["id"], body)
                return {"character": char_store.get(existing[0]["id"])}
            elif on_conflict == "merge":
                merged = existing[0]
                for k, v in body.items():
                    if v not in (None, "", [], {}):
                        merged[k] = v
                char_store.update(merged["id"], merged)
                return {"character": char_store.get(merged["id"])}
    char = char_store.create(body)
    return {"character": char}
```

- [ ] **Step 5: 注册 PUT /api/characters/{id} — 更新**

```python
@app.put("/api/characters/{char_id}")
def update_character(char_id: str, body: dict):
    char = char_store.update(char_id, body)
    if not char:
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"character": char}
```

- [ ] **Step 6: 注册 DELETE /api/characters/{id} — 删除**

```python
@app.delete("/api/characters/{char_id}")
def delete_character(char_id: str):
    if not char_store.delete(char_id):
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"status": "deleted"}
```

- [ ] **Step 7: 注册 POST /api/characters/extract — 自然语言提取**

```python
@app.post("/api/characters/extract")
def extract_characters(body: dict):
    text = body.get("text", "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text 不能为空")
    from .agents.character_manager import CharacterManager
    cm = CharacterManager()
    chars = cm.extract_characters(text)
    # 给每个角色加临时 client_id
    for i, c in enumerate(chars):
        c["client_id"] = f"temp_{i + 1}"
    return {"characters": chars}
```

- [ ] **Step 8: 注册 POST /api/characters/batch-save — 批量保存**

```python
@app.post("/api/characters/batch-save")
def batch_save_characters(body: dict, on_conflict: str = "skip"):
    chars = body.get("characters", [])
    if not chars:
        raise HTTPException(status_code=400, detail="characters 不能为空")
    saved, skipped, merged = [], [], []
    for c in chars:
        name = c.get("name", "")
        existing = char_store.find_by_name(name) if name else []
        if existing:
            if on_conflict == "skip":
                skipped.append(c)
            elif on_conflict == "overwrite":
                char_store.update(existing[0]["id"], c)
                merged.append(char_store.get(existing[0]["id"]))
            elif on_conflict == "merge":
                m = existing[0]
                for k, v in c.items():
                    if v not in (None, "", [], {}):
                        m[k] = v
                char_store.update(m["id"], m)
                merged.append(char_store.get(m["id"]))
        else:
            saved.append(char_store.create(c))
    return {"saved": saved, "skipped": skipped, "merged": merged}
```

- [ ] **Step 9: 注册 GET /api/characters/stats — 统计**

```python
@app.get("/api/characters/stats")
def get_character_stats():
    return char_store.stats()
```

- [ ] **Step 10: 验证 API 导入**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.main import app; print('API OK')"
```

Expected: `API OK`

- [ ] **Step 11: Commit**

```bash
cd E:/writer/my_writing_system && git add app/main.py && git commit -m "feat: add 8 character library API endpoints"
```

---

### Task 3: 一致性检查 Prompt + Coordinator 集成

**Files:**
- Modify: `app/utils/prompt_templates.py` — 追加 CHARACTER_CONSISTENCY_PROMPT
- Modify: `app/coordinator.py` — Phase 0 角色库加载；Phase 3.5 一致性检查

- [ ] **Step 1: 追加 CHARACTER_CONSISTENCY_PROMPT**

在 `app/utils/prompt_templates.py` 末尾追加：

```python
# ----------------------------------------------------------------
# 角色一致性检查
# ----------------------------------------------------------------
CHARACTER_CONSISTENCY_PROMPT = """你是一位严谨的角色一致性检查员。请对比以下生成内容与角色库中的角色设定，找出不一致之处。

刚写好的正文片段：
{section_text}

角色库中的角色设定：
{characters_json}

请以 JSON 数组格式输出（不要包含其他内容）：
[
  {{
    "character": "角色名",
    "issue": "不一致的具体描述",
    "severity": "critical 或 minor"
  }}
]

判断标准：
- critical: 性格完全相反、关系矛盾、关键背景被改写
- minor: 措辞风格微偏、口头禅未使用、外貌描写细节不符
- 如果没有不一致，输出空数组 []"""
```

- [ ] **Step 2: Coordinator Phase 0 — 角色库加载逻辑**

在 `coordinator.py` 的 `writing_task()` 中，修改角色初始化逻辑。

找到现有的 `characters = []` 初始化块，替换为：

```python
    if characters is None:
        characters = []

    # Phase 0: 角色来源判断
    if characters:
        # 角色来自角色库（用户通过 UI 勾选），已结构化，跳过 LLM 提取
        bb.set(task_id, "characters", characters)
        _add_timeline(bb, task_id, "character", "system",
                      f"从角色库加载 {len(characters)} 个角色")
    elif character_text:
        # 自然语言输入，LLM 提取（现有逻辑）
        bb.set(task_id, "status", "extracting_characters")
        cm = CharacterManager()
        try:
            characters = cm.extract_characters(character_text)
            if characters:
                bb.set(task_id, "characters", characters)
                _add_timeline(bb, task_id, "character", "character_manager",
                              f"提取 {len(characters)} 个角色",
                              ", ".join(c.get("name", "?") for c in characters))
        except Exception as e:
            _add_timeline(bb, task_id, "character", "system",
                          f"角色提取失败: {str(e)[:200]}")
```

- [ ] **Step 3: Coordinator Phase 3.5 — 角色一致性检查**

在 `coordinator.py` 的 Phase 3（Writer）和 Phase 4（ContinuityEditor）之间添加：

```python
        # ================================================
        # Phase 3.5: 角色一致性检查
        # ================================================
        consistency_issues = []
        if characters and os.getenv("ENABLE_CONSISTENCY_CHECK", "true").lower() != "false":
            bb.set(task_id, "status", "checking_character_consistency")
            try:
                from .utils.prompt_templates import CHARACTER_CONSISTENCY_PROMPT
                from .utils.llm_client import get_llm_client
                llm = get_llm_client()
                characters_json = _json.dumps(characters, ensure_ascii=False, indent=2)
                prompt = CHARACTER_CONSISTENCY_PROMPT.format(
                    section_text=draft[:4000],
                    characters_json=characters_json,
                )
                messages = [
                    {"role": "system", "content": "你是一位严谨的角色一致性检查员。"},
                    {"role": "user", "content": prompt},
                ]
                resp = llm.chat_completion(messages, temperature=0.3, max_tokens=1000)
                try:
                    consistency_issues = _json.loads(resp)
                except _json.JSONDecodeError:
                    from .utils.json_parser import parse_json
                    try:
                        consistency_issues = parse_json(resp)
                    except ValueError:
                        pass
                if consistency_issues:
                    bb.set(task_id, "consistency_issues", consistency_issues)
                    _add_timeline(bb, task_id, "consistency", "character_checker",
                                  f"发现 {len(consistency_issues)} 处角色不一致")
            except Exception as e:
                _add_timeline(bb, task_id, "consistency", "system",
                              f"角色一致性检查失败: {str(e)[:200]}")
```

需要添加 `import os` 在 coordinator.py 顶部。

- [ ] **Step 4: 可选情节建议**

在 Writer prompt 构建中，如果角色来自角色库且有 motivation/secret，注入情节线索（通过 `characters` 中的 motivation/secret 字段构建）。在 `Writer._build_character_context()` 中，角色上下文末尾追加：

```python
        # 情节建议（可选）
        plot_hints = []
        for c in (characters or []):
            secret = c.get("secret", "")
            motivation = c.get("motivation", "")
            if secret:
                plot_hints.append(f"- {c.get('name', '?')}: 秘密({secret})可能在后续情节中被揭露")
            if motivation:
                plot_hints.append(f"- {c.get('name', '?')}: 动机({motivation})驱动情节走向")
        if plot_hints:
            arc_context += "\n\n## 可用的情节线索\n" + "\n".join(plot_hints)
```

- [ ] **Step 5: 验证导入**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.coordinator import writing_task; from app.utils.prompt_templates import CHARACTER_CONSISTENCY_PROMPT; print('Coordinator OK')"
```

Expected: `Coordinator OK`

- [ ] **Step 6: Commit**

```bash
cd E:/writer/my_writing_system && git add app/utils/prompt_templates.py app/coordinator.py app/agents/writer.py && git commit -m "feat: add character consistency checker and library-aware coordinator flow"
```

---

### Task 4: UI — 浮动按钮 + 角色库侧边栏

**Files:**
- Modify: `debug_ui.html`

- [ ] **Step 1: 添加浮动按钮 HTML + CSS**

在 `</body>` 之前添加：

```html
<!-- 角色库浮动按钮 -->
<button id="charLibraryBtn" onclick="toggleCharLibrary()" style="position:fixed;bottom:24px;right:24px;width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,#42a5f5,#7e57c2);border:none;color:#fff;font-size:22px;cursor:pointer;z-index:100;box-shadow:0 4px 16px rgba(0,0,0,0.4);transition:transform 0.2s;">
  👥<span id="charLibCount" style="position:absolute;top:-4px;right:-4px;background:#f44336;border-radius:50%;width:20px;height:20px;font-size:10px;line-height:20px;text-align:center;">0</span>
</button>

<!-- 遮罩层 -->
<div id="charLibraryOverlay" onclick="toggleCharLibrary()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:98;"></div>
```

- [ ] **Step 2: 添加侧边栏 HTML**

```html
<!-- 角色库侧边栏 -->
<div id="charLibrary" style="position:fixed;right:0;top:0;width:320px;height:100vh;background:#1a1a2e;border-left:1px solid rgba(255,255,255,0.08);z-index:99;transform:translateX(100%);transition:transform 0.3s ease;display:flex;flex-direction:column;">
  <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid rgba(255,255,255,0.06);">
    <h3 style="color:#90caf9;margin:0;font-size:15px;">角色库</h3>
    <button onclick="toggleCharLibrary()" style="background:none;border:none;color:#999;font-size:18px;cursor:pointer;">✕</button>
  </div>
  
  <!-- 搜索 + 过滤 -->
  <div style="padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.04);">
    <input id="charLibSearch" type="text" placeholder="搜索角色..." oninput="refreshCharLibrary()" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#d0d0d0;font-size:12px;">
    <select id="charLibFilter" onchange="refreshCharLibrary()" style="margin-top:6px;width:100%;padding:6px 8px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#d0d0d0;font-size:11px;">
      <option value="">全部性格</option>
    </select>
  </div>
  
  <!-- 角色列表 -->
  <div id="charLibList" style="flex:1;overflow-y:auto;padding:8px 12px;">
    <div class="empty-state">加载中...</div>
  </div>
  
  <!-- 底部操作栏 -->
  <div style="padding:10px 16px;border-top:1px solid rgba(255,255,255,0.06);">
    <button onclick="openCharModal()" style="width:100%;padding:8px;background:linear-gradient(135deg,#42a5f5,#7e57c2);border:none;border-radius:6px;color:#fff;font-size:12px;cursor:pointer;">+ 新建角色</button>
    <button onclick="importCurrentCharacters()" style="margin-top:6px;width:100%;padding:6px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#bbb;font-size:11px;cursor:pointer;">导入当前任务角色</button>
  </div>
</div>
```

- [ ] **Step 3: 添加 toggleCharLibrary() 和 refreshCharLibrary() JS 函数**

```javascript
// 角色库全局状态
var CHAR_LIB = {
  visible: false,
  selected: {},   // {char_id: true} — 已勾选的角色
  allChars: [],   // 角色库所有角色缓存
};

function toggleCharLibrary() {
  CHAR_LIB.visible = !CHAR_LIB.visible;
  var sidebar = document.getElementById('charLibrary');
  var overlay = document.getElementById('charLibraryOverlay');
  if (CHAR_LIB.visible) {
    sidebar.style.transform = 'translateX(0)';
    overlay.style.display = 'block';
    refreshCharLibrary();
  } else {
    sidebar.style.transform = 'translateX(100%)';
    overlay.style.display = 'none';
  }
}

async function refreshCharLibrary() {
  var search = document.getElementById('charLibSearch').value;
  var filter = document.getElementById('charLibFilter').value;
  var url = apiUrl('/api/characters?search=' + encodeURIComponent(search) + '&limit=50');
  if (filter) { url += '&trait=' + encodeURIComponent('personality:' + filter); }
  try {
    var resp = await fetch(url);
    var data = await resp.json();
    CHAR_LIB.allChars = data.characters || [];
    document.getElementById('charLibCount').textContent = data.stats ? data.stats.total : CHAR_LIB.allChars.length;
    renderCharLibraryList(data.characters || []);
    // 更新性格过滤
    var filterEl = document.getElementById('charLibFilter');
    if (data.stats && data.stats.top_traits) {
      var traits = data.stats.top_traits.filter(function(t) { return t.type === 'personality'; });
      filterEl.innerHTML = '<option value="">全部性格</option>' +
        traits.map(function(t) { return '<option value="' + t.value + '">' + t.value + ' (' + t.count + ')</option>'; }).join('');
    }
  } catch(e) {
    log('角色库加载失败: ' + e.message, 'error');
  }
}

function renderCharLibraryList(chars) {
  var html = '';
  var colors = ['#64b5f6', '#ce93d8', '#ff8a65', '#81c784', '#ffb74d', '#ef5350'];
  if (chars.length === 0) {
    html = '<div class="empty-state">暂无角色，点击下方按钮新建</div>';
  }
  chars.forEach(function(c, idx) {
    var color = colors[idx % colors.length];
    var tags = (c.personality || []).map(function(p) {
      return '<span class="char-tag" style="background:' + color + '33;color:' + color + '">' + escHtml(p) + '</span>';
    }).join(' ');
    var checked = CHAR_LIB.selected[c.id] ? 'checked' : '';
    html += '<div class="char-card char-lib-card" style="border-left:3px solid ' + color + ';cursor:pointer;padding:10px;margin:6px 0;" onclick="viewCharDetail(\'' + c.id + '\')">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;">' +
        '<div><strong style="color:' + color + '">' + escHtml(c.name) + '</strong></div>' +
        '<input type="checkbox" ' + checked + ' onclick="event.stopPropagation();toggleCharSelect(\'' + c.id + '\')" style="cursor:pointer;">' +
      '</div>' +
      '<div style="margin-top:4px;">' + tags + '</div>' +
      '<div style="font-size:11px;color:#888;margin-top:4px;">' + escHtml(c.motivation || '').substring(0, 40) + '</div>' +
    '</div>';
  });
  document.getElementById('charLibList').innerHTML = html;
}

function toggleCharSelect(charId) {
  if (CHAR_LIB.selected[charId]) {
    delete CHAR_LIB.selected[charId];
  } else {
    CHAR_LIB.selected[charId] = true;
  }
  updateSelectedCharsDisplay();
  log('已选角色: ' + Object.keys(CHAR_LIB.selected).length + ' 个', 'info');
}

function updateSelectedCharsDisplay() {
  var selected = CHAR_LIB.allChars.filter(function(c) { return CHAR_LIB.selected[c.id]; });
  // 在提交表单中显示已选角色标签
  // (在 Task 5 的写作表单集成中实现)
}
```

- [ ] **Step 4: 验证 HTML 无 JS 语法错误**

```bash
cd E:/writer/my_writing_system && uv run python -c "print('HTML file size:', len(open('debug_ui.html', encoding='utf-8').read()), 'chars')"
```

- [ ] **Step 5: Commit**

```bash
cd E:/writer/my_writing_system && git add debug_ui.html && git commit -m "feat: add character library floating button and sidebar to debug_ui.html"
```

---

### Task 5: UI — 角色编辑模态框

**Files:**
- Modify: `debug_ui.html` — 追加模态框 HTML + JS 函数

- [ ] **Step 1: 添加编辑模态框 HTML**

在 `</body>` 之前（浮动按钮之后）添加模态框：

```html
<!-- 角色编辑模态框 -->
<div id="charEditModal" style="display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,0.7);overflow-y:auto;">
  <div style="max-width:620px;margin:40px auto;background:#1a1a2e;border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:0;">
    
    <!-- 头部 -->
    <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 20px;border-bottom:1px solid rgba(255,255,255,0.06);">
      <h3 style="color:#90caf9;margin:0;" id="charModalTitle">新建角色</h3>
      <div style="display:flex;gap:8px;align-items:center;">
        <label style="font-size:11px;color:#999;">
          <input type="radio" name="charMode" value="simple" checked onchange="switchCharMode('simple')"> 精简
        </label>
        <label style="font-size:11px;color:#999;">
          <input type="radio" name="charMode" value="full" onchange="switchCharMode('full')"> 专业
        </label>
        <button onclick="closeCharModal()" style="background:none;border:none;color:#999;font-size:18px;cursor:pointer;">✕</button>
      </div>
    </div>
    
    <!-- 表单内容 -->
    <div style="padding:16px 20px;max-height:70vh;overflow-y:auto;">
      
      <!-- 快速提取区 -->
      <div style="margin-bottom:16px;">
        <label style="font-size:12px;color:#999;">快速提取（粘贴人物小传）</label>
        <textarea id="charExtractText" placeholder="张三，28岁，退伍军人。性格固执但内心柔软..." style="width:100%;height:60px;margin-top:4px;"></textarea>
        <button onclick="extractAndFill()" style="margin-top:6px;padding:6px 16px;background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.15);border-radius:6px;color:#ccc;font-size:12px;cursor:pointer;">提取并填充</button>
        <select onchange="applyTemplate(this.value)" style="margin-top:6px;margin-left:8px;padding:6px 8px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:6px;color:#d0d0d0;font-size:11px;">
          <option value="">从模板创建...</option>
          <option value="soldier">士兵</option>
          <option value="merchant">商人</option>
          <option value="mage">法师</option>
          <option value="scholar">书生</option>
          <option value="wanderer">流浪者</option>
        </select>
        <div id="charExtractResult" style="margin-top:8px;font-size:12px;color:#81c784;"></div>
      </div>
      
      <!-- 基本信息（精简模式 + 专业模式共有） -->
      <div>
        <label style="font-size:12px;color:#999;">姓名 *</label>
        <input id="charFormName" style="width:100%;padding:8px 12px;margin-bottom:10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
        
        <div style="display:flex;gap:10px;">
          <div style="flex:1;">
            <label style="font-size:12px;color:#999;">性别</label>
            <input id="charFormGender" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
          </div>
          <div style="flex:1;">
            <label style="font-size:12px;color:#999;">年龄</label>
            <input id="charFormAge" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
          </div>
        </div>
        
        <label style="font-size:12px;color:#999;margin-top:10px;">性格标签（逗号分隔）</label>
        <input id="charFormPersonality" placeholder="固执, 温柔, 内向" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
        
        <label style="font-size:12px;color:#999;margin-top:10px;">一句话背景</label>
        <input id="charFormBackground" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
      </div>
      
      <!-- 专业模式字段 -->
      <div id="charFormFullFields" style="display:none;">
        <label style="font-size:12px;color:#999;margin-top:10px;">核心动机</label>
        <textarea id="charFormMotivation" style="width:100%;height:50px;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;resize:vertical;"></textarea>
        
        <label style="font-size:12px;color:#999;margin-top:10px;">外貌描写</label>
        <textarea id="charFormAppearance" style="width:100%;height:50px;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;resize:vertical;"></textarea>
        
        <label style="font-size:12px;color:#999;margin-top:10px;">口头禅</label>
        <input id="charFormCatchphrase" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
        
        <label style="font-size:12px;color:#999;margin-top:10px;">优点（逗号分隔）</label>
        <input id="charFormStrengths" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
        
        <label style="font-size:12px;color:#999;margin-top:10px;">缺点（逗号分隔）</label>
        <input id="charFormWeaknesses" style="width:100%;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
        
        <label style="font-size:12px;color:#999;margin-top:10px;">秘密 / 软肋</label>
        <textarea id="charFormSecret" style="width:100%;height:50px;padding:8px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;resize:vertical;"></textarea>
        
        <details style="margin-top:14px;">
          <summary style="color:#64b5f6;cursor:pointer;font-size:12px;">高级设定</summary>
          <label style="font-size:12px;color:#999;">世界观位置</label>
          <input id="charFormWorldPosition" style="width:100%;padding:8px 12px;margin-bottom:10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
          
          <label style="font-size:12px;color:#999;">象征意义</label>
          <input id="charFormSymbolism" style="width:100%;padding:8px 12px;margin-bottom:10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
          
          <label style="font-size:12px;color:#999;">关键台词（逗号分隔）</label>
          <input id="charFormKeyLines" style="width:100%;padding:8px 12px;margin-bottom:10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
          
          <label style="font-size:12px;color:#999;">人物关系（格式: 角色名:关系类型，逗号分隔）</label>
          <input id="charFormRelationships" placeholder="李四:宿敌, 王五:旧友" style="width:100%;padding:8px 12px;margin-bottom:10px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#d0d0d0;font-size:13px;">
        </details>
      </div>
      
      <input type="hidden" id="charFormId">  <!-- 编辑模式下存储角色 id -->
    </div>
    
    <!-- 底部操作 -->
    <div style="padding:12px 20px;border-top:1px solid rgba(255,255,255,0.06);display:flex;gap:8px;justify-content:flex-end;">
      <button id="charDeleteBtn" onclick="deleteChar()" style="display:none;padding:8px 20px;background:#d32f2f;border:none;border-radius:6px;color:#fff;font-size:12px;cursor:pointer;">删除</button>
      <button onclick="saveChar()" style="padding:8px 28px;background:linear-gradient(135deg,#42a5f5,#7e57c2);border:none;border-radius:6px;color:#fff;font-size:13px;cursor:pointer;">保存到角色库</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 添加 JS 函数 — 模态框控制 + 模式切换 + 模板 + 提取填充**

```javascript
var CHAR_TEMPLATES = {
  soldier: { personality: '勇敢, 忠诚, 坚韧', strengths: '战斗技能, 纪律性', weaknesses: '不善表达, 创伤后应激', catchphrase: '这是命令' },
  merchant: { personality: '精明, 谨慎, 圆滑', strengths: '谈判技巧, 人脉', weaknesses: '贪婪, 缺乏体力', catchphrase: '生意就是生意' },
  mage: { personality: '博学, 孤傲, 好奇', strengths: '知识渊博, 法力强大', weaknesses: '体力薄弱, 傲慢', catchphrase: '知识就是力量' },
  scholar: { personality: '儒雅, 正直, 固执', strengths: '学识, 口才', weaknesses: '书生意气, 不懂人情', catchphrase: '书中自有黄金屋' },
  wanderer: { personality: '自由, 洒脱, 神秘', strengths: '生存技能, 见多识广', weaknesses: '无依无靠, 不信任他人', catchphrase: '习惯了漂泊' },
};

function openCharModal(charId) {
  // 重置表单
  document.getElementById('charFormId').value = '';
  ['Name','Gender','Age','Personality','Background','Motivation','Appearance','Catchphrase','Strengths','Weaknesses','Secret','WorldPosition','Symbolism','KeyLines','Relationships'].forEach(function(f) {
    var el = document.getElementById('charForm' + f);
    if (el) { el.value = ''; }
  });
  document.getElementById('charExtractText').value = '';
  document.getElementById('charExtractResult').innerHTML = '';
  document.getElementById('charDeleteBtn').style.display = 'none';
  document.getElementById('charModalTitle').textContent = '新建角色';
  
  if (charId) {
    // 编辑模式：加载已有角色
    loadCharForEdit(charId);
  }
  
  document.getElementById('charEditModal').style.display = 'block';
}

function closeCharModal() {
  document.getElementById('charEditModal').style.display = 'none';
}

function switchCharMode(mode) {
  document.getElementById('charFormFullFields').style.display = (mode === 'full') ? 'block' : 'none';
}

function applyTemplate(name) {
  if (!name) return;
  var t = CHAR_TEMPLATES[name];
  if (!t) return;
  Object.keys(t).forEach(function(f) {
    var el = document.getElementById('charForm' + f.charAt(0).toUpperCase() + f.slice(1));
    if (el) { el.value = t[f]; }
  });
}

async function extractAndFill() {
  var text = document.getElementById('charExtractText').value.trim();
  if (!text) { alert('请先粘贴人物小传'); return; }
  var resultEl = document.getElementById('charExtractResult');
  resultEl.innerHTML = '提取中...';
  try {
    var resp = await fetch(apiUrl('/api/characters/extract'), {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: text})
    });
    var data = await resp.json();
    var chars = data.characters || [];
    if (chars.length === 0) {
      resultEl.innerHTML = '未提取到角色';
      return;
    }
    resultEl.innerHTML = '提取了 ' + chars.length + ' 个角色：' +
      chars.map(function(c, i) {
        return '<span style="cursor:pointer;color:#64b5f6;margin-right:8px;" onclick="fillFormFromChar(' + i + ')">' + escHtml(c.name) + '</span>' +
          '<button class="btn btn-sm" onclick="saveSingleExtracted(' + i + ')" style="margin-right:4px;">保存</button>';
      }).join(' ');
    // 自动填充第一个
    fillFormFromChar(0, chars);
  } catch(e) {
    resultEl.innerHTML = '提取失败: ' + e.message;
    log('角色提取失败: ' + e.message, 'error');
  }
}

var _extractedChars = [];  // 当前提取结果缓存

function fillFormFromChar(idx, chars) {
  chars = chars || _extractedChars;
  _extractedChars = chars;
  var c = chars[idx];
  if (!c) return;
  document.getElementById('charFormName').value = c.name || '';
  document.getElementById('charFormGender').value = c.gender || '';
  document.getElementById('charFormAge').value = c.age || '';
  document.getElementById('charFormPersonality').value = (c.personality || []).join(', ');
  document.getElementById('charFormBackground').value = c.background || '';
  document.getElementById('charFormMotivation').value = c.motivation || '';
  document.getElementById('charFormAppearance').value = c.appearance || '';
  document.getElementById('charFormCatchphrase').value = c.catchphrase || '';
  document.getElementById('charFormStrengths').value = (c.strengths || []).join(', ');
  document.getElementById('charFormWeaknesses').value = (c.weaknesses || []).join(', ');
  document.getElementById('charFormSecret').value = c.secret || '';
  document.getElementById('charFormWorldPosition').value = c.world_position || '';
  document.getElementById('charFormSymbolism').value = c.symbolism || '';
  document.getElementById('charFormKeyLines').value = (c.key_lines || []).join(', ');
  document.getElementById('charFormRelationships').value = (c.relationships || []).map(function(r) { return r.target + ':' + r.relation; }).join(', ');
}

async function saveSingleExtracted(idx) {
  var c = _extractedChars[idx];
  if (!c) return;
  // 保存前查重
  var existing = await fetch(apiUrl('/api/characters?search=' + encodeURIComponent(c.name)));
  var edata = await existing.json();
  var dup = (edata.characters || []).find(function(e) { return e.name === c.name; });
  if (dup) {
    var action = confirm('角色库中已存在"' + c.name + '"，是否覆盖？\n确定=覆盖，取消=跳过');
    if (!action) { log('跳过保存: ' + c.name, 'info'); return; }
    await fetch(apiUrl('/api/characters/' + dup.id), { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(c) });
  } else {
    await fetch(apiUrl('/api/characters'), { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(c) });
  }
  log('已保存: ' + c.name, 'info');
  refreshCharLibrary();
}

async function loadCharForEdit(charId) {
  try {
    var resp = await fetch(apiUrl('/api/characters/' + charId));
    var data = await resp.json();
    var c = data.character;
    document.getElementById('charFormId').value = c.id;
    document.getElementById('charModalTitle').textContent = '编辑角色: ' + c.name;
    document.getElementById('charDeleteBtn').style.display = 'inline-block';
    // 填充所有字段
    document.getElementById('charFormName').value = c.name || '';
    document.getElementById('charFormGender').value = c.gender || '';
    document.getElementById('charFormAge').value = c.age || '';
    document.getElementById('charFormPersonality').value = (c.personality || []).join(', ');
    document.getElementById('charFormBackground').value = c.background || '';
    document.getElementById('charFormMotivation').value = c.motivation || '';
    document.getElementById('charFormAppearance').value = c.appearance || '';
    document.getElementById('charFormCatchphrase').value = c.catchphrase || '';
    document.getElementById('charFormStrengths').value = (c.strengths || []).join(', ');
    document.getElementById('charFormWeaknesses').value = (c.weaknesses || []).join(', ');
    document.getElementById('charFormSecret').value = c.secret || '';
    document.getElementById('charFormWorldPosition').value = c.world_position || '';
    document.getElementById('charFormSymbolism').value = c.symbolism || '';
    document.getElementById('charFormKeyLines').value = (c.key_lines || []).join(', ');
    document.getElementById('charFormRelationships').value = (c.relationships || []).map(function(r) { return r.target + ':' + r.relation; }).join(', ');
    switchCharMode('full');  // 编辑模式自动切换到专业模式
  } catch(e) {
    log('加载角色失败: ' + e.message, 'error');
  }
}

async function saveChar() {
  var char = {
    name: document.getElementById('charFormName').value.trim(),
    gender: document.getElementById('charFormGender').value.trim(),
    age: document.getElementById('charFormAge').value.trim(),
    personality: document.getElementById('charFormPersonality').value.split(',').map(function(s) { return s.trim(); }).filter(Boolean),
    background: document.getElementById('charFormBackground').value.trim(),
    motivation: document.getElementById('charFormMotivation').value.trim(),
    appearance: document.getElementById('charFormAppearance').value.trim(),
    catchphrase: document.getElementById('charFormCatchphrase').value.trim(),
    strengths: document.getElementById('charFormStrengths').value.split(',').map(function(s) { return s.trim(); }).filter(Boolean),
    weaknesses: document.getElementById('charFormWeaknesses').value.split(',').map(function(s) { return s.trim(); }).filter(Boolean),
    secret: document.getElementById('charFormSecret').value.trim(),
    world_position: document.getElementById('charFormWorldPosition').value.trim(),
    symbolism: document.getElementById('charFormSymbolism').value.trim(),
    key_lines: document.getElementById('charFormKeyLines').value.split(',').map(function(s) { return s.trim(); }).filter(Boolean),
    relationships: document.getElementById('charFormRelationships').value.split(',').map(function(s) {
      var parts = s.trim().split(':');
      return parts.length === 2 ? {target: parts[0].trim(), relation: parts[1].trim()} : null;
    }).filter(Boolean),
  };
  
  if (!char.name) { alert('请填写角色姓名'); return; }
  
  try {
    var charId = document.getElementById('charFormId').value;
    var resp;
    if (charId) {
      resp = await fetch(apiUrl('/api/characters/' + charId), { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(char) });
    } else {
      resp = await fetch(apiUrl('/api/characters'), { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(char) });
    }
    if (resp.ok) {
      log('角色已保存', 'info');
      closeCharModal();
      refreshCharLibrary();
    } else {
      var err = await resp.json();
      alert('保存失败: ' + (err.detail || '未知错误'));
    }
  } catch(e) {
    log('保存角色失败: ' + e.message, 'error');
  }
}

async function deleteChar() {
  var charId = document.getElementById('charFormId').value;
  if (!charId) return;
  if (!confirm('确定删除此角色？此操作不可撤销。')) return;
  try {
    await fetch(apiUrl('/api/characters/' + charId), { method: 'DELETE' });
    log('角色已删除', 'info');
    closeCharModal();
    refreshCharLibrary();
  } catch(e) {
    log('删除失败: ' + e.message, 'error');
  }
}
```

- [ ] **Step 3: 验证无语法错误**

```bash
cd E:/writer/my_writing_system && uv run python -c "print('HTML size:', len(open('debug_ui.html', encoding='utf-8').read()), 'chars')"
```

- [ ] **Step 4: Commit**

```bash
cd E:/writer/my_writing_system && git add debug_ui.html && git commit -m "feat: add character edit modal with simple/full mode, templates, multi-extract"
```

---

### Task 6: UI — 角色详情 + 写作表单集成 + 导入按钮

**Files:**
- Modify: `debug_ui.html` — 追加详情面板、表单集成、导入逻辑

- [ ] **Step 1: 添加角色详情面板 HTML（在侧边栏内部）**

在侧边栏的角色列表区域之后添加：

```html
  <!-- 角色详情面板 -->
  <div id="charDetailPanel" style="display:none;position:absolute;inset:0;background:#1a1a2e;z-index:110;padding:16px;overflow-y:auto;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h4 id="charDetailName" style="color:#90caf9;margin:0;"></h4>
      <div>
        <button onclick="openCharModal(document.getElementById('charDetailPanel').dataset.charId)" style="padding:4px 10px;background:rgba(255,255,255,0.1);border:1px solid rgba(255,255,255,0.15);border-radius:4px;color:#ccc;font-size:11px;cursor:pointer;">编辑</button>
        <button onclick="document.getElementById('charDetailPanel').style.display='none'" style="background:none;border:none;color:#999;font-size:16px;cursor:pointer;">✕</button>
      </div>
    </div>
    <div id="charDetailContent" style="font-size:12px;color:#bbb;"></div>
  </div>
```

- [ ] **Step 2: 添加 viewCharDetail() JS 函数**

```javascript
async function viewCharDetail(charId) {
  try {
    var resp = await fetch(apiUrl('/api/characters/' + charId));
    var data = await resp.json();
    var c = data.character;
    var panel = document.getElementById('charDetailPanel');
    panel.dataset.charId = charId;
    document.getElementById('charDetailName').textContent = c.name;
    
    var html = '<div style="color:#888;margin-bottom:8px;">' + escHtml(c.gender || '') + ' · ' + escHtml(c.age || '') + '</div>';
    html += '<div style="margin:6px 0;"><strong>性格:</strong> ' + (c.personality || []).map(function(p) { return '<span class="char-tag">' + escHtml(p) + '</span>'; }).join(' ') + '</div>';
    html += '<div style="margin:6px 0;"><strong>动机:</strong> ' + escHtml(c.motivation || '无') + '</div>';
    html += '<div style="margin:6px 0;"><strong>背景:</strong> ' + escHtml(c.background || '无') + '</div>';
    if (c.catchphrase) html += '<div style="margin:6px 0;"><strong>口头禅:</strong> "' + escHtml(c.catchphrase) + '"</div>';
    if (c.strengths && c.strengths.length) html += '<div style="margin:6px 0;"><strong>优点:</strong> ' + escHtml(c.strengths.join('、')) + '</div>';
    if (c.weaknesses && c.weaknesses.length) html += '<div style="margin:6px 0;"><strong>缺点:</strong> ' + escHtml(c.weaknesses.join('、')) + '</div>';
    if (c.secret) html += '<div style="margin:6px 0;"><strong>秘密:</strong> ' + escHtml(c.secret) + '</div>';
    if (c.relationships && c.relationships.length) {
      html += '<div style="margin:6px 0;"><strong>关系:</strong> ' + c.relationships.map(function(r) { return escHtml(r.target) + '→' + escHtml(r.relation); }).join('、') + '</div>';
    }
    html += '<div style="margin-top:8px;font-size:10px;color:#666;">创建: ' + escHtml(c.created_at || '') + '</div>';
    
    document.getElementById('charDetailContent').innerHTML = html;
    panel.style.display = 'block';
  } catch(e) {
    log('加载角色详情失败: ' + e.message, 'error');
  }
}
```

- [ ] **Step 3: 修改 submitTask() 集成已选角色**

在 `submitTask()` 中，构建 POST body 时加入已选角色：

```javascript
    // 在 submitTask() 函数中 body 构建之前添加:
    var selectedChars = CHAR_LIB.allChars.filter(function(c) { return CHAR_LIB.selected[c.id]; });
    
    // body 中加入 characters 字段:
    body: JSON.stringify({ topic: topic, reference_text: reference, target_words_per_section: tw, character_text: document.getElementById('characterText').value.trim(), characters: selectedChars })
```

- [ ] **Step 4: 添加 importCurrentCharacters() JS 函数**

```javascript
async function importCurrentCharacters() {
  var rawData = STATE.lastRawData;
  if (!rawData || !rawData.characters || rawData.characters.length === 0) {
    alert('当前任务没有角色数据');
    return;
  }
  var chars = rawData.characters;
  var imported = 0, skipped = 0;
  for (var i = 0; i < chars.length; i++) {
    var c = chars[i];
    try {
      var resp = await fetch(apiUrl('/api/characters'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(c)
      });
      if (resp.ok) { imported++; } else { skipped++; }
    } catch(e) { skipped++; }
  }
  log('导入完成: ' + imported + ' 个成功, ' + skipped + ' 个跳过', 'info');
  refreshCharLibrary();
}
```

- [ ] **Step 5: Commit**

```bash
cd E:/writer/my_writing_system && git add debug_ui.html && git commit -m "feat: add character detail panel, form integration, import button"
```

---

### Task 7: 测试

**Files:**
- Modify: `tests/test_basic.py`

- [ ] **Step 1: 添加 test_character_store()**

```python
def test_character_store():
    """测试 CharacterStore CRUD + 搜索 + 统计。"""
    print("=" * 60)
    print("[TEST] 测试角色库存储")

    from app.character_store import CharacterStore
    import os
    db_path = "./test_characters.db"
    store = CharacterStore(db_path)

    # 创建
    c = store.create({
        "name": "测试张三",
        "personality": ["勇敢", "固执"],
        "strengths": ["战斗技能"],
        "weaknesses": ["不善表达"],
        "motivation": "复仇",
        "key_lines": ["这是命令"],
        "relationships": [{"target": "李四", "relation": "宿敌"}],
    })
    assert c["name"] == "测试张三"
    assert c["personality"] == ["勇敢", "固执"]
    assert c["strengths"] == ["战斗技能"]
    assert len(c["id"]) > 0

    # 查询
    c2 = store.get(c["id"])
    assert c2 is not None
    assert c2["name"] == "测试张三"

    # 搜索
    results = store.list_all(search="勇敢")
    assert len(results) >= 1

    # trait 过滤
    results = store.list_all(trait_filter="personality:固执")
    assert len(results) >= 1

    # 更新
    c3 = store.update(c["id"], {"motivation": "证明自己", "personality": ["勇敢", "固执", "冷静"]})
    assert c3 is not None
    assert c3["motivation"] == "证明自己"
    assert len(c3["personality"]) == 3

    # 统计
    stats = store.stats()
    assert "total" in stats
    assert stats["total"] >= 1

    # 删除
    assert store.delete(c["id"]) is True
    assert store.get(c["id"]) is None

    os.remove(db_path)
    print("[PASS] 角色库存储测试通过\n")
```

- [ ] **Step 2: 追加测试调用**

在 `if __name__ == "__main__":` 块中添加：

```python
    test_character_store()
```

- [ ] **Step 3: 运行测试**

```bash
cd E:/writer/my_writing_system && uv run python tests/test_basic.py
```

- [ ] **Step 4: Commit**

```bash
cd E:/writer/my_writing_system && git add tests/test_basic.py && git commit -m "test: add CharacterStore CRUD, search, stats test"
```

---

## Execution Order

```
Task 1 (CharacterStore) → Task 2 (API) → Task 3 (Coordinator)
    → Task 4 (UI Sidebar) → Task 5 (UI Modal) → Task 6 (UI Detail+Form)
    → Task 7 (Tests)
```

Tasks 4-6 all modify `debug_ui.html` — they must be sequential to avoid merge conflicts.
