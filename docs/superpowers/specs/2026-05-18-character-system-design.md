# 人物设定系统 — 设计文档

> 日期: 2026-05-18
> 项目: 多智能体协作写作系统 (my_writing_system)
> 方案: A — Agent-Driven Character Pipeline

## 1. 概述

在现有写作流程中集成人物设定能力。用户通过**自然语言 + 结构化表单**两种方式输入人物信息，系统使用 LLM 提取、规划角色弧线，在写作过程中自动更新角色状态，并通过独立 UI 标签页展示。

### 用户故事

1. 提交写作任务时，粘贴一段人物小传（自然语言），系统自动提取为结构化角色卡
2. 在 Web UI 中确认/修正提取结果后开始写作
3. 大纲生成后，系统为每个角色规划弧线（起点→终点，每节里程碑）
4. 写作过程中，每节写完后自动更新各角色的当前状态
5. 在"人物"标签页实时查看角色卡片、弧线进度和状态变化
6. 审阅时系统检查角色行为是否与设定一致

---

## 2. 数据模型

### 2.1 CharacterProfile — 单个人物完整设定

```python
class CharacterProfile(BaseModel):
    id: str = ""                              # 自动生成 uuid
    name: str                                 # 姓名（必填）
    gender: str = ""                          # 性别
    age: str = ""                             # 年龄（"28岁" / "中年"）
    personality: list[str] = []               # 性格标签 ["固执", "温柔"]
    motivation: str = ""                      # 核心动机
    background: str = ""                      # 一句话背景
    appearance: str = ""                      # 外貌描写
    catchphrase: str = ""                     # 口头禅 / 语言风格
    strengths: list[str] = []                 # 优点
    weaknesses: list[str] = []                # 缺点
    secret: str = ""                          # 秘密 / 软肋
    world_position: str = ""                  # 在世界观中的位置
    symbolism: str = ""                       # 象征意义
    key_lines: list[str] = []                 # 预写关键台词
    relationships: list[dict] = []            # [{"target": "李四", "relation": "宿敌"}]
```

### 2.2 CharacterArc — 角色弧线

```python
class CharacterArc(BaseModel):
    character_id: str                         # 关联 CharacterProfile.id
    starting_state: str = ""                  # 起点状态
    ending_state: str = ""                    # 终点状态
    key_milestones: list[dict] = []           # [{"section": 1, "event": "..."}]
    current_state: str = ""                   # 当前状态（写作中动态更新）
```

### 2.3 WriteRequest 扩展

```python
class WriteRequest(BaseModel):
    topic: str
    reference_text: str
    target_words_per_section: int = 10000
    character_text: str = ""                  # 新增：自然语言角色描述
    characters: list[CharacterProfile] = []   # 新增：结构化角色（提取或手动）
```

### 2.4 TaskStatus / FinalResult 扩展

两个模型均新增可选字段：

```python
characters: list[dict] | None = None          # 角色卡列表
character_arcs: list[dict] | None = None      # 角色弧线列表
```

### 2.5 Redis 黑板字段

| key | 类型 | 说明 |
|-----|------|------|
| `task_id:characters` | JSON array | 角色卡列表 |
| `task_id:character_arcs` | JSON array | 角色弧线列表（含 current_state） |

---

## 3. CharacterManager 智能体

**文件**: `app/agents/character_manager.py`
**基类**: `BaseAgent`

### 3.1 extract_characters()

```
输入: character_text (str) — 自然语言人物描述
输出: list[CharacterProfile]

Prompt: CHARACTER_EXTRACTION_PROMPT
  - 输入用户自由文本
  - LLM 解析为 JSON 数组，每项含 14 个字段
  - 缺失字段填空字符串 ""
  - json_parser.parse_json() 解析
  - 失败抛出 ValueError 附带原始响应
```

### 3.2 plan_arcs()

```
输入:
  - characters: list[CharacterProfile]
  - outline: list[dict] — 大纲 V2

输出: list[CharacterArc]

Prompt: CHARACTER_ARC_PROMPT
  - 输入角色卡 JSON + 大纲 JSON
  - LLM 为每个角色生成弧线
  - starting_state: 一句话概括起点状态
  - ending_state: 一句话概括终点状态
  - key_milestones: 在哪些节发生关键变化
  - current_state: 初始等于 starting_state
```

### 3.3 update_states()

```
输入:
  - characters: list[CharacterProfile]
  - arcs: list[CharacterArc]
  - section_text: str — 刚写完的节的完整文本
  - section_num: int

输出: 更新后的 arcs（仅修改 current_state）

Prompt: CHARACTER_STATE_UPDATE_PROMPT
  - 输入单个角色卡 + 当前弧线 + 本节正文
  - LLM 输出新的 current_state（一句话）
  - 按角色逐个调用或批量调用（取决于角色数量）
```

### 3.4 兼容旧接口

```python
def run(self, **kwargs) -> dict:
    """兼容 BaseAgent 抽象方法。由 coordinator 统一调度。"""
    raise NotImplementedError("使用 extract_characters / plan_arcs / update_states 替代")
```

---

## 4. 流程集成

在现有 `coordinator.py` 的 6 阶段 pipeline 中插入 4 个集成点：

### Phase 0: 角色提取（新增）

```
位置: Phase 1 (风格分析) 之前
条件: WriteRequest.character_text 非空 且 characters 为空

执行:
  1. CharacterManager.extract_characters(character_text)
  2. 结果存入 bb.set(task_id, "characters", characters)
  3. 协作时间线记录: "角色提取完成: N 个角色"

如果用户已直接提交 characters（跳过提取），直接使用。
如果 character_text 和 characters 均为空，跳过整个角色子系统。
```

### Phase 2 后: 弧线规划（新增）

```
位置: 大纲确定后（outline_v2），Phase 3 写作前
条件: characters 列表非空

执行:
  1. CharacterManager.plan_arcs(characters, outline_v2)
  2. 结果存入 bb.set(task_id, "character_arcs", arcs)
  3. 协作时间线记录: "角色弧线规划完成: N 个角色"
```

### Phase 3 每节后: 状态更新（新增）

```
位置: Writer.run() 内，每节写完后
条件: characters 和 arcs 均非空

Writer.run() 签名变化:
  - 新增参数: characters, character_arcs

执行:
  1. 每节写完后调用 CharacterManager.update_states()
  2. 更新后的 arcs 写回 bb.set(task_id, "character_arcs", arcs)
  3. 协作时间线不单独记录（避免过于频繁）
```

### Phase 5: 审阅增强（修改）

```
位置: reviewer.review_global()
条件: characters 非空

变更:
  1. GLOBAL_REVIEW_PROMPT 新增 "## 人物一致性" 段落
  2. 注入角色卡摘要和弧线完成度
  3. 审阅结果中增加角色一致性评价
```

---

## 5. Writer 增强

### 5.1 Writer.run() 签名扩展

```python
def run(
    self,
    topic, style, outline,
    vector_store, blackboard, task_id,
    characters: list[dict] | None = None,          # 新增
    character_arcs: list[dict] | None = None,       # 新增
) -> dict:
```

### 5.2 Prompt 注入

`WRITING_PROMPT` 和 `WRITING_SECTION1_PROMPT` 在「风格要求」下方新增：

```
## 人物设定
{character_context}

## 本节人物弧线要求
{character_arc_context}
```

`character_context` 格式：
```
- 张三（固执、温柔）：复仇者，口头禅"这世界欠我的"，当前状态：刚发现真相，愤怒中
- 李四（冷静、腹黑）：张三日后的宿敌，当前状态：暗中观察张三
```

`character_arc_context` 格式：
```
- 张三：本节应展现他从"逃避"到"面对"的转折点
```

### 5.3 每节后状态更新

在 `Writer.run()` 每节循环结束后（section_texts 已累积该节内容）：

```python
if character_arcs:
    cm_char = CharacterManager()  # 或复用外部传入的实例
    character_arcs = cm_char.update_states(
        characters, character_arcs, section_text, section_num
    )
    if blackboard:
        blackboard.set(task_id, "character_arcs", character_arcs)
```

---

## 6. 提示词模板

### 6.1 CHARACTER_EXTRACTION_PROMPT（新增）

```
你是一位专业的人物设定编辑。请从以下自然语言描述中提取所有人物信息。

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
4. 保持原文语言风格
```

### 6.2 CHARACTER_ARC_PROMPT（新增）

```
你是一位故事结构专家。请根据人物设定和大纲，为每个角色设计变化弧线。

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
3. 结合大纲的具体情节设计里程碑
```

### 6.3 CHARACTER_STATE_UPDATE_PROMPT（新增）

```
你是一位细心的角色跟踪编辑。根据刚写好的正文，更新角色的当前状态。

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
3. 状态描述应与弧线中的里程碑对应
```

### 6.4 WRITING_PROMPT 扩展（修改）

在「风格要求」下方、「前面章节的交接笔记」上方插入：

```
## 人物设定
{character_context}

## 本节人物弧线要求
{arc_context}
```

### 6.5 GLOBAL_REVIEW_PROMPT 扩展（修改）

在现有评审维度后追加：

```
## 人物一致性
{character_consistency_context}
```

输出 JSON 扩展：
```json
{
    ...原有字段...,
    "character_consistency": "人物行为是否与设定一致的总体评价",
    "character_arc_progress": "各角色弧线完成度评价"
}
```

---

## 7. Web UI 变更 (debug_ui.html)

### 7.1 提交表单

在风格参考文本 textarea 下方新增：

```html
<label>人物设定（可选，自由描述或粘贴人物小传）</label>
<textarea id="characterText" placeholder="例如：张三，28岁，一个被背叛的退伍军人。性格固执但内心温柔..."></textarea>
```

提交时将 `character_text` 加入 POST body。

### 7.2 "人物"标签页

在现有标签页（大纲、交接笔记...）中新增：

```html
<div class="tab-btn" onclick="switchTab('characters')">人物
  <span class="badge" id="badge-characters"></span>
</div>
<div class="tab-content" id="tab-characters">
  <div id="charactersContent"></div>
</div>
```

### 7.3 角色卡片渲染

`renderCharacters(data)` 函数：

- 从 `data.characters` 读取角色列表
- 每个角色渲染为一张卡片（`.character-card`），包含：
  - 姓名 + 性别/年龄（标题行）
  - 性格标签（彩色标签）
  - 核心动机（高亮）
  - 背景、外貌、口头禅（可折叠详情）
  - 关系列表（链接到其他角色卡片）
  - 当前状态和弧线进度条

- 弧线进度：起始状态 → [里程碑1] → [里程碑2] → 终点状态，当前所在阶段高亮

### 7.4 提取反馈流程

1. 用户提交任务后，轮询检测 `characters` 字段出现
2. 自动切换到"人物"标签页
3. 展示提取结果，每个字段可点击编辑（contenteditable）
4. 用户确认后，通过 `POST /tasks/{task_id}/edit-field?field=characters` 保存
5. 如果用户未在提取阶段确认，系统在弧线规划前使用当前 `characters` 继续

### 7.5 样式要点

- 角色卡片采用暗色卡片背景 + 左侧彩色边条（每个角色一种颜色）
- 弧线进度条用渐变（灰色→蓝色→绿色）
- 状态更新时卡片有微弱的脉冲动画
- 编辑模式在字段上 hover 时显示虚线边框

---

## 8. 文件变更清单

| 文件 | 操作 | 变更内容 |
|------|------|----------|
| `app/models.py` | 修改 | 新增 CharacterProfile、CharacterArc；WriteRequest/TaskStatus/FinalResult 扩展 |
| `app/agents/character_manager.py` | **新建** | extract_characters()、plan_arcs()、update_states() |
| `app/coordinator.py` | 修改 | Phase 0 角色提取、Phase 2 后弧线规划、Phase 3 状态更新调用 |
| `app/agents/writer.py` | 修改 | run() 接收 characters/arcs，注入 prompt，每节后调 update_states() |
| `app/utils/prompt_templates.py` | 修改 | 新增 3 个角色模板；修改 WRITING_PROMPT、WRITING_SECTION1_PROMPT、GLOBAL_REVIEW_PROMPT |
| `app/main.py` | 修改 | Status 和 result 接口返回 characters、character_arcs 字段 |
| `debug_ui.html` | 修改 | 新增角色输入区 + "人物"标签页 + 角色卡片渲染 + 提取反馈流程 |
| `tests/test_basic.py` | 修改 | 新增 3 个角色测试 + 扩展端到端测试 |

**新建 1 个文件，修改 7 个文件。**

---

## 9. 测试策略

### 单元测试

| 测试 | 描述 |
|------|------|
| `test_character_extraction()` | 输入人物描述文本，验证返回 list[dict]，每个角色含 name/motivation 等必填字段 |
| `test_character_arc_planning()` | 给定角色卡 + 大纲，验证返回含 starting_state/ending_state/key_milestones 的弧线 |
| `test_character_state_update()` | 给定角色 + 弧线 + 正文片段，验证 current_state 正确更新 |

### 集成测试

| 测试 | 描述 |
|------|------|
| `test_full_workflow_with_characters()` | 端到端流程含角色输入，验证黑板 characters/character_arcs 贯穿始终 |
| `test_character_injection_in_prompt()` | 验证 Writer prompt 包含角色姓名、性格、当前状态 |

### 回归

所有现有测试仍需通过，角色字段为可选，不影响无角色场景。

### 运行

```bash
uv run python tests/test_basic.py
```

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| LLM 提取角色质量不稳定 | 中 | 中 | Prompt 严格要求 JSON；缺失字段填空串；UI 支持逐字段修正 |
| 角色状态更新过于模糊 | 低 | 低 | update_states() 限定输出为一句话；解析失败回退到上一状态 |
| token 消耗显著增加 | 中 | 低 | 写作时每节只注入 2-3 个非背景板角色；角色卡按需裁剪 |
| 提取/弧线 LLM 调用超时 | 低 | 中 | 与现有 LLM 调用共享重试逻辑（2 次）；失败不阻塞主流程 |
| debug_ui.html 体积膨胀 | 中 | 低 | 角色卡片用函数式渲染，逻辑集中在 renderCharacters() 中，不超过新增 200 行 |

---

## 11. 扩展预留

- **角色库**：将角色保存为 JSON 文件，跨任务复用
- **关系图**：用 Canvas/SVG 绘制角色关系网络图
- **角色语音**：为每个角色设置独特的对话风格模板
- **情节注入**：用户可指定"第 X 节必须发生角色 Y 的事件 Z"

---

## 12. 自审清单

- [x] 无 TBD / TODO 占位符
- [x] 数据模型前后一致（CharacterProfile 的 14 字段在提取 prompt、模型定义、UI 渲染中统一）
- [x] 流程集成点明确（4 个集成点，每个含位置、条件、执行动作）
- [x] 与现有架构一致（新增 Agent 遵循 BaseAgent 模式，黑板使用 Redis Hash）
- [x] 向后兼容（character_text 为空时整个子系统不激活）
- [x] 文件变更量可控（新建 1 + 修改 7）
- [x] 测试覆盖提取、弧线、状态更新、端到端四个层面
