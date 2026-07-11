"""任务分析与审阅 API。"""

from fastapi import APIRouter, Header, HTTPException
from ..dependencies import bb
from ..utils.endpoint_helpers import assemble_draft_from_checkpoint
from ..utils.llm_client import set_api_key

router = APIRouter(tags=["analysis"])


@router.post("/tasks/{task_id}/review/continuity")
def review_continuity(task_id: str, x_api_key: str = Header("", alias="X-API-Key")):
    """评估任务所有相邻章节之间的承续质量。"""
    if x_api_key:
        set_api_key(x_api_key)
    checkpoint = bb.load_checkpoint(task_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="检查点不存在")

    section_texts = checkpoint.get("section_texts", {})
    if len(section_texts) < 2:
        return {"results": [], "message": "至少需要 2 节才能评估承续"}

    from ..agents.reviewer import Reviewer
    reviewer = Reviewer()
    results = reviewer.review_continuity_chain(
        {int(k): v for k, v in section_texts.items()},
        checkpoint.get("handover_chain") or [],
        checkpoint.get("characters"),
        checkpoint.get("character_arcs"),
    )

    scores = [r.get("continuity_score", 6) for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0

    return {
        "task_id": task_id,
        "average_continuity_score": round(avg_score, 1),
        "total_transitions": len(results),
        "results": results,
    }


@router.get("/tasks/{task_id}/events")
def get_task_events(task_id: str):
    """获取任务的叙事事件图谱。"""
    from ..narrative_event import EventGraph
    eg = EventGraph(bb, task_id)
    events = [e.to_dict() for e in eg._events.values()]
    summary = eg.get_summary()
    return {"events": events, "summary": summary}


@router.post("/tasks/{task_id}/analyze")
def analyze_task(task_id: str, x_api_key: str = Header("", alias="X-API-Key")):
    """写后分析：人物关系图 + 时间链。"""
    if x_api_key:
        set_api_key(x_api_key)
    checkpoint = bb.load_checkpoint(task_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="检查点不存在")

    draft = assemble_draft_from_checkpoint(checkpoint)
    if not draft:
        raise HTTPException(status_code=400, detail="无可分析的草稿")

    from ..task_store import TaskStore
    from ..utils.llm_client import get_llm_client
    from ..utils.json_parser import parse_json
    import json as _json

    llm = get_llm_client()
    draft_preview = draft[:15000]
    characters = checkpoint.get("characters") or []
    chars_json = _json.dumps(characters, ensure_ascii=False, indent=2)

    prompt = f"""你是一位文学分析专家。请分析以下小说文本，提取：
1. 人物关系图谱（每个关系包含两个人物、关系类型、羁绊深度1-10、演化历史一句话）
2. 时间线事件（按节号排序，每个事件关联到具体人物和节号）

人物设定：
{chars_json}

小说正文（前15000字）：
{draft_preview}

请以 JSON 格式输出：
{{
    "relations": [
        {{"char_a": "人物A", "char_b": "人物B", "type": "敌对/友好/师徒/恋人/家人/同事/中立",
          "depth": 7, "evolution": "从最初的不信任逐渐发展为..."}}
    ],
    "timeline": {{
        "events": [
            {{"section": 1, "character": "江辰", "event": "发现蓝色星盘", "location": "废弃星域", "time": "深秋黄昏"}}
        ]
    }}
}}"""

    resp = llm.chat_completion(
        [{"role": "system", "content": "你是一位文学分析专家。请以 JSON 格式输出。"},
         {"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=3000,
    )

    try:
        analysis = parse_json(resp)
    except ValueError:
        analysis = {"relations": [], "timeline": {"events": []}, "raw": resp[:500]}

    # 持久化到 SQLite
    try:
        ts = TaskStore()
        existing = ts.get(task_id)
        if existing:
            ts.save(task_id, {**{
                "topic": existing.get("topic", ""),
                "word_count": existing.get("word_count", 0),
                "section_count": existing.get("section_count", 0),
                "status": existing.get("status", "completed"),
                "mode": existing.get("mode", "celery"),
                "style": existing.get("style_json", {}),
                "outline": existing.get("outline_json", []),
                "handover_notes": existing.get("handover_json", []),
                "characters": existing.get("characters_json", []),
                "review": existing.get("review_json", {}),
                "world_setting": existing.get("world_setting", ""),
                "story_synopsis": existing.get("story_synopsis", ""),
                "target_words": existing.get("target_words", 0),
                "world_state": existing.get("world_state_json", {}),
                "draft": existing.get("draft_preview", ""),
                "output_file": existing.get("output_file", ""),
                "events": existing.get("events_json", []),
            }, "analysis": analysis})
    except Exception:
        pass

    return analysis
