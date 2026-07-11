import sqlite3
import uuid
import json
import logging

logger = logging.getLogger(__name__)


class CharacterStore:
    """SQLite 角色库封装。双表：characters + character_traits。"""

    JSON_FIELDS = ["key_lines", "relationships", "custom"]
    TRAIT_FIELDS = ["personality", "strengths", "weaknesses"]

    _KNOWN_COLUMNS = {
        "id", "name", "gender", "age", "motivation", "background",
        "appearance", "catchphrase", "secret", "world_position",
        "symbolism", "key_lines", "relationships", "custom",
        "previous_life", "previous_world", "preserved_knowledge", "identity_conflict",
    }

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            from .config import settings
            db_path = settings.CHARACTER_DB_PATH
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
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
                previous_life TEXT DEFAULT '',
                previous_world TEXT DEFAULT '',
                preserved_knowledge TEXT DEFAULT '',
                identity_conflict TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # 迁移：添加隐层字段
        for col in ("previous_life", "previous_world", "preserved_knowledge", "identity_conflict"):
            try: self._conn.execute(f"ALTER TABLE characters ADD COLUMN {col} TEXT DEFAULT ''"); self._conn.commit()
            except Exception: pass
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS character_traits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                trait_type TEXT NOT NULL,
                trait_value TEXT NOT NULL,
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traits_type_value ON character_traits(trait_type, trait_value)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traits_character ON character_traits(character_id)"
        )
        self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        for f in self.JSON_FIELDS:
            if f in d and isinstance(d[f], str):
                try:
                    d[f] = json.loads(d[f])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"JSON 字段 '{f}' 解析失败，使用默认值")
                    d[f] = [] if f in ("key_lines", "relationships") else {}
        return d

    def _dict_to_row(self, d: dict) -> dict:
        result = {}
        for k, v in d.items():
            if k in self.TRAIT_FIELDS or k in ("created_at", "updated_at"):
                continue
            if k not in self._KNOWN_COLUMNS:
                continue
            if k in self.JSON_FIELDS and not isinstance(v, str):
                result[k] = json.dumps(v, ensure_ascii=False)
            else:
                result[k] = v
        return result

    def _save_traits(self, char_id: str, traits: dict):
        self._conn.execute("DELETE FROM character_traits WHERE character_id = ?", (char_id,))
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
        rows = self._conn.execute(
            "SELECT trait_type, trait_value FROM character_traits WHERE character_id = ?", (char_id,)
        ).fetchall()
        traits = {"personality": [], "strengths": [], "weaknesses": []}
        for r in rows:
            t = r["trait_type"]
            if t in traits:
                traits[t].append(r["trait_value"])
        return traits

    def create(self, char: dict) -> dict:
        char_id = char.get("id") or str(uuid.uuid4())
        row = self._dict_to_row(char)
        row["id"] = char_id
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        self._conn.execute(
            f"INSERT INTO characters ({cols}) VALUES ({placeholders})", list(row.values())
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
        result = []
        for r in rows:
            d = self._row_to_dict(r)
            d.update(self._load_traits(r["id"]))
            result.append(d)
        return result

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
