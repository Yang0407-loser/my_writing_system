"""角色关系管理器 —— 关系弧演化 + Writer 上下文注入。"""

import json
import os
import uuid
from pathlib import Path

from .config import settings

DB_PATH = os.path.join(os.path.dirname(settings.TASK_DB_PATH), "character_relations.db")

PRESET_RELATION_TYPES = [
    "挚友", "恋人", "夫妻", "师徒", "宿敌", "血亲", "盟友",
    "情敌", "主仆", "陌路", "仇敌", "青梅竹马", "一面之缘", "暗中仰慕",
]
DIRECTION_TYPES = ["positive", "negative", "complex"]
DIRECTION_LABELS = {"positive": "正向", "negative": "负向", "complex": "复杂"}


def _get_conn():
    import sqlite3
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS character_relations (
            id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            character_a TEXT NOT NULL,
            character_b TEXT NOT NULL,
            relation_type TEXT DEFAULT '盟友',
            direction TEXT DEFAULT 'positive',
            intensity INTEGER DEFAULT 5,
            stages TEXT DEFAULT '[]',
            current_stage INTEGER DEFAULT 0,
            source TEXT DEFAULT 'manual',
            source_section INTEGER DEFAULT 0,
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crel_task ON character_relations(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crel_chara ON character_relations(character_a)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_crel_charb ON character_relations(character_b)")
    conn.commit()
    return conn


def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    """保证 pair 内字母序小的在前，确保唯一性。"""
    return (a, b) if a <= b else (b, a)


def _row_to_dict(row) -> dict:
    d = dict(row)
    for f in ("stages",):
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                d[f] = []
    return d


# ═══ CRUD ═══

def create_relation(data: dict) -> dict:
    conn = _get_conn()
    try:
        rid = data.get("id") or str(uuid.uuid4())
        a, b = _normalize_pair(data.get("character_a", ""), data.get("character_b", ""))
        stages = json.dumps(data.get("stages", []), ensure_ascii=False)
        conn.execute("""
            INSERT OR REPLACE INTO character_relations
            (id, task_id, character_a, character_b, relation_type, direction,
             intensity, stages, current_stage, source, source_section, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (rid, data.get("task_id", ""), a, b,
              data.get("relation_type", "盟友"), data.get("direction", "positive"),
              data.get("intensity", 5), stages, data.get("current_stage", 0),
              data.get("source", "manual"), data.get("source_section", 0),
              data.get("description", "")))
        conn.commit()
        return get_relation(rid)
    finally:
        conn.close()


def get_relation(rid: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM character_relations WHERE id = ?", (rid,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_relations(task_id: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        if task_id:
            rows = conn.execute(
                "SELECT * FROM character_relations WHERE task_id = ? ORDER BY intensity DESC",
                (task_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM character_relations ORDER BY intensity DESC").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def find_relation_by_pair(task_id: str, char_a: str, char_b: str) -> dict | None:
    """查找指定角色对的关系（已存在则返回，否则 None）。"""
    conn = _get_conn()
    try:
        a, b = _normalize_pair(char_a, char_b)
        row = conn.execute(
            "SELECT * FROM character_relations WHERE task_id = ? AND character_a = ? AND character_b = ?",
            (task_id, a, b)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def update_relation(rid: str, updates: dict) -> dict | None:
    conn = _get_conn()
    try:
        existing = get_relation(rid)
        if not existing:
            return None
        merged = {**existing, **updates, "id": rid}
        if "character_a" in updates or "character_b" in updates:
            a, b = _normalize_pair(
                merged.get("character_a", ""), merged.get("character_b", ""))
            merged["character_a"] = a
            merged["character_b"] = b
        stages = json.dumps(merged.get("stages", []), ensure_ascii=False)
        conn.execute("""
            UPDATE character_relations SET
                character_a=?, character_b=?, relation_type=?, direction=?,
                intensity=?, stages=?, current_stage=?, source=?,
                source_section=?, description=?, updated_at=datetime('now')
            WHERE id=?
        """, (merged["character_a"], merged["character_b"], merged["relation_type"],
              merged["direction"], merged["intensity"], stages,
              merged["current_stage"], merged.get("source", "manual"),
              merged.get("source_section", 0), merged.get("description", ""), rid))
        conn.commit()
        return get_relation(rid)
    finally:
        conn.close()


def delete_relation(rid: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM character_relations WHERE id = ?", (rid,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ═══ 关系弧推进 ═══

def advance_stage(rid: str, stage_index: int, status: str = "done") -> dict | None:
    """将指定阶段标记为 done/active，自动推进 current_stage。"""
    rel = get_relation(rid)
    if not rel:
        return None
    stages = rel.get("stages", [])
    if stage_index < 0 or stage_index >= len(stages):
        return None
    stages[stage_index]["status"] = status
    # 如果标记为 done 的是当前阶段，推进到下一个 pending
    if status == "done" and stage_index == rel.get("current_stage", 0):
        next_idx = stage_index + 1
        if next_idx < len(stages):
            stages[next_idx]["status"] = "active"
            return update_relation(rid, {"stages": stages, "current_stage": next_idx})
    return update_relation(rid, {"stages": stages})


# ═══ AI 提取 ═══

EXTRACTION_PROMPT = """你是一位专业的小说编辑，请从以下正文中提取角色之间的关系变化。

已有角色列表：{character_names}

请以 JSON 格式返回本段正文中出现的**新的或变化的关系**：
```json
{{
  "relations": [
    {{
      "character_a": "角色名",
      "character_b": "角色名",
      "relation_type": "当前关系（如：挚友/恋人/师徒/宿敌/血亲/盟友/情敌/主仆/陌路/仇敌/青梅竹马/一面之缘/暗中仰慕/自定义）",
      "direction": "positive/negative/complex",
      "intensity": 5,
      "new_stage": {{
        "stage": "本段中到达的新阶段描述",
        "trigger": "触发这个变化的事件"
      }},
      "description": "当前关系状态的自然语言描述"
    }}
  ]
}}
```

规则：
- 只返回本段正文中**明确体现**的关系变化，不要猜测
- 如果本段没有明显的关系变化，返回 {{"relations": []}}
- character_a 和 character_b 必须来自已有角色列表
- intensity 1-10，1=几乎无关，10=生死羁绊
"""


def extract_relations_from_text(section_text: str, task_id: str,
                                character_names: list[str],
                                section_num: int = 0,
                                llm_call=None) -> list[dict]:
    """从正文中提取角色关系变化，自动合并到已有关系。"""
    if not llm_call or not section_text.strip() or len(character_names) < 2:
        return []

    prompt = EXTRACTION_PROMPT.format(character_names=", ".join(character_names))
    try:
        raw = llm_call(prompt, system="你是一位专业的小说编辑，擅长分析角色关系。", max_tokens=800)
        # 提取 JSON
        json_start = raw.find("{")
        json_end = raw.rfind("}") + 1
        if json_start == -1 or json_end <= json_start:
            return []
        data = json.loads(raw[json_start:json_end])
        extracted = data.get("relations", [])
    except (json.JSONDecodeError, Exception):
        return []

    results = []
    for rel_data in extracted:
        char_a = rel_data.get("character_a", "")
        char_b = rel_data.get("character_b", "")
        if char_a == char_b or not char_a or not char_b:
            continue
        # 检查角色名是否在列表中
        if char_a not in character_names and char_b not in character_names:
            continue

        # 查找是否已存在
        existing = find_relation_by_pair(task_id, char_a, char_b)
        new_stage = rel_data.get("new_stage", {})

        if existing:
            stages = existing.get("stages", [])
            if new_stage and new_stage.get("stage"):
                new_section = new_stage.get("trigger_section", section_num)
                stages.append({
                    "stage": new_stage["stage"],
                    "section": new_section,
                    "trigger": new_stage.get("trigger", ""),
                    "status": "active"
                })
                # 将前一个 active 改为 done
                for s in stages[:-1]:
                    if s.get("status") == "active":
                        s["status"] = "done"
                updated = update_relation(existing["id"], {
                    "stages": stages,
                    "current_stage": len(stages) - 1,
                    "relation_type": rel_data.get("relation_type", existing["relation_type"]),
                    "direction": rel_data.get("direction", existing["direction"]),
                    "intensity": rel_data.get("intensity", existing["intensity"]),
                    "description": rel_data.get("description", existing.get("description", "")),
                    "source": "ai_extracted",
                    "source_section": section_num,
                })
                if updated:
                    results.append(updated)
            else:
                # 无新阶段，只更新描述和强度
                updated = update_relation(existing["id"], {
                    "relation_type": rel_data.get("relation_type", existing["relation_type"]),
                    "direction": rel_data.get("direction", existing["direction"]),
                    "intensity": rel_data.get("intensity", existing["intensity"]),
                    "description": rel_data.get("description", existing.get("description", "")),
                    "source": "ai_extracted",
                    "source_section": section_num,
                })
                if updated:
                    results.append(updated)
        else:
            # 新建关系
            stages = []
            if new_stage and new_stage.get("stage"):
                stages = [{
                    "stage": new_stage["stage"],
                    "section": section_num,
                    "trigger": new_stage.get("trigger", ""),
                    "status": "active"
                }]
            created = create_relation({
                "task_id": task_id,
                "character_a": char_a,
                "character_b": char_b,
                "relation_type": rel_data.get("relation_type", "盟友"),
                "direction": rel_data.get("direction", "positive"),
                "intensity": rel_data.get("intensity", 5),
                "stages": stages,
                "current_stage": 0 if stages else 0,
                "source": "ai_extracted",
                "source_section": section_num,
                "description": rel_data.get("description", ""),
            })
            results.append(created)

    return results


# ═══ Writer 上下文构建 ═══

def build_relation_context(task_id: str, section_num: int = 0) -> str:
    """构建注入 Writer prompt 的角色关系上下文。"""
    relations = list_relations(task_id)
    if not relations:
        return ""

    lines = []
    for r in relations:
        stages = r.get("stages", [])
        current_idx = r.get("current_stage", 0)

        # 阶段可视化简写
        stage_summary = ""
        if stages:
            parts = []
            for i, s in enumerate(stages):
                icon = {"done": "✓", "active": "●", "pending": "○"}.get(s.get("status", "pending"), "○")
                parts.append(f"{icon}{s['stage']}")
            stage_summary = " → ".join(parts)

        dir_label = DIRECTION_LABELS.get(r.get("direction", "positive"), "正向")
        current_stage_name = ""
        if stages and 0 <= current_idx < len(stages):
            current_stage_name = stages[current_idx].get("stage", "")

        lines.append(
            f"【{r['character_a']} ↔ {r['character_b']}】{r['relation_type']} | "
            f"{dir_label} | 羁绊 {r['intensity']}/10"
        )
        if stage_summary:
            lines.append(f"  关系弧: {stage_summary}")
        if current_stage_name:
            lines.append(f"  当前阶段: {current_stage_name}")
        if r.get("description"):
            lines.append(f"  状态: {r['description']}")

    return "## 角色关系状态\n" + "\n".join(lines) if lines else ""
