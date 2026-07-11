import re
import json
import uuid
import logging
from datetime import datetime
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

# ============================================================
# 规则层：零成本预过滤主观/客观
#
# 设计原则：
#   主观优先短路 → 改为双匹配识别。
#   当一句话同时包含主观感知词和客观事件词时（如"他感到左臂断裂"），
#   不丢弃，而是标记为 "mixed" 走 LLM 精判。
#   纯主观（"他觉得自己很帅"）→ 丢弃。纯客观 → 自动验证。
# ============================================================
_SUBJECTIVE_PATTERNS = [
    r"感到", r"觉得", r"似乎", r"好像", r"仿佛",
    r"看起来", r"显得", r"意识到", r"认为",
    r"可能", r"也许", r"大概", r"应该", r"猜测",
]

_OBJECTIVE_PATTERNS = [
    # 物理变化
    r"(断裂|破碎|崩塌|坍塌|毁灭|湮灭|消散|粉碎|崩碎|折断)",
    # 空间/法则变化（玄幻核心）
    r"(扭曲|撕裂|折叠|扩展|收缩|震荡|紊乱|重组|崩塌|裂开|湮灭|吞噬)",
    # 人物状态变化（可外观察证）
    r"(获得了?|失去|到达|进入|离开|摧毁|建造|说出|杀死|受伤|死亡|出生|结婚|离婚|获得|丢失|发现|创造)",
    # 能力/修为变化
    r"(突破|晋升|觉醒|变异|反噬|暴走|失控|封印|解封|消散)",
]


def _extract_entities(text: str) -> set[str]:
    """从文本中提取实体词（中文名、地名、关键名词）。"""
    entities = set()
    # 两字及以上中文词
    for m in re.finditer(r"[一-鿿]{2,}", text):
        entities.add(m.group())
    return entities


def _rule_filter_fact(fact: str) -> str:
    """返回 "objective" | "subjective" | "mixed" | "uncertain"

    mixed: 同时包含主观感知词和客观事件词 → 走 LLM 精判。
           例："他感到左臂断裂" → 感到(主观) + 断裂(客观)
           例："李四觉得空间在扭曲" → 觉得(主观) + 扭曲(客观)
    """
    has_subj = any(re.search(p, fact) for p in _SUBJECTIVE_PATTERNS)
    has_obj  = any(re.search(p, fact) for p in _OBJECTIVE_PATTERNS)

    if has_subj and has_obj:
        return "mixed"
    if has_subj:
        return "subjective"
    if has_obj:
        return "objective"
    return "uncertain"


# ============================================================
# WorldFact
# ============================================================
class WorldFact:
    """一条世界事实。"""

    __slots__ = (
        "fact_id", "category", "fact", "source_section",
        "source_subsection", "immutable", "verified",
        "contradiction_of", "created_at",
    )

    def __init__(
        self,
        category: str,
        fact: str,
        source_section: int = 0,
        source_subsection: int = 0,
        immutable: bool = True,
        verified: bool = False,
        contradiction_of: Optional[str] = None,
        fact_id: Optional[str] = None,
    ):
        self.fact_id = fact_id or str(uuid.uuid4())
        self.category = category
        self.fact = fact
        self.source_section = source_section
        self.source_subsection = source_subsection
        self.immutable = immutable
        self.verified = verified
        self.contradiction_of = contradiction_of
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "category": self.category,
            "fact": self.fact,
            "source_section": self.source_section,
            "source_subsection": self.source_subsection,
            "immutable": self.immutable,
            "verified": self.verified,
            "contradiction_of": self.contradiction_of,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorldFact":
        return cls(
            fact_id=d.get("fact_id"),
            category=d.get("category", ""),
            fact=d.get("fact", ""),
            source_section=d.get("source_section", 0),
            source_subsection=d.get("source_subsection", 0),
            immutable=d.get("immutable", True),
            verified=d.get("verified", False),
            contradiction_of=d.get("contradiction_of"),
        )


# ============================================================
# WorldStateManager
# ============================================================
class WorldStateManager:
    """世界事实管理器。

    存储结构化世界事实（地理、历史、规则、角色事实、关系），
    支持交叉验证、矛盾检测、按需检索、序列化到 Redis 黑板。
    """

    def __init__(self, blackboard, task_id: str, llm_client=None, event_graph=None):
        self._bb = blackboard
        self._task_id = task_id
        self._llm = llm_client
        self._event_graph = event_graph  # v3: 事件图谱作为事实来源
        self._facts: dict[str, WorldFact] = {}
        self._contradictions: list[dict] = []
        self._active_warnings: list[str] = []

        # 从黑板恢复（v3: 优先从 event_graph 同步）
        if self._event_graph:
            self._sync_from_event_graph()
        else:
            self._load()

    # ── 事实 CRUD ──────────────────────────────────────────────

    def _sync_from_event_graph(self):
        """v3: 从 EventGraph 同步 world_fact 类型的事件到本地事实库。"""
        if not self._event_graph:
            return
        for e in self._event_graph._events.values():
            if e.type == "world_fact" and e.status == "established":
                wf = WorldFact(
                    fact_id=e.event_id,
                    category=e.tags[0] if e.tags else "character_fact",
                    fact=e.description,
                    source_section=e.section,
                    source_subsection=e.subsection,
                    immutable=True,
                    verified=True,
                )
                self._facts[e.event_id] = wf
        self._save()

    def add_fact(self, category: str, fact: str, source_section: int = 0,
                 source_subsection: int = 0) -> str:
        """[DEPRECATED v3] 请通过 EventGraph 管理事实。保留用于向后兼容。
        添加一条事实。自动检测矛盾、交叉验证。返回 fact_id。

        规则层分类：
          - subjective → 纯主观（"他觉得自己很帅"），静默丢弃
          - mixed      → 主客观交叠（"他感到左臂断裂"），存储待 LLM 验证
          - objective  → 纯客观，自动验证
          - uncertain  → 不确定，存储待验证
        """
        # 1. 规则预过滤
        rule_result = _rule_filter_fact(fact)
        if rule_result == "subjective":
            return ""  # 纯主观 → 丢弃

        # 2. 矛盾检测
        contradiction = self._detect_contradiction_rule(
            category, fact, source_section
        )
        if contradiction:
            fact_id = str(uuid.uuid4())
            wf = WorldFact(
                category=category,
                fact=fact,
                source_section=source_section,
                source_subsection=source_subsection,
                immutable=False,
                verified=False,
                contradiction_of=contradiction["fact_id"],
                fact_id=fact_id,
            )
            self._facts[fact_id] = wf
            self._contradictions.append({
                "new_fact_id": fact_id,
                "old_fact_id": contradiction["fact_id"],
                "old_fact": contradiction["fact"],
                "new_fact": fact,
                "source_section": source_section,
                "severity": "critical",
            })
            self._active_warnings.append(
                f"⚠ 第{source_section}节发现与第{contradiction['source_section']}节矛盾: "
                f"'{fact}' vs '{contradiction['fact']}'。请在后文中修正或解释。"
            )
            self._save()
            return fact_id

        # 3. 添加事实
        #    objective → 自动验证、不可变
        #    mixed     → 主客观交叠，存储但标记待验证（优先走 LLM 精判）
        #    uncertain → 规则层无法判断，存储待验证
        fact_id = str(uuid.uuid4())
        wf = WorldFact(
            category=category,
            fact=fact,
            source_section=source_section,
            source_subsection=source_subsection,
            immutable=(rule_result == "objective"),
            verified=(rule_result == "objective"),
            fact_id=fact_id,
        )
        self._facts[fact_id] = wf
        self._save()
        return fact_id

    def verify_facts(
        self,
        potential_facts: list[dict],
        mode: str = "both",
    ) -> list[str]:
        """交叉验证潜在事实列表。返回通过验证的 fact 文本列表。

        mode: "rule" — 仅规则过滤（零 LLM 调用）
              "llm"  — 全部送 LLM（最准确）
              "both" — 规则过滤 → 不确定的送 LLM（默认）
        """
        verified = []
        uncertain = []

        for f in potential_facts:
            text = f.get("fact", "")
            result = _rule_filter_fact(text)
            if result == "objective":
                verified.append(text)
            elif result == "subjective":
                continue
            else:
                uncertain.append(f)

        if mode == "rule":
            return verified

        if mode == "both" and uncertain and self._llm:
            llm_verified = self._llm_verify(uncertain)
            verified += llm_verified
        elif mode == "llm" and self._llm:
            verified = self._llm_verify(potential_facts)

        return verified

    def query_relevant(
        self,
        keywords: list[str],
        current_section: int = 0,
        top_k: int = 8,
    ) -> list[dict]:
        """检索与当前场景最相关的事实。按关键词交集 + recency 排序。"""
        if not keywords:
            return [f.to_dict() for f in self._facts.values()][:top_k]

        keyword_set = set(k.lower() for k in keywords)
        scored = []
        for wf in self._facts.values():
            score = 0
            fact_lower = wf.fact.lower()
            for kw in keyword_set:
                if kw.lower() in fact_lower:
                    score += 1
            # verified 事实加权
            if wf.verified:
                score *= 1.5
            # 近期事实加权
            recency_bonus = max(0, 1.0 - 0.1 * abs(current_section - wf.source_section))
            score += recency_bonus
            if score > 0:
                scored.append((score, wf))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [wf.to_dict() for _, wf in scored[:top_k]]

    def get_all_facts(self) -> list[dict]:
        return [f.to_dict() for f in self._facts.values()]

    def get_contradictions(self) -> list[dict]:
        return list(self._contradictions)

    def get_active_warnings(self) -> list[str]:
        return list(self._active_warnings)

    def consume_warnings(self) -> list[str]:
        """取出并清空活跃警告。"""
        warnings = list(self._active_warnings)
        self._active_warnings = []
        return warnings

    # ═══ P7: 元素生命周期 + 章节上下文构建 ═══

    def build_section_context(
        self, outline_node: dict, section_num: int,
        characters: list[dict] | None = None,
    ) -> dict:
        """为指定章节构建世界状态快照。

        Returns: {locked_block, active_block, inactive_block}
        """
        injections = outline_node.get("injections", {}) if isinstance(outline_node, dict) else {}

        # LOCKED: 用户指定本节必须引入的元素
        locked_parts = []
        for field, label in [
            ("new_items", "新物品"), ("new_characters", "新角色"),
            ("new_factions", "新势力"), ("new_locations", "新地点"),
            ("foreshadowing_plant", "埋设伏笔"), ("foreshadowing_resolve", "回收伏笔"),
        ]:
            items = injections.get(field, [])
            if items:
                locked_parts.append(f"【{label}】{', '.join(items)} — 必须在正文中出现")

        locked_block = "\n".join(locked_parts) if locked_parts else "（本节无指定引入元素）"

        # ACTIVE: 当前活跃的元素
        active_parts = []
        if characters:
            active_chars = [c for c in characters if c.get("status", "active") == "active"]
            if active_chars:
                names = [f"{c['name']}" for c in active_chars[:8]]
                active_parts.append(f"【角色】{', '.join(names)}")

        # 从 faction_store 获取活跃势力
        try:
            from . import faction_store as fs
            factions = fs.list_factions(self._task_id)
            active_factions = [f for f in factions if f.get("is_active")]
            if active_factions:
                f_names = [f"{f['name']}({f.get('type','')})" for f in active_factions[:6]]
                active_parts.append(f"【势力】{', '.join(f_names)}")
        except Exception:
            pass

        active_block = "\n".join(active_parts) if active_parts else "（无活跃元素）"

        # INACTIVE: 已退出的元素
        inactive_parts = []
        if characters:
            dead = [c for c in characters if c.get("status") == "inactive"]
            if dead:
                inactive_parts.append(f"【已退出角色】{', '.join(c['name'] for c in dead)} — 请勿引用")

        inactive_block = "\n".join(inactive_parts) if inactive_parts else "（无）"

        return {
            "locked_block": locked_block,
            "active_block": active_block,
            "inactive_block": inactive_block,
        }

    def update_element_state(self, element_type: str, element_name: str,
                              new_status: str, chapter: int = 0):
        """元素状态变更：角色死亡/物品损毁/势力关系变化。"""
        import logging
        log = logging.getLogger("writing_system.world_state")
        log.info(f"[ws] 状态变更: {element_type}.{element_name} → {new_status} (ch{chapter})")
        # 记录到黑板的 world_state 变更日志
        if self._bb:
            self._bb.xadd_event(self._task_id, {
                "event": "element_state_change",
                "element_type": element_type,
                "element_name": element_name,
                "new_status": new_status,
                "chapter": chapter,
            })

    # ── 内部 ────────────────────────────────────────────────────

    def _detect_contradiction_rule(
        self, category: str, fact: str, source_section: int
    ) -> Optional[dict]:
        """规则层矛盾检测：同 category + 实体交集 → 候选矛盾。"""
        new_entities = _extract_entities(fact)
        if not new_entities:
            return None

        for wf in self._facts.values():
            if wf.category != category:
                continue
            old_entities = _extract_entities(wf.fact)
            common = new_entities & old_entities
            if common and wf.source_section != source_section:
                # 有共同实体且在不同节 → 调用 LLM 精判
                if self._llm:
                    return self._llm_detect_contradiction(
                        {"category": category, "fact": fact},
                        wf,
                    )
                # 无 LLM 时保守处理：有交集即标记
                return wf.to_dict()
        return None

    def _llm_verify(self, facts: list[dict]) -> list[str]:
        """LLM 交叉验证：区分客观事实 vs 主观感受。"""
        if not self._llm:
            return [f["fact"] for f in facts]

        facts_json = json.dumps(
            [{"fact": f["fact"], "category": f.get("category", "")} for f in facts],
            ensure_ascii=False,
            indent=2,
        )
        prompt = f"""以下是从最新章节提取的潜在事实。请逐一核查：
1. 标记出哪些是 100% 成立的客观事实（可被多个角色独立观察证实）
2. 标记出哪些是角色主观感受或推测
3. 只有客观事实才能标记为 verified

潜在事实列表：
{facts_json}

请以 JSON 数组格式输出：
[
  {{"fact": "原文", "verified": true或false, "reason": "一句话理由"}}
]"""

        messages = [
            {"role": "system", "content": "你是一位严谨的事实核查员。请以 JSON 数组格式输出。"},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = self._llm.chat_completion(messages, temperature=0.2, max_tokens=800)
            from .utils.json_parser import parse_json
            result = parse_json(resp)
            if isinstance(result, list):
                return [r["fact"] for r in result if r.get("verified")]
            return []
        except Exception:
            logger.warning("LLM 事实验证失败，回退到全部通过")
            return [f["fact"] for f in facts]

    def _llm_detect_contradiction(
        self, new_fact: dict, old_wf: WorldFact
    ) -> Optional[dict]:
        """LLM 精判：两个事实是否构成矛盾。"""
        if not self._llm:
            return old_wf.to_dict()

        prompt = f"""请判断以下两个事实是否存在矛盾。

事实 A（旧 — 第{old_wf.source_section}节）：
[{old_wf.category}] {old_wf.fact}

事实 B（新 — 当前节）：
[{new_fact['category']}] {new_fact['fact']}

请以 JSON 格式输出：
{{"contradiction": true或false, "reason": "一句话理由"}}

判断标准：
- 如果 B 与 A 在逻辑上不可共存（如"李四断了左臂"vs"李四双手持剑"），标记为矛盾
- 如果 B 只是补充或细化 A（如"旧工厂建于2050年"vs"旧工厂的锅炉房建于2052年"），不算矛盾
- 如果 B 与 A 涉及不同对象或时间点，不算矛盾"""

        messages = [
            {"role": "system", "content": "你是一位严谨的逻辑检查员。请以 JSON 格式输出。"},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = self._llm.chat_completion(messages, temperature=0.2, max_tokens=400)
            from .utils.json_parser import parse_json
            result = parse_json(resp)
            if isinstance(result, dict) and result.get("contradiction"):
                return old_wf.to_dict()
        except Exception:
            pass
        return None

    def serialize(self) -> dict:
        return {
            "facts": [f.to_dict() for f in self._facts.values()],
            "contradictions": self._contradictions,
            "active_warnings": self._active_warnings,
        }

    # ── 持久化 ──────────────────────────────────────────────────

    def _save(self) -> None:
        if self._bb:
            self._bb.set(self._task_id, "world_state", self.serialize())

    def _load(self) -> None:
        if not self._bb:
            return
        raw = self._bb.get(self._task_id, "world_state")
        if not raw:
            return
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
            data = raw if isinstance(raw, dict) else {}
            for fd in data.get("facts", []):
                wf = WorldFact.from_dict(fd)
                self._facts[wf.fact_id] = wf
            self._contradictions = data.get("contradictions", [])
            self._active_warnings = data.get("active_warnings", [])
        except (json.JSONDecodeError, TypeError, KeyError):
            logger.warning("世界状态恢复失败，从空开始")
