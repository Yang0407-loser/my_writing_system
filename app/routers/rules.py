"""规则中心 API —— CRUD + 导入导出。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from .. import rule_store

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RuleBody(BaseModel):
    name: str = ""
    description: str = ""
    content: str = ""
    type: str = "global"
    priority: int = 5
    scope: str = "global"
    enabled: bool = True
    created_by: str = "user"


class ImportBody(BaseModel):
    json_str: str
    on_conflict: str = "skip"  # skip | overwrite | duplicate


# ── CRUD ─────────────────────────────────────────────────────────

@router.get("")
def list_rules(enabled_only: bool = Query(False)):
    rules = rule_store.list_rules(enabled_only=enabled_only)
    return {"rules": rules, "total": len(rules)}


@router.get("/context")
def get_rules_context():
    """获取注入 prompt 的规则上下文字符串。"""
    return {"context": rule_store.build_rules_context()}


@router.get("/{rule_id}")
def get_rule(rule_id: str):
    rule = rule_store.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return rule


@router.post("")
def create_rule(body: RuleBody):
    rule = rule_store.create_rule(body.model_dump())
    return rule


@router.put("/{rule_id}")
def update_rule(rule_id: str, body: RuleBody):
    rule = rule_store.update_rule(rule_id, body.model_dump(exclude_unset=True))
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return rule


@router.delete("/{rule_id}")
def delete_rule(rule_id: str):
    ok = rule_store.delete_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "deleted"}


# ── 导入导出 ────────────────────────────────────────────────────

@router.post("/export")
def export_rules(rule_ids: list[str] | None = None):
    json_str = rule_store.export_rules(rule_ids)
    return {"json": json_str}


@router.post("/import")
def import_rules(body: ImportBody):
    imported = rule_store.import_rules(body.json_str, body.on_conflict)
    return {"imported": len(imported), "rules": imported}


# ── 预设 ────────────────────────────────────────────────────────

@router.get("/presets/list")
def list_presets():
    """返回所有系统预设规则的简略列表。"""
    rules = rule_store.list_rules()
    presets = [r for r in rules if r.get("created_by") == "system"]
    return {"presets": presets}
