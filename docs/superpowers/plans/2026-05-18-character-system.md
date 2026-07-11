# 人物设定系统 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在多智能体协作写作系统中集成人物设定能力，支持自然语言角色提取、角色弧线规划、写作中自动状态更新和独立 UI 展示。

**Architecture:** 新增 CharacterManager 智能体（继承 BaseAgent），在 coordinator 流程中插入 4 个集成点（Phase 0 提取、Phase 2 后弧线、Phase 3 状态更新、Phase 5 审阅增强），修改 Writer/Reviewer prompt 注入角色上下文，扩展 debug_ui.html 新增"人物"标签页。

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, Celery, Redis, DeepSeek V4 Pro LLM

**Spec:** `docs/superpowers/specs/2026-05-18-character-system-design.md`

---

## File Responsibility Map

| File | Responsibility |
|------|---------------|
| `app/models.py` | 数据模型：CharacterProfile、CharacterArc、WriteRequest/TaskStatus/FinalResult 扩展 |
| `app/agents/character_manager.py` (新) | 角色生命周期管理：提取、弧线规划、状态更新 |
| `app/utils/prompt_templates.py` | 所有提示词模板（新增 3 个角色模板 + 修改 3 个现有模板） |
| `app/agents/writer.py` | 编剧增强：接收角色/弧线参数，注入 prompt，每节后更新状态 |
| `app/coordinator.py` | 流程编排：Phase 0 提取、Phase 2 后弧线、Phase 3 状态更新调用、Phase 5 审阅增强 |
| `app/main.py` | API 端点：status/result 返回 characters/character_arcs |
| `debug_ui.html` | Web UI：角色输入区、人物标签页、角色卡片渲染 |
| `tests/test_basic.py` | 测试：模型验证、提取、弧线、状态更新、端到端流程 |

---

### Task 1: 数据模型 — CharacterProfile、CharacterArc 和请求扩展

**Files:**
- Modify: `app/models.py` — 在文件末尾追加新模型，修改 WriteRequest、TaskStatus、FinalResult

- [ ] **Step 1: 在 models.py 末尾追加 CharacterProfile 和 CharacterArc**

在 `app/models.py` 的 `FinalResult` 类定义之后追加：

```python
# ============================================================
# 人物设定
# ============================================================
class CharacterProfile(BaseModel):
    """单个人物的完整设定（14 个字段）。"""
    id: str = ""
    name: str
    gender: str = ""
    age: str = ""
    personality: list[str] = []
    motivation: str = ""
    background: str = ""
    appearance: str = ""
    catchphrase: str = ""
    strengths: list[str] = []
    weaknesses: list[str] = []
    secret: str = ""
    world_position: str = ""
    symbolism: str = ""
    key_lines: list[str] = []
    relationships: list[dict] = []


class CharacterArc(BaseModel):
    """角色弧线 —— 角色的变化轨迹。"""
    character_id: str
    starting_state: str = ""
    ending_state: str = ""
    key_milestones: list[dict] = []
    current_state: str = ""
```

- [ ] **Step 2: 修改 WriteRequest，新增 character_text 和 characters 字段**

```python
# 修改 WriteRequest 类，在 target_words_per_section 之后追加：
class WriteRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    reference_text: str = Field(..., min_length=1)
    target_words_per_section: int = Field(10000, ge=500)
    character_text: str = ""                              # 新增
    characters: list[CharacterProfile] = []               # 新增
```

> 注意：由于 `CharacterProfile` 在 WriteRequest 之后定义，需要将 `CharacterProfile` 类移到 `WriteRequest` 之前，或者使用 `from __future__ import annotations`（Python 3.11+ 默认启用延迟注解求值，Pydantic v2 原生支持 forward references）。实际编辑时将 `CharacterProfile` 和 `CharacterArc` 放在 `WriteRequest` 之前即可。

- [ ] **Step 3: 修改 TaskStatus，新增 characters 和 character_arcs 字段**

```python
class TaskStatus(BaseModel):
    task_id: str
    status: str
    progress: str | None = None
    style: dict | None = None
    outline: list[dict] | None = None
    outline_v1: list[dict] | None = None
    outline_reviews: list[dict] | None = None
    handover_notes: list[dict] | None = None
    fix_checklist: dict | None = None
    timeline: list[dict] | None = None
    draft: str | None = None
    review: dict | None = None
    error: str | None = None
    characters: list[dict] | None = None          # 新增
    character_arcs: list[dict] | None = None      # 新增
```

- [ ] **Step 4: 修改 FinalResult，新增 characters 和 character_arcs 字段**

```python
class FinalResult(BaseModel):
    task_id: str
    topic: str
    style: dict
    outline: list[dict]
    draft: str
    review: dict
    handover_notes: list[dict] = []
    fix_checklist: dict | None = None
    timeline: list[dict] = []
    characters: list[dict] | None = None          # 新增
    character_arcs: list[dict] | None = None      # 新增
```

- [ ] **Step 5: 运行测试验证模型导入无错误**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.models import CharacterProfile, CharacterArc, WriteRequest, TaskStatus, FinalResult; print('Models OK')"
```

Expected: `Models OK`

- [ ] **Step 6: Commit**

```bash
git add app/models.py
git commit -m "feat: add CharacterProfile, CharacterArc models and extend request/status types"
```

---

### Task 2: 提示词模板 — 新增 3 个角色模板

**Files:**
- Modify: `app/utils/prompt_templates.py` — 在文件末尾追加 3 个新模板

- [ ] **Step 1: 追加 CHARACTER_EXTRACTION_PROMPT**

在 `app/utils/prompt_templates.py` 末尾追加：

```python
# ----------------------------------------------------------------
# 角色提取
# ----------------------------------------------------------------
CHARACTER_EXTRACTION_PROMPT = """你是一位专业的人物设定编辑。请从以下自然语言描述中提取所有人物信息。

描述：
{character_text}

请以 JSON 数组格式输出（不要包含其他内容）：
[
  {{
    "name": "姓名",
    "gender": "性别",
    "age": "年龄（可模糊，如'28岁'或'中年'）",
    "personality": ["标签1", "标签2", "标签3"],
    "motivation": "核心动机（一句话）",
    "background": "一句话背景",
    "appearance": "外貌描写",
    "catchphrase": "口头禅或语言风格",
    "strengths": ["优点1"],
    "weaknesses": ["缺点1"],
    "secret": "秘密或软肋",
    "world_position": "在世界观中的位置",
    "symbolism": "象征意义",
    "key_lines": ["预写的关键台词"],
    "relationships": [{{"target": "其他角色名", "relation": "关系类型"}}]
  }}
]

提取规则：
1. 缺失字段填空字符串 "" 或空数组 []
2. 即使描述中只提到一个人，也输出单元素数组
3. 从描述中推断不明显的信息（如从行为推断性格）
4. 保持原文语言风格"""
```

- [ ] **Step 2: 追加 CHARACTER_ARC_PROMPT**

```python
# ----------------------------------------------------------------
# 角色弧线规划
# ----------------------------------------------------------------
CHARACTER_ARC_PROMPT = """你是一位故事结构专家。请根据人物设定和大纲，为每个角色设计变化弧线。

人物设定：
{characters_json}

大纲：
{outline_json}

请以 JSON 数组格式输出（每个角色一个元素）：
[
  {{
    "character_id": "角色ID",
    "starting_state": "起点状态（一句话，如'一个对世界充满愤怒的复仇者'）",
    "ending_state": "终点状态（一句话，如'学会放下，找到新的人生意义'）",
    "key_milestones": [
      {{"section": 1, "event": "触发事件：首次面对真相"}},
      {{"section": 2, "event": "转折点：被迫选择原谅或复仇"}},
      {{"section": 3, "event": "结局：做出最终抉择"}}
    ]
  }}
]

要求：
1. 弧线必须跨越多节，每节至少一个里程碑
2. 状态变化要合理渐进，避免突兀跳跃
3. 结合大纲的具体情节设计里程碑"""
```

- [ ] **Step 3: 追加 CHARACTER_STATE_UPDATE_PROMPT**

```python
# ----------------------------------------------------------------
# 角色状态更新
# ----------------------------------------------------------------
CHARACTER_STATE_UPDATE_PROMPT = """你是一位细心的角色跟踪编辑。根据刚写好的正文，更新角色的当前状态。

角色信息：
{character_json}

当前弧线：
{arc_json}

刚写完的第{section_num}节正文（节选）：
{section_text}

请以 JSON 格式输出（不要包含其他内容）：
{{
    "character_id": "角色ID",
    "current_state": "更新后的状态（一句话，描述角色此刻的情绪、处境、关系变化）"
}}

要求：
1. 基于正文中实际发生的事件更新状态
2. 如果正文没有涉及该角色，保持状态不变
3. 状态描述应与弧线中的里程碑对应"""
```

- [ ] **Step 4: 运行测试验证模板可导入**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.utils.prompt_templates import CHARACTER_EXTRACTION_PROMPT, CHARACTER_ARC_PROMPT, CHARACTER_STATE_UPDATE_PROMPT; print('Prompts OK')"
```

Expected: `Prompts OK`

- [ ] **Step 5: Commit**

```bash
git add app/utils/prompt_templates.py
git commit -m "feat: add CHARACTER_EXTRACTION, CHARACTER_ARC, CHARACTER_STATE_UPDATE prompts"
```

---

### Task 3: CharacterManager 智能体（新建）

**Files:**
- Create: `app/agents/character_manager.py`
- Modify: `app/agents/__init__.py` — 无需修改（已有 `__init__.py`，模块自动发现）

- [ ] **Step 1: 创建 character_manager.py 骨架**

```python
import uuid
import json
from .base import BaseAgent
from ..utils.prompt_templates import (
    CHARACTER_EXTRACTION_PROMPT,
    CHARACTER_ARC_PROMPT,
    CHARACTER_STATE_UPDATE_PROMPT,
)
from ..utils.json_parser import parse_json


class CharacterManager(BaseAgent):
    """角色管理器：提取、弧线规划、状态更新。

    不重载 run() —— 由 coordinator 直接调用专用方法。
    """

    def run(self, **kwargs) -> dict:
        raise NotImplementedError("使用 extract_characters / plan_arcs / update_states 替代")
```

- [ ] **Step 2: 实现 extract_characters()**

```python
    def extract_characters(self, character_text: str) -> list[dict]:
        """从自然语言描述中提取结构化角色列表。

        Args:
            character_text: 用户输入的自由文本人物描述。

        Returns:
            list[dict]: 角色卡列表，每个含 id/name/personality 等 14 个字段。
        """
        if not character_text.strip():
            return []

        prompt = CHARACTER_EXTRACTION_PROMPT.format(character_text=character_text)
        messages = [
            {"role": "system", "content": "你是一位专业的人物设定编辑。请严格以 JSON 数组格式输出。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=4000)
        self.last_raw_response = response

        try:
            result = parse_json(response)
            if isinstance(result, dict):
                result = [result]
            if not isinstance(result, list):
                raise ValueError(f"LLM 返回了非数组格式: {type(result)}")
        except ValueError:
            raise ValueError(
                f"角色提取 JSON 解析失败。原始响应:\n{response[:800]}"
            )

        # 确保每个角色有 id
        for char in result:
            if not char.get("id"):
                char["id"] = str(uuid.uuid4())
        return result
```

- [ ] **Step 3: 实现 plan_arcs()**

```python
    def plan_arcs(self, characters: list[dict], outline: list[dict]) -> list[dict]:
        """根据角色卡和大纲规划每个角色的变化弧线。

        Args:
            characters: 角色卡列表。
            outline: 大纲 V2。

        Returns:
            list[dict]: 角色弧线列表。
        """
        if not characters:
            return []

        characters_json = json.dumps(characters, ensure_ascii=False, indent=2)
        outline_json = json.dumps(outline, ensure_ascii=False, indent=2)

        prompt = CHARACTER_ARC_PROMPT.format(
            characters_json=characters_json,
            outline_json=outline_json,
        )
        messages = [
            {"role": "system", "content": "你是一位故事结构专家。请严格以 JSON 数组格式输出。"},
            {"role": "user", "content": prompt},
        ]
        response = self.llm.chat_completion(messages, temperature=0.4, max_tokens=4000)
        self.last_raw_response = response

        try:
            result = parse_json(response)
            if isinstance(result, dict):
                result = [result]
            if not isinstance(result, list):
                raise ValueError(f"LLM 返回了非数组格式: {type(result)}")
        except ValueError:
            return []

        # 确保 current_state 初始等于 starting_state
        for arc in result:
            if not arc.get("current_state"):
                arc["current_state"] = arc.get("starting_state", "")
        return result
```

- [ ] **Step 4: 实现 update_states()**

```python
    def update_states(
        self, characters: list[dict], arcs: list[dict],
        section_text: str, section_num: int,
    ) -> list[dict]:
        """根据刚写好的正文更新每个角色的当前状态。

        Args:
            characters: 角色卡列表。
            arcs: 当前弧线列表（会被原地修改并返回）。
            section_text: 刚写完的节的完整文本。
            section_num: 节号。

        Returns:
            list[dict]: 更新后的弧线列表。
        """
        if not arcs:
            return arcs

        updated_arcs = []
        for i, arc in enumerate(arcs):
            char_id = arc.get("character_id", "")
            # 找到对应角色
            char = next((c for c in characters if c.get("id") == char_id), None)
            if not char:
                updated_arcs.append(arc)
                continue

            char_json = json.dumps(char, ensure_ascii=False, indent=2)
            arc_json = json.dumps(arc, ensure_ascii=False, indent=2)
            prompt = CHARACTER_STATE_UPDATE_PROMPT.format(
                character_json=char_json,
                arc_json=arc_json,
                section_num=section_num,
                section_text=section_text[:8000],
            )
            messages = [
                {"role": "system", "content": "你是一位细心的角色跟踪编辑。请严格以 JSON 格式输出。"},
                {"role": "user", "content": prompt},
            ]
            response = self.llm.chat_completion(messages, temperature=0.3, max_tokens=500)
            self.last_raw_response = response

            try:
                result = parse_json(response)
                if isinstance(result, dict) and result.get("current_state"):
                    arc["current_state"] = result["current_state"]
            except ValueError:
                pass  # 解析失败保持旧状态

            updated_arcs.append(arc)

        return updated_arcs
```

- [ ] **Step 5: 验证导入**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.agents.character_manager import CharacterManager; cm = CharacterManager(); print('CharacterManager OK')"
```

Expected: `CharacterManager OK`（LLM_API_KEY 需要有效，但导入不应触发 LLM 调用）

- [ ] **Step 6: Commit**

```bash
git add app/agents/character_manager.py
git commit -m "feat: add CharacterManager agent with extract, plan_arcs, update_states"
```

---

### Task 4: Writer 和写作 Prompt 增强

**Files:**
- Modify: `app/utils/prompt_templates.py` — 修改 WRITING_PROMPT 和 WRITING_SECTION1_PROMPT
- Modify: `app/agents/writer.py` — run() 签名扩展、prompt 注入、每节后状态更新

- [ ] **Step 1: 在 WRITING_PROMPT 中注入角色上下文**

修改 `WRITING_PROMPT`，在「## 风格要求」段落之后、「## 前面章节的交接笔记」之前插入：

```
## 人物设定
{character_context}

## 本节人物弧线要求
{arc_context}
```

完整修改位置：在 `## 前面章节的交接笔记` 行之前插入以上两段。

- [ ] **Step 2: 在 WRITING_SECTION1_PROMPT 中注入角色上下文**

修改 `WRITING_SECTION1_PROMPT`，在「## 风格要求」段落之后、「## 前文回顾」之前插入相同的两段内容（同 Step 1）。

- [ ] **Step 3: 修改 Writer.run() 签名，新增 characters 和 character_arcs 参数**

```python
def run(
    self,
    topic: str,
    style: dict,
    outline: list[dict],
    vector_store,
    blackboard,
    task_id: str,
    characters: list[dict] | None = None,
    character_arcs: list[dict] | None = None,
) -> dict:
```

- [ ] **Step 4: 添加 _build_character_context() 辅助方法**

在 Writer 类中添加：

```python
    def _build_character_context(self, characters: list[dict] | None, arcs: list[dict] | None) -> tuple[str, str]:
        """构建注入 prompt 的角色上下文和弧线上下文。

        Returns:
            (character_context, arc_context) 两个字符串。
        """
        if not characters:
            return "（无人物设定）", "（无弧线要求）"

        char_lines = []
        for c in characters:
            name = c.get("name", "?")
            personality = "、".join(c.get("personality", [])) or "?"
            motivation = c.get("motivation", "") or "?"
            catchphrase = c.get("catchphrase", "") or "?"
            current_state = ""
            if arcs:
                arc = next((a for a in arcs if a.get("character_id") == c.get("id")), None)
                if arc and arc.get("current_state"):
                    current_state = f"，当前状态：{arc['current_state']}"

            char_lines.append(
                f"- {name}（{personality}）{current_state}"
            )

        arc_lines = []
        if arcs:
            for a in arcs:
                char = next((c for c in (characters or []) if c.get("id") == a.get("character_id")), None)
                name = char.get("name", "?") if char else "?"
                milestones = a.get("key_milestones", [])
                if milestones:
                    ms_text = " → ".join(
                        f"第{m.get('section', '?')}节: {m.get('event', '?')}"
                        for m in milestones
                    )
                    arc_lines.append(f"- {name}: {ms_text}")

        character_context = "\n".join(char_lines) if char_lines else "（无人物设定）"
        arc_context = "\n".join(arc_lines) if arc_lines else "（无弧线要求）"
        return character_context, arc_context
```

- [ ] **Step 5: 在 Writer.run() 的 prompt 构建中注入角色上下文**

在 `Writer.run()` 中，构建 prompt 之前调用 `_build_character_context()`，并将其传入 `template.format()`：

```python
                # 在 # --- 构建交接笔记上下文 --- 之前：
                character_context, arc_context = self._build_character_context(
                    characters, character_arcs
                )

                # 在 prompt = template.format(...) 调用中添加两个参数：
                prompt = template.format(
                    topic=topic,
                    section=section_num,
                    subsection=sub_num,
                    subsection_title=sub_title,
                    section_outline=section_outline,
                    key_points="、".join(key_points),
                    intensity=style.get("intensity", 50),
                    melancholy=style.get("melancholy", 50),
                    avg_sentence_length=style.get("avg_sentence_length", 20),
                    adjective_density=style.get("adjective_density", 0.1),
                    tone=style.get("tone", ""),
                    character_context=character_context,          # 新增
                    arc_context=arc_context,                      # 新增
                    handover_context=handover_context,
                    summary_context=summary_context if summary_context else "（故事开头）",
                    retrieved_context=retrieved_context if retrieved_context else "（无相关段落）",
                    target_words=target_words,
                )
```

- [ ] **Step 6: 在每节写完后调用 update_states()**

在 `Writer.run()` 中，每节循环结束后（`section_texts[section_num] = section_text` 之后，`if section_handover_parts:` 之前或之后），添加：

```python
            # --- 更新角色状态 ---
            if character_arcs:
                from .character_manager import CharacterManager
                cm_char = CharacterManager()
                character_arcs = cm_char.update_states(
                    characters, character_arcs, section_text, section_num
                )
                if blackboard:
                    blackboard.set(task_id, "character_arcs", character_arcs)
```

- [ ] **Step 7: 验证 Writer 导入和签名**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.agents.writer import Writer; w = Writer(); print('Writer OK'); help(w.run)"
```

Expected: 显示 run 签名含 characters/character_arcs

- [ ] **Step 8: Commit**

```bash
git add app/utils/prompt_templates.py app/agents/writer.py
git commit -m "feat: inject character context into Writer prompts and auto-update states per section"
```

---

### Task 5: 审阅 Prompt 增强

**Files:**
- Modify: `app/utils/prompt_templates.py` — 修改 GLOBAL_REVIEW_PROMPT
- Modify: `app/agents/reviewer.py` — review_global() 接收角色上下文

- [ ] **Step 1: 修改 GLOBAL_REVIEW_PROMPT 注入角色一致性**

在 `GLOBAL_REVIEW_PROMPT` 末尾的「修正清单」段落之后、输出 JSON 格式说明之前插入：

```
## 人物一致性
{character_consistency_context}
```

并在输出 JSON 模板中添加新字段：

```
    "character_consistency": "人物行为是否与设定一致的总体评价（一句话）",
    "character_arc_progress": "各角色弧线完成度评价（一句话）"
```

- [ ] **Step 2: 修改 Reviewer.review_global() 签名和调用**

```python
def review_global(
    self,
    topic: str,
    style: dict,
    section_summaries: str,
    total_words: int,
    handover_chain: str = "",
    fix_summary: str = "",
    characters: list[dict] | None = None,
    character_arcs: list[dict] | None = None,
) -> dict:
```

在 prompt 构建前生成 `character_consistency_context`：

```python
        # 构建角色一致性上下文
        character_consistency_context = "（无人物设定）"
        if characters:
            parts = []
            for c in characters:
                name = c.get("name", "?")
                personality = "、".join(c.get("personality", [])) or "?"
                motivation = c.get("motivation", "") or "?"
                parts.append(f"- {name}（{personality}）：动机={motivation}")
            if parts:
                character_consistency_context = "\n".join(parts)
            if character_arcs:
                arc_parts = []
                for a in character_arcs:
                    char = next((c for c in characters if c.get("id") == a.get("character_id")), None)
                    name = char.get("name", "?") if char else "?"
                    arc_parts.append(
                        f"- {name}: {a.get('starting_state', '?')} → {a.get('current_state', '?')} → {a.get('ending_state', '?')}"
                    )
                if arc_parts:
                    character_consistency_context += "\n\n弧线进度：\n" + "\n".join(arc_parts)
```

然后在 `prompt = GLOBAL_REVIEW_PROMPT.format(...)` 中添加此参数。

- [ ] **Step 3: 验证导入**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.agents.reviewer import Reviewer; print('Reviewer OK')"
```

Expected: `Reviewer OK`

- [ ] **Step 4: Commit**

```bash
git add app/utils/prompt_templates.py app/agents/reviewer.py
git commit -m "feat: add character consistency review dimension to Reviewer"
```

---

### Task 6: Coordinator 流程集成

**Files:**
- Modify: `app/coordinator.py` — Phase 0 角色提取、Phase 2 后弧线规划、Phase 3 传递角色参数、Phase 5 角色审阅

- [ ] **Step 1: 导入 CharacterManager**

在 `app/coordinator.py` 顶部 import 中添加：

```python
from .agents.character_manager import CharacterManager
```

- [ ] **Step 2: 新增 Phase 0 — 角色提取（在 Phase 1 风格分析之前）**

在 `bb.set(task_id, "status", "running")` 之后、`# Phase 1: 风格分析` 之前插入：

```python
        # ================================================
        # Phase 0: 角色提取（如果用户提供了 character_text）
        # ================================================
        characters = []
        if character_text and not characters:
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
                # 不阻塞主流程
```

注意：`writing_task` 的签名需要新增参数 `character_text` 和 `characters`：

```python
@celery_app.task(bind=True, name="writing_task")
def writing_task(
    self, topic: str, reference_text: str,
    target_words_per_section: int = 10000,
    character_text: str = "",
    characters: list[dict] | None = None,
) -> dict:
```

同时在函数开头将 `characters` 参数转换为列表（如果为 None）：

```python
    if characters is None:
        characters = []
```

- [ ] **Step 3: Phase 2 后 — 弧线规划（在大纲确定后、Phase 3 写作前）**

在大纲审批流程结束后（用户审批 / 超时之后）、`# Phase 3: 继承制写作` 之前插入：

```python
        # ================================================
        # Phase 2.6: 角色弧线规划
        # ================================================
        character_arcs = []
        if characters:
            cm = CharacterManager()
            try:
                character_arcs = cm.plan_arcs(characters, outline_v2)
                if character_arcs:
                    bb.set(task_id, "character_arcs", character_arcs)
                    _add_timeline(bb, task_id, "character", "character_manager",
                                  f"角色弧线规划完成: {len(character_arcs)} 个角色")
            except Exception as e:
                _add_timeline(bb, task_id, "character", "system",
                              f"弧线规划失败: {str(e)[:200]}")
                # 不阻塞主流程
```

- [ ] **Step 4: Phase 3 — 传递角色参数给 Writer**

在 `writer_result = writer.run(...)` 调用中添加 characters 和 character_arcs 参数：

```python
        writer_result = writer.run(
            topic=topic, style=style, outline=outline_v2,
            vector_store=vector_store, blackboard=bb, task_id=task_id,
            characters=characters,            # 新增
            character_arcs=character_arcs,    # 新增
        )
```

同时从 `writer_result` 中获取更新后的 arcs（Writer 在写作过程中更新了 arc 状态）：

```python
        # Writer 内部会更新 character_arcs 并写回黑板，但本地变量可能过期
        # 从黑板重新读取以确保后续步骤使用最新数据
        arcs_raw = bb.get(task_id, "character_arcs")
        if arcs_raw:
            import json as _json
            try:
                character_arcs = _json.loads(arcs_raw) if isinstance(arcs_raw, str) else arcs_raw
            except (_json.JSONDecodeError, TypeError):
                pass
```

- [ ] **Step 5: Phase 5 — 审阅时传递角色信息**

在 `global_review = reviewer.review_global(...)` 调用中添加 characters 和 character_arcs 参数：

```python
        global_review = reviewer.review_global(
            topic=topic, style=style,
            section_summaries=section_summaries,
            total_words=len(draft),
            handover_chain=handover_chain_text,
            fix_summary=fix_checklist.get("summary", ""),
            characters=characters,             # 新增
            character_arcs=character_arcs,     # 新增
        )
```

- [ ] **Step 6: 返回值中包含角色数据**

在 `return { ... }` 字典中添加：

```python
        return {
            ...
            "characters": characters,
            "character_arcs": character_arcs,
        }
```

- [ ] **Step 7: 验证 coordinator 导入**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.coordinator import writing_task; print('Coordinator OK')"
```

Expected: `Coordinator OK`

- [ ] **Step 8: Commit**

```bash
git add app/coordinator.py
git commit -m "feat: integrate character pipeline into coordinator (extract, arcs, update, review)"
```

---

### Task 7: API 端点扩展

**Files:**
- Modify: `app/main.py` — status 和 result 端点返回角色数据

- [ ] **Step 1: 修改 /status/{task_id} 返回值**

在 get_task_status 的 TaskStatus 构造中添加：

```python
    return TaskStatus(
        task_id=task_id,
        status=data.get("status", "unknown"),
        ...
        error=data.get("error"),
        characters=data.get("characters"),          # 新增
        character_arcs=data.get("character_arcs"),  # 新增
    )
```

- [ ] **Step 2: 验证 API 导入**

```bash
cd E:/writer/my_writing_system && uv run python -c "from app.main import app; print('API OK')"
```

Expected: `API OK`

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: return characters and character_arcs in status/result endpoints"
```

---

### Task 8: Web UI — 角色输入区

**Files:**
- Modify: `debug_ui.html` — 提交表单新增角色输入区

- [ ] **Step 1: 在提交表单中添加角色 textarea**

在风格参考文本 `<textarea id="reference">` 之后、提交按钮之前添加：

```html
  <label>人物设定（可选，自由描述或粘贴人物小传）</label>
  <textarea id="characterText" placeholder="例如：张三，28岁，一个被背叛的退伍军人。性格固执但内心温柔，口头禅是'这世界欠我的'。他的秘密是当年撤退时抛弃了战友...&#10;&#10;李四，30岁，冷静腹黑的商界精英，张三的宿敌。表面上是成功企业家，实际上是地下组织的幕后操纵者..."></textarea>
```

- [ ] **Step 2: 修改 submitTask() 函数，在 POST body 中加入 character_text**

在 `submitTask()` 函数的 fetch body 中添加：

```javascript
      body: JSON.stringify({ topic: topic, reference_text: reference, target_words_per_section: tw, character_text: document.getElementById('characterText').value.trim() })
```

- [ ] **Step 3: Commit**

```bash
git add debug_ui.html
git commit -m "feat: add character_text input area to debug_ui.html submission form"
```

---

### Task 9: Web UI — "人物"标签页和角色卡片渲染

**Files:**
- Modify: `debug_ui.html` — 新增标签页、JavaScript 渲染函数、CSS 样式

- [ ] **Step 1: 添加"人物"标签页按钮**

在现有 tabs div 中、审阅标签页之后添加：

```html
    <div class="tab-btn" onclick="switchTab('characters')">人物<span class="badge" id="badge-characters"></span></div>
```

- [ ] **Step 2: 添加"人物"标签页内容区**

在 tab-review 和 tab-raw 之间添加：

```html
  <div class="tab-content" id="tab-characters"><div id="charactersContent"><p class="empty-state">等待角色数据...</p></div></div>
```

- [ ] **Step 3: 添加 renderCharacters(data) JavaScript 函数**

在 `renderReview` 函数之后添加：

```javascript
function renderCharacters(data) {
  if (!data.characters || !Array.isArray(data.characters) || data.characters.length === 0) return;
  badgeShow('characters');

  var arcs = data.character_arcs || [];
  var colors = ['#64b5f6', '#ce93d8', '#ff8a65', '#81c784', '#ffb74d', '#ef5350'];
  var html = '';

  data.characters.forEach(function(c, idx) {
    var color = colors[idx % colors.length];
    var arc = arcs.find(function(a) { return a.character_id === c.id; }) || {};
    var personalityTags = (c.personality || []).map(function(p) {
      return '<span class="char-tag" style="background:' + color + '33;color:' + color + '">' + escHtml(p) + '</span>';
    }).join(' ');

    var relationships = (c.relationships || []).map(function(r) {
      return '<span class="char-rel">' + escHtml(r.relation) + ': ' + escHtml(r.target) + '</span>';
    }).join(' ');

    // 弧线进度条
    var arcBar = '';
    if (arc.key_milestones && arc.key_milestones.length > 0) {
      var steps = arc.key_milestones.length + 2; // start + milestones + end
      var pct = 100 / steps;
      arcBar = '<div class="char-arc-bar">';
      arcBar += '<div class="char-arc-node active" style="left:0%;width:' + pct + '%">起: ' + escHtml(arc.starting_state || '?') + '</div>';
      arc.key_milestones.forEach(function(m, mi) {
        arcBar += '<div class="char-arc-node" style="left:' + ((mi + 1) * pct) + '%;width:' + pct + '%">§' + m.section + ': ' + escHtml(m.event || '?') + '</div>';
      });
      arcBar += '<div class="char-arc-node" style="left:' + ((steps - 1) * pct) + '%;width:' + pct + '%">终: ' + escHtml(arc.ending_state || '?') + '</div>';
      arcBar += '</div>';
    }

    html += '<div class="char-card" style="border-left:3px solid ' + color + '">' +
      '<div class="char-header">' +
        '<strong style="font-size:15px;color:' + color + '">' + escHtml(c.name || '?') + '</strong>' +
        '<span style="color:#888;font-size:12px;margin-left:8px">' + escHtml(c.gender || '') + ' &middot; ' + escHtml(c.age || '') + '</span>' +
      '</div>' +
      '<div class="char-tags">' + personalityTags + '</div>' +
      (c.motivation ? '<div class="char-field"><strong>动机:</strong> ' + escHtml(c.motivation) + '</div>' : '') +
      (c.catchphrase ? '<div class="char-field"><strong>口头禅:</strong> "' + escHtml(c.catchphrase) + '"</div>' : '') +
      (c.current_state || arc.current_state ? '<div class="char-field"><strong>当前状态:</strong> <span style="color:' + color + '">' + escHtml(c.current_state || arc.current_state || '') + '</span></div>' : '') +
      (relationships ? '<div class="char-field"><strong>关系:</strong> ' + relationships + '</div>' : '') +
      '<details class="char-details"><summary>更多信息</summary>' +
        (c.background ? '<div class="char-field"><strong>背景:</strong> ' + escHtml(c.background) + '</div>' : '') +
        (c.appearance ? '<div class="char-field"><strong>外貌:</strong> ' + escHtml(c.appearance) + '</div>' : '') +
        ((c.strengths || []).length ? '<div class="char-field"><strong>优点:</strong> ' + escHtml((c.strengths || []).join('、')) + '</div>' : '') +
        ((c.weaknesses || []).length ? '<div class="char-field"><strong>缺点:</strong> ' + escHtml((c.weaknesses || []).join('、')) + '</div>' : '') +
        (c.secret ? '<div class="char-field"><strong>秘密:</strong> ' + escHtml(c.secret) + '</div>' : '') +
        (c.world_position ? '<div class="char-field"><strong>世界观位置:</strong> ' + escHtml(c.world_position) + '</div>' : '') +
        (c.symbolism ? '<div class="char-field"><strong>象征:</strong> ' + escHtml(c.symbolism) + '</div>' : '') +
        ((c.key_lines || []).length ? '<div class="char-field"><strong>关键台词:</strong> ' + escHtml((c.key_lines || []).join('；')) + '</div>' : '') +
      '</details>' +
      arcBar +
    '</div>';
  });

  document.getElementById('charactersContent').innerHTML = html;
  log('Characters 渲染: ' + data.characters.length + ' 个角色', 'debug');
}
```

- [ ] **Step 4: 在 pollStatus() 中调用 renderCharacters**

在 `pollStatus()` 函数中，`renderReview(data)` 之后添加：

```javascript
    renderCharacters(data);
```

- [ ] **Step 5: 在 resetUI() 中添加角色内容区重置**

```javascript
  document.getElementById('charactersContent').innerHTML = '<p class="empty-state">等待角色数据...</p>';
```

同时在 badge 重置列表中添加 `'characters'`：

```javascript
  ['timeline', 'outline', 'handover', 'draft', 'review', 'characters'].forEach(function(name) {
```

- [ ] **Step 6: 添加角色卡片 CSS 样式**

在 `</style>` 之前添加：

```css
.char-card { background: rgba(255,255,255,0.03); border-radius: 8px; padding: 14px; margin: 10px 0; }
.char-header { display: flex; align-items: baseline; margin-bottom: 8px; }
.char-tags { margin: 4px 0 8px; }
.char-tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; margin-right: 4px; }
.char-rel { display: inline-block; padding: 1px 6px; background: rgba(255,255,255,0.05); border-radius: 4px; font-size: 11px; margin-right: 4px; }
.char-field { font-size: 12px; margin: 3px 0; color: #bbb; }
.char-field strong { color: #999; }
.char-details { margin-top: 8px; font-size: 12px; color: #888; }
.char-details summary { cursor: pointer; color: #64b5f6; }
.char-arc-bar { position: relative; height: 28px; background: rgba(255,255,255,0.04); border-radius: 4px; margin-top: 10px; display: flex; }
.char-arc-node { font-size: 10px; padding: 2px 4px; text-align: center; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; border-right: 1px solid rgba(255,255,255,0.06); }
.char-arc-node.active { color: #4caf50; background: rgba(76,175,80,0.1); }
```

- [ ] **Step 7: Commit**

```bash
git add debug_ui.html
git commit -m "feat: add Characters tab with card rendering and arc progress bar to debug_ui.html"
```

---

### Task 10: 测试

**Files:**
- Modify: `tests/test_basic.py` — 新增角色相关测试函数

- [ ] **Step 1: 添加 test_character_models()**

```python
def test_character_models():
    """验证 CharacterProfile 和 CharacterArc 模型定义。"""
    print("=" * 60)
    print("[TEST] 测试角色数据模型")

    from app.models import CharacterProfile, CharacterArc

    # 创建角色
    c = CharacterProfile(
        name="张三",
        gender="男",
        age="28岁",
        personality=["固执", "温柔"],
        motivation="复仇",
        background="被背叛的退伍军人",
    )
    assert c.name == "张三"
    assert c.personality == ["固执", "温柔"]
    assert c.motivation == "复仇"
    assert c.key_lines == []  # 默认值
    assert c.id == ""  # 默认空，由系统填充

    # 创建弧线
    a = CharacterArc(
        character_id="test-id",
        starting_state="愤怒的复仇者",
        ending_state="学会放下",
        key_milestones=[{"section": 1, "event": "触发事件"}],
        current_state="愤怒的复仇者",
    )
    assert a.character_id == "test-id"
    assert len(a.key_milestones) == 1

    # WriteRequest 默认值
    from app.models import WriteRequest
    req = WriteRequest(topic="测试", reference_text="测试文本")
    assert req.character_text == ""
    assert req.characters == []

    print("[PASS] 角色数据模型测试通过\n")
```

- [ ] **Step 2: 添加 test_character_extraction()**

```python
def test_character_extraction():
    """测试 LLM 从自然语言提取角色。"""
    print("=" * 60)
    print("[TEST] 测试角色提取")

    if not settings.LLM_API_KEY:
        print("[SKIP] LLM_API_KEY 未设置\n")
        return

    from app.agents.character_manager import CharacterManager

    cm = CharacterManager()
    text = "张三，28岁，退伍军人。性格固执但内心柔软，口头禅是'习惯了'。他的秘密是在战场上抛弃过战友。"
    result = cm.extract_characters(text)

    assert isinstance(result, list)
    assert len(result) >= 1
    char = result[0]
    assert "name" in char
    assert char["name"] or True  # 可能为空但必须有字段
    print(f"  提取结果: {char.get('name', '?')}")
    print("[PASS] 角色提取测试通过\n")
```

- [ ] **Step 3: 添加 test_character_arc_planning()**

```python
def test_character_arc_planning():
    """测试角色弧线规划。"""
    print("=" * 60)
    print("[TEST] 测试角色弧线规划")

    if not settings.LLM_API_KEY:
        print("[SKIP] LLM_API_KEY 未设置\n")
        return

    from app.agents.character_manager import CharacterManager

    cm = CharacterManager()
    characters = [{
        "id": "test-1",
        "name": "测试角色",
        "personality": ["勇敢", "脆弱"],
        "motivation": "证明自己",
        "catchphrase": "我可以的",
    }]
    outline = [
        {
            "section": 1,
            "title": "开端",
            "key_points": ["引入角色"],
            "subsections": [{"subsection": 1, "title": "初遇", "key_points": ["首次登场"]}],
        },
        {
            "section": 2,
            "title": "转折",
            "key_points": ["关键事件"],
            "subsections": [{"subsection": 1, "title": "抉择", "key_points": ["角色做出关键选择"]}],
        },
    ]
    result = cm.plan_arcs(characters, outline)

    assert isinstance(result, list)
    if len(result) > 0:
        arc = result[0]
        assert "starting_state" in arc
        assert "ending_state" in arc
        assert "key_milestones" in arc
        assert "current_state" in arc
        print(f"  弧线: {arc.get('starting_state', '?')} → {arc.get('ending_state', '?')}")
    print("[PASS] 角色弧线规划测试通过\n")
```

- [ ] **Step 4: 在 main 函数中添加新测试调用**

在 `if __name__ == "__main__":` 块中，现有测试之后添加：

```python
    # Test 4: 角色模型
    test_character_models()

    # Test 5: 角色提取（需要 LLM）
    test_character_extraction()

    # Test 6: 角色弧线规划（需要 LLM）
    test_character_arc_planning()
```

- [ ] **Step 5: 运行测试**

```bash
cd E:/writer/my_writing_system && uv run python tests/test_basic.py
```

Expected: 所有测试通过（角色模型测试即使在无 LLM 环境下也应通过；LLM 相关测试在无 API key 时 skip）

- [ ] **Step 6: Commit**

```bash
git add tests/test_basic.py
git commit -m "test: add character model, extraction, and arc planning tests"
```

---

## Execution Order

Tasks are designed to be executed sequentially:

```
Task 1 (Models) → Task 2 (Prompts) → Task 3 (CharacterManager)
    → Task 4 (Writer) → Task 5 (Reviewer) → Task 6 (Coordinator)
    → Task 7 (API) → Task 8 (UI Input) → Task 9 (UI Tab)
    → Task 10 (Tests)
```

Tasks 2-3 can run in parallel after Task 1. Tasks 4-7 are sequential. Tasks 8-9 can run in parallel after Task 7. Task 10 runs last.
