from pydantic import BaseModel, Field
from enum import Enum


# ============================================================
# Phase 1 新增: 枚举类型
# ============================================================
class ConstraintType(str, Enum):
    MUST_INCLUDE = "must_include"
    MUST_NOT_INCLUDE = "must_not_include"
    MUST_HAPPEN_BEFORE = "must_happen_before"
    MUST_HAPPEN_AFTER = "must_happen_after"


class RuleType(str, Enum):
    STYLE = "style"
    PLOT = "plot"
    CHARACTER = "character"
    DIALOGUE = "dialogue"
    GLOBAL = "global"
    CUSTOM = "custom"


class RuleScope(str, Enum):
    GLOBAL = "global"
    PER_CHAPTER = "per_chapter"
    PER_PARAGRAPH = "per_paragraph"


# ============================================================
# Phase 1 新增: 故事线约束
# ============================================================
class StorylineConstraint(BaseModel):
    """故事线约束——不可变的叙事骨架。"""
    id: str = ""
    type: ConstraintType = ConstraintType.MUST_INCLUDE
    description: str = ""
    source_chapter: int = 0
    target_chapter: int | None = None
    priority: int = Field(5, ge=1, le=10)
    status: str = "active"  # active | fulfilled | violated
    related_characters: list[str] = []
    related_events: list[str] = []


# ============================================================
# Phase 1 新增: 伏笔管理
# ============================================================
class Foreshadowing(BaseModel):
    """伏笔——埋设→回收的完整生命周期。"""
    id: str = ""
    name: str = ""
    description: str = ""
    plant_chapter: int = 0
    resolve_chapter: int | None = None
    status: str = "pending"  # pending | planted | hinted | resolved
    related_characters: list[str] = []
    related_items: list[str] = []
    importance: int = Field(5, ge=1, le=10)
    tags: list[str] = []


# ============================================================
# Phase 1 新增: 规则中心
# ============================================================
class WritingRule(BaseModel):
    """用户自定义写作规则。"""
    id: str = ""
    name: str = ""
    description: str = ""
    content: str = ""
    type: RuleType = RuleType.GLOBAL
    priority: int = Field(5, ge=1, le=10)
    scope: RuleScope = RuleScope.GLOBAL
    enabled: bool = True
    created_by: str = "user"  # user | system | imported


# ============================================================
# 角色模型
# ============================================================
class CharacterProfile(BaseModel):
    """单个人物的完整设定（16 个字段）。"""
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
    custom: dict = {}


class ArcMilestone(BaseModel):
    """角色弧线中的一个时间节点——小节级别的具体事件。"""
    section: int
    subsection: int = 1
    event: str = ""          # 具体事件描述
    location: str = ""       # 地点
    time: str = ""           # 时间（季节/时刻）
    emotional_shift: str = ""  # 情感转折：从X → Y
    milestone_id: str = ""
    classification: str = ""
    requiredness: str = ""
    before_state: str = ""
    trigger: str = ""
    after_state: str = ""
    observable_evidence: str = ""
    source_id: str = ""
    source_hash: str = ""
    rationale: str = ""
    depends_on: list[str] = []
    causes: list[str] = []


class CharacterArc(BaseModel):
    """角色弧线 —— 角色的变化轨迹（legacy，v3 后由 NarrativeEvent 替代）。"""
    character_id: str
    starting_state: str = ""
    ending_state: str = ""
    key_milestones: list[dict] = []  # v3: 改用 NarrativeEvent 列表
    current_state: str = ""


# ============================================================
# v3 统一事件模型
# ============================================================
class NarrativeEventModel(BaseModel):
    """统一叙事事件（API 传输用）。"""
    event_id: str = ""
    type: str = "plot_thread"  # arc_milestone | plot_thread | world_fact
    description: str
    section: int = 0
    subsection: int = 0
    character_id: str = ""
    status: str = "pending"
    weight: int = Field(5, ge=1, le=10)
    span: str = "medium"  # short | medium | long
    urgency: str = "low"  # low | medium | high
    related_events: list[str] = []
    tags: list[str] = []
    classification: str = ""
    requiredness: str = ""
    contract_version: str = ""
    source_id: str = ""
    source_hash: str = ""
    rationale: str = ""
    relation_metadata: dict[str, dict] = {}


# ============================================================
# 请求 / 响应
# ============================================================
class WriteRequest(BaseModel):
    task_id: str = ""  # draft task UUID；为空时 Celery 自动生成
    topic: str = Field(..., min_length=1)
    reference_text: str = Field(..., min_length=1)
    target_words_per_section: int = Field(10000, ge=500)
    character_text: str = ""
    characters: list[CharacterProfile] = []
    world_setting: str = ""      # 世界观设定
    story_synopsis: str = ""     # 故事梗概

    style_profile: dict = {}  # 4 个主要风格控制量 + 兼容统计字段
    outline: list[dict] = []  # 用户预设大纲（可选，跳过 Planner 生成）


class WriteResponse(BaseModel):
    task_id: str
    status: str = "pending"


class ReviseRequest(BaseModel):
    section: int
    subsection: int | None = None
    instruction: str  # e.g. "加重孤独感", "删除这段", "加入一段环境描写"


# ============================================================
# 风格 / 大纲
# ============================================================
class StyleProfile(BaseModel):
    """精简风格模型 —— 参考文本提供底色，4 个旋钮做微调。"""
    # 元数据
    reference_text: str = ""
    preset_name: str = ""
    # 4 个旋钮
    dialogue_ratio: float = Field(0.3, ge=0, le=1)     # 对话占比
    emotion_intensity: int = Field(50, ge=10, le=90)     # 情感强度: 克制←50→浓郁
    sentence_preference: str = "balanced"                # 句长偏好: short | balanced | long
    sensory_density: str = "medium"                      # 感官密度: sparse | medium | rich
    # 保留兼容字段
    narrative_density: float = 0.5
    adjective_density: float = 0.15
    paragraph_length_avg: int = 200
    short_sentence_ratio: float = 0.33
    medium_sentence_ratio: float = 0.34
    long_sentence_ratio: float = 0.33
    dialogue_tag_style: str = "动作替代"
    pacing: str = "中等"


class SubsectionItem(BaseModel):
    subsection: int
    title: str
    key_points: list[str] = []
    target_words: int = 2000
    description: str = ""  # 小节梗概：这一幕发生了什么
    event_contract: dict | None = None


class OutlineItem(BaseModel):
    section: int
    title: str
    key_points: list[str]
    subsections: list[SubsectionItem] = []


def flatten_tree_to_outline(root_nodes: list[dict]) -> list[dict]:
    """将树状大纲递归展开为 [{section, subsections}] 格式。

    每个根节点生成一个 section，其所有叶子后代成为 subsections。
    中间节点的 title/description 仅用于组织结构，不出现在输出中。
    """
    def _collect_leaves(node: dict) -> list[dict]:
        children = node.get("children") or []
        if not children:
            return [node]
        result: list[dict] = []
        for child in children:
            result.extend(_collect_leaves(child))
        return result

    outline: list[dict] = []
    for si, root in enumerate(root_nodes, 1):
        leaves = _collect_leaves(root)
        outline.append({
            "section": si,
            "title": root.get("title", ""),
            "key_points": root.get("key_points") or [],
            "subsections": [
                {
                    "subsection": li,
                    "title": leaf.get("title", ""),
                    "description": leaf.get("description", ""),
                    "key_points": leaf.get("key_points") or [],
                    "target_words": leaf.get("target_words", 2000),
                    **(
                        {"event_contract": leaf["event_contract"]}
                        if leaf.get("event_contract")
                        else {}
                    ),
                }
                for li, leaf in enumerate(leaves, 1)
            ],
        })
    return outline


# ============================================================
# 协作模型
# ============================================================
class OutlineReviewFeedback(BaseModel):
    """单个智能体对大纲的审查意见。"""
    reviewer: str  # "style_analyst" | "writer"
    approved: bool
    criticism: str = ""       # 具体批评
    suggestion: str = ""      # 修改建议


class HandoverNote(BaseModel):
    """一节写完后交给下一节的笔记。"""
    from_section: int
    to_section: int
    foreshadowing: str = ""       # 埋下的伏笔（note 类）
    character_state: str = ""     # 人物当前情绪/状态（note 类）
    open_threads: str = ""        # 需要后续承接的关键信息（note 类）
    found_contradictions: str = ""  # 对前面章节发现的矛盾
    new_facts: list[str] = []     # ★ fact 类：本节确立的客观事实


class BackrefFix(BaseModel):
    """后继章节对前面章节的回溯修正建议。"""
    from_section: int              # 哪个章节提出
    target_section: int            # 要修正哪个章节
    target_subsection: int | None = None
    severity: str = "minor"       # "critical" | "minor"
    description: str               # 修正内容描述


class FixChecklist(BaseModel):
    """ContinuityEditor 产出的修正清单。"""
    critical_fixes: list[BackrefFix] = []  # 必须修
    minor_fixes: list[BackrefFix] = []     # 建议修
    summary: str = ""                       # 汇总说明


class CollaborationTimeline(BaseModel):
    """在 Web UI 中展示的协作事件。"""
    stage: str             # "outline_review" | "writing" | "handover" | "backref" | "fixing" | "review"
    agent: str             # "planner" | "style_analyst" | "writer" | "continuity_editor" | "reviewer"
    action: str            # 人类可读的动作描述
    detail: str = ""       # 具体内容
    section: int | None = None


# ============================================================
# 审阅
# ============================================================
class SectionReview(BaseModel):
    section: int
    score: int = Field(..., ge=1, le=10)
    consistency_notes: str = ""


class ReviewResult(BaseModel):
    section_reviews: list[SectionReview] = []
    global_score: int = Field(1, ge=1, le=10)
    suggestion: str = ""
    handover_insight: str = ""  # 交接笔记链中最有价值的洞察


# ============================================================
# 状态 / 结果
# ============================================================
class TaskStatus(BaseModel):
    task_id: str
    status: str
    progress: str | None = None
    style: dict | None = None
    outline: list[dict] | None = None
    outline_v1: list[dict] | None = None       # 大纲初版（用于对比）
    outline_reviews: list[dict] | None = None  # 大纲评审意见
    handover_notes: list[dict] | None = None   # 交接笔记链
    fix_checklist: dict | None = None           # 修正清单
    timeline: list[dict] | None = None          # 协作时间线
    draft: str | None = None
    review: dict | None = None
    error: str | None = None
    characters: list[dict] | None = None
    character_arcs: list[dict] | None = None
    world_state: dict | None = None  # v0.6.0: 世界事实库
    constraints: list[dict] | None = None  # v0.9.0: 故事线约束
    foreshadowings: list[dict] | None = None  # v0.9.0: 伏笔列表
    ai_detect_log: list[dict] | None = None   # AI痕迹检测日志
    section_reviews: list[dict] | None = None  # 分节审阅累积
    token_usage: int | None = None  # v0.9.1: 累计 token 消耗 (prompt + completion)
    token_cost: dict | None = None  # v0.9.2: {total_tokens, est_cost_usd, total_time_s}
    topic: str | None = None
    world_setting: str | None = None
    story_synopsis: str | None = None
    reference_text: str | None = None
    style_profile: dict | None = None
    target_words_per_section: int | None = None
    document_ref: dict | None = None
    commit_status: str | None = None
    state_version_id: str | None = None
    critical_projection_status: str | None = None
    non_blocking_projection_status: str | None = None


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
    characters: list[dict] | None = None
    character_arcs: list[dict] | None = None
    world_state: dict | None = None  # v0.6.0: 世界事实库
    output_file: str = ""  # 导出的 .md 文件路径
    document_ref: dict | None = None
    commit_status: str | None = None
    state_version_id: str | None = None
    critical_projection_status: str | None = None
    non_blocking_projection_status: str | None = None


# ============================================================
# 任务检查点状态（用于挂起-继续模式）
# ============================================================
class TaskState(BaseModel):
    """Celery 任务检查点 —— 序列化到 Redis 的完整状态快照。"""
    task_id: str
    phase: str = "init"
    status: str = "pending"
    config_topic: str = ""
    config_reference_text: str = ""
    config_target_words: int = 10000
    config_character_text: str = ""
    config_interactive: bool = False
    characters: list[dict] = []
    style_profile: dict | None = None
    outline_v1: list[dict] = []
    outline_v2: list[dict] = []
    outline_reviews: list[dict] = []
    character_arcs: list[dict] = []
    draft: dict[str, str] = {}
    handover_chain: list[dict] = []
    backref_suggestions: list[dict] = []
    fix_checklist: dict | None = None
    review_result: dict | None = None
    timeline: list[dict] = []
    section_texts: dict[int, str] = {}
    constraints: list[dict] = []  # v0.9.0
    updated_at: str = ""


# ============================================================
# v0.9: 章节版本历史
# ============================================================
class ChapterVersion(BaseModel):
    chapter: int
    version: int = 1
    text: str
    change_desc: str = ""
    created_at: str = ""


def paragraph_id(text: str, chapter: int, para_idx: int) -> str:
    """基于内容的段落签名, 不受编辑位置变动影响。"""
    import hashlib
    first = text.strip()[:30] if text else ""
    last = text.strip()[-30:] if len(text.strip()) > 30 else ""
    sig = hashlib.md5(f"{chapter}:{first}:{last}".encode()).hexdigest()[:8]
    return f"ch{chapter}-p{para_idx}-{sig}"
