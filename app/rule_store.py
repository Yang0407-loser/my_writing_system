"""规则中心存储 —— SQLite 持久化，支持导入导出。"""

import sqlite3
import json
import uuid
import os
from pathlib import Path
from .config import settings


RULES_DB_PATH = os.path.join(
    os.path.dirname(settings.TASK_DB_PATH),
    "rules.db"
)


def _get_conn() -> sqlite3.Connection:
    Path(RULES_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(RULES_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            content TEXT NOT NULL,
            type TEXT DEFAULT 'global',
            priority INTEGER DEFAULT 5,
            scope TEXT DEFAULT 'global',
            enabled INTEGER DEFAULT 1,
            created_by TEXT DEFAULT 'user',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule_presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            rules_json TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled", 1))
    return d


# ── CRUD ─────────────────────────────────────────────────────────

def list_rules(enabled_only: bool = False) -> list[dict]:
    conn = _get_conn()
    try:
        sql = "SELECT * FROM rules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY priority DESC, updated_at DESC"
        return [_row_to_dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def get_rule(rule_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def create_rule(rule: dict) -> dict:
    conn = _get_conn()
    try:
        rule_id = rule.get("id") or str(uuid.uuid4())
        conn.execute("""
            INSERT OR REPLACE INTO rules (id, name, description, content, type, priority, scope, enabled, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rule_id,
            rule.get("name", ""),
            rule.get("description", ""),
            rule.get("content", ""),
            rule.get("type", "global"),
            rule.get("priority", 5),
            rule.get("scope", "global"),
            1 if rule.get("enabled", True) else 0,
            rule.get("created_by", "user"),
        ))
        conn.commit()
        return get_rule(rule_id)
    finally:
        conn.close()


def update_rule(rule_id: str, updates: dict) -> dict | None:
    conn = _get_conn()
    try:
        existing = get_rule(rule_id)
        if not existing:
            return None
        merged = {**existing, **updates, "id": rule_id}
        conn.execute("""
            UPDATE rules SET name=?, description=?, content=?, type=?, priority=?, scope=?, enabled=?, updated_at=datetime('now')
            WHERE id=?
        """, (
            merged["name"], merged["description"], merged["content"],
            merged["type"], merged["priority"], merged["scope"],
            1 if merged["enabled"] else 0, rule_id,
        ))
        conn.commit()
        return get_rule(rule_id)
    finally:
        conn.close()


def delete_rule(rule_id: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


# ── 导入导出 ────────────────────────────────────────────────────

def export_rules(rule_ids: list[str] | None = None) -> str:
    """将规则导出为 JSON 字符串（规则包格式）。"""
    conn = _get_conn()
    try:
        if rule_ids:
            placeholders = ",".join("?" * len(rule_ids))
            rows = conn.execute(
                f"SELECT * FROM rules WHERE id IN ({placeholders})", rule_ids
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rules ORDER BY priority DESC").fetchall()
        rules_list = [_row_to_dict(r) for r in rows]
        return json.dumps({"version": "1.0", "rules": rules_list}, ensure_ascii=False, indent=2)
    finally:
        conn.close()


def import_rules(json_str: str, on_conflict: str = "skip") -> list[dict]:
    """从 JSON 字符串导入规则。

    on_conflict: 'skip'（跳过已有ID）| 'overwrite'（覆盖）| 'duplicate'（创建新ID）
    """
    data = json.loads(json_str)
    rules_list = data.get("rules", []) if isinstance(data, dict) else data
    imported = []
    for rule in rules_list:
        if on_conflict == "duplicate":
            rule.pop("id", None)
        elif on_conflict == "skip" and rule.get("id") and get_rule(rule["id"]):
            continue
        imported.append(create_rule(rule))
    return imported


# ── 预设 ────────────────────────────────────────────────────────

def seed_presets():
    """初始化内置预设规则（按名称去重，幂等）。"""
    conn = _get_conn()
    try:
        import hashlib
        presets = [
            {"name": "禁止主角杀人", "description": "主角在任何情况下都不能主动杀人",
             "content": "主角绝对不能主动杀害任何人。即使面对仇敌，也必须通过其他方式解决冲突。",
             "type": "character", "priority": 10, "created_by": "system"},
            {"name": "每章必有对话", "description": "确保每章正文至少包含3段角色对话",
             "content": "每一章正文中必须至少包含3段不同角色之间的对话，避免纯叙述。",
             "type": "dialogue", "priority": 8, "created_by": "system"},
            {"name": "古龙短句风格", "description": "以短句为主，模仿古龙武侠小说的文风",
             "content": "以短句为主，单句不超过20字。多用断句和分行，营造冷峻的氛围。减少形容词，多用动作和对话推进剧情。",
             "type": "style", "priority": 7, "created_by": "system"},
            {"name": "禁止AI套话", "description": "禁止使用AI常见的套路化表达",
             "content": "禁止使用以下AI常见套话：'在这个充满XX的世界里'、'X不仅是一种Y，更是一种Z'、'随着时间的推移'、'他的眼中闪过一丝XX'。如果出现这些表达，必须替换为原创表达。",
             "type": "style", "priority": 9, "created_by": "system"},
            {"name": "伏笔必须回收", "description": "确保每一个埋下的伏笔在后续章节中有回收",
             "content": "每一个埋设的伏笔必须在10章之内有明确的回收或推进。伏笔不能无限期悬置。如果某个伏笔在当前章节应该回收但没有合适的时机，至少要给读者一个提示或进展。",
             "type": "plot", "priority": 8, "created_by": "system"},
            {"name": "情绪节奏变化", "description": "避免连续章节情绪单一",
             "content": "连续章节之间必须有情绪节奏的变化。不能连续三章以上保持同一情绪基调。紧张之后应有放松，悲伤之后应有希望，高潮之后应有平缓过渡。",
             "type": "plot", "priority": 6, "created_by": "system"},
            {"name": "配角功能性", "description": "确保每个出场配角都有叙事功能",
             "content": "每个在当前章节出场的配角必须有明确的叙事功能：推进主线/揭示信息/制造冲突/烘托主角/埋设伏笔。不能出现纯粹凑字数的路人角色。",
             "type": "character", "priority": 5, "created_by": "system"},
            {"name": "场景描写三要素", "description": "每个场景至少包含视觉+听觉+感受",
             "content": "每个新场景至少包含三种感官描写：视觉（看到的）、听觉（听到的）、身体感受（冷/热/疼痛/舒适等）。避免只有视觉描写的单调场景。",
             "type": "style", "priority": 4, "created_by": "system"},
            {"name": "对话区分角色", "description": "确保不同角色有可区分的说话风格",
             "content": "每个角色的对话必须有可辨识的风格差异：用词偏好、句式长度、语气词使用、敬语程度等。不能让所有角色听起来像同一个人。",
             "type": "dialogue", "priority": 5, "created_by": "system"},
            {"name": "章节结尾钩子", "description": "每章结尾必须有悬念或钩子",
             "content": "每一章的结尾必须留下悬念、未解之谜或强烈的继续阅读欲望。可以使用：突发事件的预告、关键信息的半揭露、角色面临的选择、意外人物的登场等手法。",
             "type": "plot", "priority": 7, "created_by": "system"},
        ]
        for rule in presets:
            # 用 name 生成确定性 ID，INSERT OR REPLACE 保证幂等
            rule_id = hashlib.md5(f"preset:{rule['name']}".encode()).hexdigest()[:16]
            conn.execute("""
                INSERT OR REPLACE INTO rules (id, name, description, content, type, priority, scope, enabled, created_by)
                VALUES (?, ?, ?, ?, ?, ?, 'global', 1, ?)
            """, (
                rule_id, rule["name"], rule["description"], rule["content"],
                rule["type"], rule["priority"], rule["created_by"],
            ))
        conn.commit()
    finally:
        conn.close()


def build_rules_context(enabled_only: bool = True) -> str:
    """构建注入 prompt 的规则上下文文本。"""
    ensure_presets_seeded()
    rules = list_rules(enabled_only=enabled_only)
    if not rules:
        return ""
    lines = ["## 用户自定义规则（优先级执行）"]
    for i, r in enumerate(rules, 1):
        lines.append(f"{i}. [优先级{r['priority']}] {r['content']}")
    return "\n".join(lines)


_presets_seeded = False


def ensure_presets_seeded():
    """幂等初始化预设规则（首次调用时执行，后续调用跳过）。"""
    global _presets_seeded
    if _presets_seeded:
        return
    seed_presets()
    _presets_seeded = True
