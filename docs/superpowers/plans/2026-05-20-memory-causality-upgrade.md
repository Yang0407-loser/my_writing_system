# Plan: 六大设计集成 — 多智能体写作系统记忆与因果架构升级 (v2)

## Context

当前系统存在 6 个核心痛点：
1. **世界观崩塌**：世界事实仅靠交接笔记+摘要传递，压缩过程会丢失关键设定
2. **信息过载**：RAG 检索 + 运行摘要无差别注入，LLM 被无关信息淹没
3. **情节僵化**：Writer 机械执行大纲，缺乏角色驱动的戏剧冲突
4. **因果链断裂**：运行摘要是"一锅粥"，LLM 找不到事件的前因后果
5. **核心事实漂移**：交接笔记把"李四断了腿"和"这里可以回收伏笔"混在一起
6. **架构模糊**：Writer.run() 的记忆管理是零散字符串拼接，缺乏统一范式

参考 InkOS/NovelClaw/StoryBox/StoryWriter/WenShape/RecurrentGPT 的设计，逐项集成。

## 演进路线（渐进式，风险可控）

```
Step 1&2: WorldStateManager + Facts/Notes 分离  ← 基石，不动现有循环
    │
Step 4: Weighted Memory Pool  ← 内部增强，不改变 get_summary() 接口
    │
Step 3: Event Chain  ← 作为 WeightedMemoryPool 的"特殊生产者"
    │
Step 5: Character-driven Plot  ← 可选增强，独立分支
    │
Step 6: LSTM Architecture  ← 水到渠成的最终融合
```

每步完成后运行 `uv run python tests/test_basic.py` 确保核心流程不退化。新老代码通过 feature flag 短暂共存，可随时回退。

---

## Step 1&2: WorldStateManager + Facts/Notes 分离（合并实施）

> 只动 _parse_output 和 prompt 模板，不动 Writer 主循环和 ContextManager。效果立竿见影，风险极小。

### 新建 `app/world_state.py`

```python
class WorldFact(BaseModel):
    fact_id: str          # uuid
    category: str         # "geography" | "history" | "rule" | "character_fact" | "relationship"
    fact: str             # 一句话事实
    source_section: int
    source_subsection: int
    immutable: bool = True
    verified: bool = False  # 经交叉验证后为 True
    contradiction_of: str | None = None  # 如果此事实与某旧事实矛盾，记录旧 fact_id
    created_at: str

class WorldStateManager:
    def __init__(self, blackboard, task_id, llm_client=None)
    def add_fact(category, fact, source_section, source_subsection) -> str
    def verify_facts(potential_facts: list[dict]) -> list[str]  # 返回通过验证的 fact_id 列表
    def detect_contradiction(new_fact, existing_facts) -> str | None  # 返回矛盾的旧 fact_id
    def query_relevant(keywords: list[str], current_section, top_k=8) -> list[WorldFact]
    def get_all_facts() -> list[WorldFact]
    def get_contradictions() -> list[dict]  # 返回所有已记录的矛盾
    def serialize() -> dict
```

### ★ 事实校验层（核心补充）

**问题**：LLM 可能把主观感受当客观事实（"他看起来很疲惫" vs "他已经三天没睡了"）。

**方案**：分层校验——先规则粗筛，再 LLM 精判。

```python
# 规则层：零成本预过滤
_SUBJECTIVE_PATTERNS = [
    r"感到", r"觉得", r"似乎", r"好像", r"仿佛",
    r"看起来", r"显得", r"变得", r"意识到", r"认为",
]

def _rule_filter(self, fact: str) -> str:
    """返回 "objective" | "subjective" | "uncertain" """
    for pat in _SUBJECTIVE_PATTERNS:
        if re.search(pat, fact):
            return "subjective"
    # 纯外部动作描述 → 客观
    if re.match(r"^[\w一-鿿]+(获得了?|失去|到达|进入|离开|摧毁|建造|说出|杀死|受伤)", fact):
        return "objective"
    return "uncertain"

# LLM 层：仅对规则无法判断的条目调用
def verify_facts(self, potential_facts: list[dict], mode: str = "both") -> list[str]:
    """
    mode: "rule" — 仅规则过滤（零成本）
          "llm"  — 全部送 LLM（最准确）
          "both" — 规则过滤 → 不确定的送 LLM（默认，推荐）
    """
    verified = []
    uncertain = []
    
    for f in potential_facts:
        result = self._rule_filter(f["fact"])
        if result == "objective":
            verified.append(f)
        elif result == "subjective":
            continue  # 主观感受，直接丢弃
        else:
            uncertain.append(f)
    
    if mode == "rule":
        return [f["fact"] for f in verified]
    
    if mode == "both" and uncertain:
        verified += self._llm_verify(uncertain)
    elif mode == "llm":
        verified = self._llm_verify(potential_facts)
    
    return [f["fact"] for f in verified]
```

**环境变量**：`WORLD_STATE_VERIFY_MODE=both` — 默认推荐，平衡速度与准确性。

**调用成本**：规则过滤零成本。若 10 条潜在事实中 6 条被规则过滤（3 客观 + 3 主观），仅剩 4 条送 LLM，额外调用量可控。加上 Step 3 的事件提取，总计约每小节 2 次轻量 LLM 调用。

### ★ 事实版本控制（核心补充）

**问题**：如果 Writer 在 Section 3 破坏了 Section 1 的设定。

**矛盾检测先走规则再走 LLM**：

```python
def detect_contradiction(self, new_fact, existing_facts) -> str | None:
    """
    返回矛盾的旧 fact_id，无矛盾返回 None。
    先用规则粗筛（同角色+同属性），再用 LLM 精判。
    """
    # 规则层：同一角色/同一属性的描述才可能矛盾
    candidates = []
    for old in existing_facts:
        if old["category"] != new_fact["category"]:
            continue
        # 提取事实中的实体词做交集
        old_entities = self._extract_entities(old["fact"])
        new_entities = self._extract_entities(new_fact["fact"])
        if old_entities & new_entities:  # 有共同实体，可能矛盾
            candidates.append(old)
    
    if not candidates:
        return None
    
    # LLM 精判：只对候选做判断
    return self._llm_detect_contradiction(new_fact, candidates)
```

**矛盾不阻塞写作，统一路由到 Phase 4**：

```python
def add_fact(self, category, fact, source_section, source_subsection):
    ...
    if contradiction:
        # ★ 记录矛盾，但不阻塞写作
        self._contradictions.append({...})
        # ★ 生成警告，注入下一小节的 prompt
        self._active_warnings.append(
            f"⚠ 第{source_section}节发现与第{contradiction['section']}节矛盾: "
            f"'{fact}' vs '{contradiction['fact']}'。请在后文中修正或解释。"
        )
        return fact_id  # 返回，但不标记为 verified/immutable
    ...
```

**Writer prompt 注入矛盾警告**：

```
## 活跃矛盾警示（需在后文中修正）
{active_contradictions_warning}
```

矛盾累积到 Phase 4（ContinuityEditor）统一生成 `fix_checklist`，与现有流程完全兼容。矛盾如果是 critical 级别，会给下一小节一个 `{active_contradictions_warning}` 提醒 Writer 别再继续错下去，但不会暂停写作。

### 修改 `app/models.py`

```python
class HandoverNote(BaseModel):
    from_section: int
    to_section: int
    foreshadowing: str = ""       # note 类：伏笔
    character_state: str = ""     # note 类：人物状态
    open_threads: str = ""        # note 类：待承接
    found_contradictions: str = ""  # 回溯修正
    new_facts: list[str] = []     # ★ fact 类：本节确立的不可变事实
```

### 修改 `app/utils/prompt_templates.py`

交接笔记 prompt 部分新增第 4 字段：

```
在正文末尾，用 [交接笔记] 标记，包含：
1. 伏笔：本小节埋下的伏笔（上下文提示）
2. 人物状态：关键人物的情绪和处境
3. 待承接：需要后续呼应的信息
4. 新事实：本小节确立的客观事实（如"张三获得了火焰剑"、"李四左臂受伤"）
   注意：只列出可被独立观察证实的客观事实，不要列角色主观感受
5. 回溯修正：对前面章节的矛盾发现
```

WRITING_PROMPT 新增段落：

```
## 世界观事实（不可违背）
{world_facts}

## 世界观矛盾警示（如有）
{world_contradictions}
```

### 修改 `app/agents/writer.py`

- `_parse_output()` 新增解析"新事实"字段（正则匹配 `5. 新事实[：:]`)
- 每小节完成后调 `world_state_manager.add_fact()` 
- `world_state_manager.verify_facts()` 交叉验证
- `Writer.run()` 每小节前调 `query_relevant()` 拉取相关事实
- 通过 `ENABLE_WORLD_STATE` 环境变量控制，老代码共存：
  ```python
  if settings.ENABLE_WORLD_STATE:
      facts = world_state.query_relevant(keywords, section_num)
      contradictions = world_state.get_contradictions()
  ```

### 修改 `app/coordinator.py`

- 新增 `_phase_world_state(bb, task_id, state)` 在 Phase 2.6 之后
- 从 `world_setting` 和角色背景中 LLM 提取初始事实

---

## Step 4: Weighted Memory Pool（调整到第二步）

> 内部增强 ContextManager，不改变 `get_summary()` 对外接口。先提升信息质量，再在 Step 3 引入事件链作为生产者。

### 修改 `app/agents/context_manager.py`

```python
class MemoryEntry:
    text: str
    weight: float           # 动态权重
    category: str           # plot_event | character_state | world_fact | dialogue | description
    section: int
    subsection: int
    recency: float          # 时间衰减因子
    importance: float       # 人工/LLM 标记的重要性（0.1 ~ 2.0）
    access_count: int       # 被检索次数
    tags: list[str]         # ["复仇", "信物", "背叛"] 

class WeightedMemoryPool:
    def __init__(self, style_decay_factor: float = 0.9)
    def add_entry(text, category, section, subsection, importance=1.0, tags=None)
    def query(current_context_keywords, top_k) -> list[MemoryEntry]
    def decay_all()
    def boost(tags)  # 提升所有带指定标签的记忆权重 ×1.5
```

### ★ 温度调节：与风格系统挂钩（核心补充）

权重公式不再是硬编码的 0.9，而是从 50 维风格模型中推导：

```python
# 从 StyleProfile 映射到记忆衰减速度
# pacing: "舒缓" → decay=0.95 (记忆持久) / "紧凑" → decay=0.80 (快速遗忘)
# tension_curve: "缓慢释放" → decay=0.93 / "持续上升" → decay=0.78

STYLE_TO_DECAY = {
    "舒缓": 0.95, "中等": 0.90, "紧凑": 0.82, "急促": 0.78, "变速": 0.85,
}

def _compute_decay(style_profile: dict) -> float:
    pacing = style_profile.get("pacing", "中等")
    decay = STYLE_TO_DECAY.get(pacing, 0.90)
    # 紧张感越高，衰减越快（近期事件更重要）
    tension = style_profile.get("tension_curve", "")
    if tension == "持续上升": decay -= 0.05
    elif tension == "缓慢释放": decay += 0.03
    return max(0.70, min(0.97, decay))
```

最终权重公式：
```
weight = importance
       × (decay_factor ^ (current_section - entry.section))
       × keyword_match_bonus(entry.tags, current_context_keywords)
       × (1 + 0.1 × access_count)  # 经常被检索的记忆获得小幅加成
```

### ★ Boost 功能：伏笔回收的正反馈循环（核心补充）

**触发机制**：Writer prompt 中要求 LLM 在成功回收伏笔或展现角色 secret/motivation 时输出 `[MEMORY_BOOST: 标签1, 标签2]`。

**实现**：`_parse_output()` 中新增对 `[MEMORY_BOOST: ...]` 的解析：
```python
boost_match = re.search(r'\[MEMORY_BOOST:\s*([^\]]+)\]', raw_output)
if boost_match:
    tags = [t.strip() for t in boost_match.group(1).split(",")]
    memory_pool.boost(tags)
```

**效果**：当"断臂"这个伏笔被回收时，所有带"断臂"标签的记忆权重 ×1.5，后续检索更容易被捞到，形成"伏笔→回收→强化→更易被引用"的正反馈循环。

### ★ Boost 天花板 + 手动遗忘（核心补充）

**问题**：如果 `[MEMORY_BOOST]` 触发过于频繁，某些高权重记忆可能"虚高"，排挤新信息。例如"断臂"被反复 boost 10 次后权重达到 50，新发生的重要事件权重只有 1.5，永远排不上号。

**方案一**：`boost_ceiling` — 权重上限

```python
class WeightedMemoryPool:
    BOOST_CEILING = 3.0  # 任何记忆的权重不能超过此值
    
    def boost(self, tags):
        for entry in self._entries:
            if any(t in entry.tags for t in tags):
                entry.weight = min(entry.weight * 1.5, self.BOOST_CEILING)
                # 超过上限时记录，用于调试
                if entry.weight >= self.BOOST_CEILING:
                    log(f"记忆权重已达上限: {entry.text[:50]}...")
```

**方案二**：`forget()` — 手动遗忘接口

```python
def forget(self, entry_id: str | None = None, tags: list[str] | None = None):
    """
    手动降低/移除记忆。
    - entry_id: 精确遗忘某条记忆
    - tags: 遗忘所有带指定标签的记忆（权重归零）
    """
    if entry_id:
        self._entries = [e for e in self._entries if e.id != entry_id]
    if tags:
        for entry in self._entries:
            if any(t in entry.tags for t in tags):
                entry.weight = 0  # 归零而非删除，保留可追溯性

def vacuum(self):
    """清理权重为 0 的记忆，释放内存"""
    self._entries = [e for e in self._entries if e.weight > 0]
```

**效果**：
- `boost_ceiling` 防止记忆"通胀"，保持权重健康分布
- `forget()` 提供人工干预出口——当用户发现某些信息不应再影响写作时，通过前端按钮一键遗忘
- **注意**：`forget()` API 需要在 Step 4 实施时就写好，即使前端暂时不接。否则后面 `writing_ui.html` 调试面板想加"遗忘标签"输入框时会发现后端接口缺失。

---

## Step 3: Event Chain（调整到第三步）

> EventChain 成为 WeightedMemoryPool 的"特殊生产者"——事件自动变为高权重记忆。

### 修改 `app/agents/context_manager.py`（追加）

```python
class CausalEvent(BaseModel):
    event_id: str
    event: str              # "张三把最后一块面包分给了李四"
    cause_event_id: str | None
    section: int
    subsection: int
    characters: list[str]
    tags: list[str]

class EventChain:
    def add_event(event, cause_event_id, section, subsection, characters, tags)
    def get_causal_chain(event_id, depth=5, relevance_filter: list[str] | None = None) -> list[CausalEvent]
    def get_recent_events(count=10) -> list[CausalEvent]
    def as_memory_entries() -> list[MemoryEntry]  # ★ 桥接到 WeightedMemoryPool
```

### ★ 降低事件提取粒度（核心补充）

提取 Prompt 中明确要求"可观测的具体行动"：

```
从以下文本中提取 1-3 个关键事件。每个事件必须满足：
1. 是可观测的具体行动或发生的事 —— 不是概括，不是心理活动
2. 一句话描述，包含"谁 + 做了什么 + 对谁/什么"

✓ 好的事件：
  - "张三把最后一块面包分给了李四"
  - "守卫队长的枪走火，击碎了窗户"
  - "李四在废墟中发现了一枚刻有龙纹的戒指"

✗ 不好的事件：
  - "他们关系变好了"（太宏观，不是具体行动）
  - "主角团踏上旅程"（太概括，丢失细节）
  - "张三感到愤怒"（心理活动，不是可观测事件）

请输出 JSON 数组：
[
  {"event": "...", "cause_event_id": "前一个事件的ID或null",
   "characters": ["角色名"], "tags": ["标签"]}
]
```

### ★ 因果链相关性过滤（核心补充）

**问题**：故事增长后，EventChain 会变得很长。写新章节时，LLM 不需要看到完整长链——例如写"李四的感情线"时，不需要看到"张三的武器升级史"。

**方案**：`get_causal_chain()` 增加 `relevance_filter` 参数：

```python
def get_causal_chain(self, event_id, depth=5, relevance_filter=None):
    """
    relevance_filter: list[str] — 当前场景的关键词（角色名、地点、主题标签）
    只返回因果链中与这些关键词相关的分支。
    
    例：写"李四在酒馆重逢旧友"场景时，
        relevance_filter = ["李四", "酒馆", "旧友", "重逢"]
        → 只返回涉及李四、酒馆或旧友的因果事件
        → 过滤掉"张三在武器铺升级装备"这条无关分支
    """
    full_chain = self._trace_back(event_id, depth)
    if not relevance_filter:
        return full_chain
    
    # 相关性评分：关键词交集 —— 不用 embedding，零成本
    filtered = []
    for event in full_chain:
        event_keywords = set(event.get("characters", []) + event.get("tags", []))
        if event_keywords & set(relevance_filter):  # 至少一个关键词匹配
            filtered.append(event)
    return filtered
```

**实现要点**：`_relevance_score` 最简单有效的实现是关键词交集，不是 embedding 相似度。事件的所有 `tags + characters` 与 `relevance_filter` 取交集，命中 1 个即通过，零额外 LLM/embedding 调用。

### 与 WeightedMemoryPool 的桥接

```python
# 在 Writer.run() 每小节完成后：
events = event_chain.extract_events(sub_text)  # LLM 提取
for evt in events:
    event_chain.add_event(...)
    # 自动变为高权重记忆
    memory_pool.add_entry(
        text=evt.event,
        category="plot_event",
        section=section_num,
        subsection=sub_num,
        importance=1.5,  # 事件默认高权重
        tags=evt.tags,
    )
```

### 因果链可视化（未来方向）

EventChain 的数据结构天然适合前端可视化。未来可在 `writing_ui.html` 中增加一个小面板，动态展示"当前故事的因果球"——事件节点 + 因果箭头。这会让系统在调试和理解故事方面有巨大优势。

---

## Step 5: Character-driven Plot（可选增强）

> 独立分支，不影响其他模块。

### 新建 `app/agents/character_decision.py`

```python
class CharacterDecisionEngine:
    def run(self, characters, arcs, current_situation, subsection_outline,
            outline_constraint: float = 0.5) -> dict:
        """
        outline_constraint: 0~1
          0 = 完全按大纲走（当前模式）
          0.5 = 角色在大纲框架内自由互动，最终导向大纲节点
          1 = 完全由角色驱动，大纲仅作参考
        """
```

### ★ 大纲约束强度调节（核心补充）

不是在"开/关"之间二选一，而是用 0-1 连续调节：

```
outline_constraint = 0:
  → 完全按大纲写，跳过角色决策环节（= 当前模式，关闭此功能）

outline_constraint = 0.5:
  → 角色在大纲框架内互动
  → Prompt: "大纲要求本小节达到 [大纲节点]。请模拟角色在此场景下的行为，
     但最终结果必须导向大纲指定的节点。你可以自由发挥如何到达那里。"

outline_constraint = 1:
  → 完全由角色驱动
  → Prompt: "让角色基于各自的性格和动机自由互动。大纲仅作为背景参考，
     如果角色的自然选择偏离了大纲，请跟随角色。"
```

**前端集成**：在 `writing_ui.html` 风格控制台附近加一个滑块：
```html
<label>角色驱动度: {{ outlineConstraint }}
  <input type="range" min="0" max="1" step="0.1" v-model.number="outlineConstraint">
</label>
```

### Prompt 设计

```
## 角色决策模拟 (大纲约束强度: {outline_constraint})
当前场景: 张三在旧工厂与守卫队长对峙
大纲要求: 本小节需要展现张三内心的挣扎

角色状态:
- 张三（固执、愤怒）：复仇者，当前状态：左臂受伤，体力不支
  核心动机: 为战友复仇 | 秘密: 曾在战场上抛弃过战友
- 守卫队长（冷静、职业）：执行命令，当前状态：发现入侵者
  核心动机: 保护工厂秘密

1. 列出 2-3 个基于角色性格的可能微观走向
2. 评估每个走向的戏剧性(1-10)和角色一致性(1-10)
3. 推荐最佳走向（考虑大纲约束强度 {outline_constraint}）
4. 如果选择了偏离大纲的走向，说明理由

Writer 将根据此分析进行创作。
```

### 集成方式

- 受 `ENABLE_CHARACTER_DRIVEN_PLOT` + `OUTLINE_CONSTRAINT` 两个环境变量控制
- 在 `Writer.run()` 的 RAG 检索之后、prompt 构建之前插入
- 当 `OUTLINE_CONSTRAINT=0` 时完全跳过此环节（默认值，向后兼容）
- 推荐走向作为 prompt 的 `{character_decisions}` 段落

---

## Step 6: LSTM Architecture（最终融合）

> 前五步都稳定运行后，进行架构性重构。此时 LTM/STM/WM 的组件已全部就绪，水到渠成。

### ★ 渐进式重构策略（核心补充）

不一次性重写 `Writer.run()`。而是在前五步中，把新模块通过组合方式嵌入旧循环：

```python
# Step 1&2 后：WorldStateManager 作为独立对象嵌入
if settings.ENABLE_WORLD_STATE:
    world_state = WorldStateManager(blackboard, task_id, self.llm)
else:
    world_state = None  # 老路径

# Step 4 后：WeightedMemoryPool 替换 ContextManager._buffer
if settings.ENABLE_WEIGHTED_MEMORY:
    memory_pool = WeightedMemoryPool(style_decay)
    cm = ContextManager(self.llm, memory_pool=memory_pool)
else:
    cm = ContextManager(self.llm)  # 老路径

# Step 3 后：EventChain 作为 WeightedMemoryPool 的生产者
if settings.ENABLE_EVENT_CHAIN:
    event_chain = EventChain()
    # 每小节后: events → event_chain → memory_pool (自动桥接)
```

**新老共存**：通过 feature flag 切换，每步都可以独立回退。

### 最终融合后的 Writer.run() 结构

```python
def run(self, ...):
    # ── 初始化三层记忆（组件已在前五步中逐一验证）──
    ltm = LongTermMemory(
        world_state=WorldStateManager(bb, task_id, self.llm),
        event_chain=EventChain(),
        character_arcs=character_arcs,
    )
    stm = ShortTermMemory(
        handover=prev_handover,
        recent_memories=WeightedMemoryPool(style_decay),
    )
    
    for sec in outline:
        for sub in subsections:
            # ── Working Memory（每小节重建）──
            wm = {
                "outline": sub,
                "char_states": ltm.get_current_states(),
                "rag_chunks": vector_store.search(...),
            }
            
            # ── 组装 prompt（三层记忆各取所需）──
            prompt = assemble_prompt(
                ltm_facts=ltm.world_state.query_relevant(wm.keywords),
                ltm_contradictions=ltm.world_state.get_contradictions(),
                stm_context=stm.get_weighted_context(wm.keywords, top_k=10),
                stm_causal=ltm.event_chain.get_causal_chain(latest_event_id),
                wm=wm,
                character_decisions=decision_engine.run(...) if enabled else None,
            )
            
            # ── LLM 生成 ──
            output = generate(prompt)
            
            # ── 更新记忆 ──
            ltm.update(output)   # facts + events + arcs
            stm.update(output)   # handover + memory decay + boost
```

### 各层职责总览

| 层级 | 存储内容 | 来源模块 | 生命周期 | 更新时机 |
|------|----------|----------|----------|----------|
| LTM | 世界事实、事件因果链、角色弧线历史 | WorldStateManager + EventChain + CharacterArcs | 全任务周期 | 每小节后追加 |
| STM | 交接笔记、加权近期记忆、因果链尾部 | HandoverNotes + WeightedMemoryPool | 滑动窗口 | 每节后衰减+更新 |
| WM | 当前大纲小节、角色此刻状态、RAG 检索 | Outline + RAG + CharacterStates | 单小节 | 每小节重建 |

### ★ assemble_prompt — 核心枢纽（提前标记）

重构时，`assemble_prompt()` 将成为所有信息注入的唯一入口。**现在就应该在注释中标明**：

```python
# [assemble_prompt 收敛点]
# 当前分散在 Writer.run() 各处的 prompt 拼接逻辑，重构时全部迁移至此：
#   - 风格参数注入 (style_brief, emotion_intensity, paragraph_length_avg)
#   - 大纲/小节信息注入 (section_outline, key_points, sub_description)
#   - 世界观事实注入 (world_facts, world_contradictions)        ← Step 1&2
#   - 事件因果链注入 (causal_chain)                              ← Step 3
#   - 加权记忆上下文注入 (weighted_context)                      ← Step 4
#   - 角色决策注入 (character_decisions)                         ← Step 5
#   - 交接笔记注入 (handover_context, character_context, arc_context)
#   - RAG 检索注入 (retrieved_context)
#   - 矛盾警告注入 (active_contradictions_warning)
def assemble_prompt(wm, ltm, stm, template) -> str:
    """所有 prompt 组装逻辑的最终收敛点。"""
    ...
```

暂时保留 `Writer.run()` 中现有的 `.format()` 拼接代码，重构时统一迁移到此函数。

---

## 环境变量新增

```bash
# Step 1&2: 世界事实管理
ENABLE_WORLD_STATE=true
WORLD_STATE_VERIFY_MODE=both     # "rule" | "llm" | "both" — 事实校验模式

# Step 3: 事件因果链追踪
ENABLE_EVENT_CHAIN=true

# Step 4: 动态记忆权重
ENABLE_WEIGHTED_MEMORY=true

# Step 5: 角色驱动情节
ENABLE_CHARACTER_DRIVEN_PLOT=false   # 默认关闭（token 消耗较高）
OUTLINE_CONSTRAINT=0.5               # 大纲约束强度 0~1（仅 ENABLE_CHARACTER_DRIVEN_PLOT=true 时生效）
```

---

## 涉及文件总览

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1&2 | `app/world_state.py` | **新建** |
| 1&2 | `app/models.py` | 修改 (新增 WorldFact, HandoverNote.new_facts) |
| 1&2,3,4 | `app/agents/writer.py` | 修改 (_parse_output 新增事实/MEMORY_BOOST; prompt 新增 world_facts/causal_chain) |
| 1&2 | `app/coordinator.py` | 修改 (新增 _phase_world_state) |
| 1,3,5 | `app/utils/prompt_templates.py` | 修改 (WRITING_PROMPT 新增段落; 事件提取 prompt) |
| 3,4 | `app/agents/context_manager.py` | 重构 (WeightedMemoryPool + EventChain 追加) |
| 5 | `app/agents/character_decision.py` | **新建** |
| 6 | `app/agents/writer.py` | 重构 (LTM/STM/WM 组合, feature flag 共存) |
| 全部 | `app/config.py` | 修改 (新增 4 个环境变量) |
| 全部 | `.env.example` | 修改 |
| 全部 | `README.md` | 更新 |

## 验证方式

| 步骤 | 验证 |
|------|------|
| 1&2 | `test_basic.py` 通过；Redis 黑板出现 `task_id:world_state`；生成 prompt 含 `世界观事实` 段落；校验层正确标记 verified/not |
| 3 | prompt 含 `事件因果链`；提取的事件是"具体行动"而非概括；cause_event_id 链正确 |
| 4 | prompt token 用量对比（同任务下应减少 ~20-30% 无关信息）；记忆衰减因子随风格变化 |
| 5 | 角色决策 prompt 含大纲约束强度；`OUTLINE_CONSTRAINT=0` 时跳过 |
| 6 | Writer.run() 循环体行数显著下降；每层可独立开关；test_basic.py 全量通过 |
| 全量 | `/write-ui` 交互模式完整流程 → 导出 Markdown → 检查事实一致性、因果逻辑、角色行为 |
