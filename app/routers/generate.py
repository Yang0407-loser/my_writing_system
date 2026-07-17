"""AI 生成 API。"""

from fastapi import APIRouter, Header, HTTPException
from ..utils.llm_client import get_llm_client, set_api_key
from ..utils.json_parser import parse_json

router = APIRouter(prefix="/api/generate", tags=["generate"])


def _use_key(x_api_key: str) -> None:
    """Set per-request API key if provided."""
    if x_api_key:
        set_api_key(x_api_key)


@router.post("/world-setting")
def generate_world_setting(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    _use_key(x_api_key)
    topic = (body or {}).get("topic", "")
    if not topic or not topic.strip():
        raise HTTPException(status_code=400, detail="topic 不能为空")
    llm = get_llm_client()
    prompt = f"""请为以下小说主题设计世界观设定（200-500字），包含时代背景、地点、规则、氛围。

主题：{topic}

直接输出世界观描述文本，不要加标题。"""
    resp = llm.chat_completion(
        [{"role": "system", "content": "你是一位科幻/奇幻世界观设计专家。"},
         {"role": "user", "content": prompt}],
        temperature=0.7, max_tokens=800,
    )
    return {"world_setting": resp.strip()}


@router.post("/story-synopsis")
def generate_story_synopsis(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    _use_key(x_api_key)
    topic = (body or {}).get("topic", "")
    world_setting = (body or {}).get("world_setting", "")
    if not topic.strip():
        raise HTTPException(status_code=400, detail="topic 不能为空")
    llm = get_llm_client()
    ws = f"世界观：{world_setting}" if world_setting.strip() else ""
    prompt = f"""请为以下小说主题设计三幕式故事梗概（300-500字）。

主题：{topic}
{ws}

直接输出梗概文本，不要加标题。"""
    resp = llm.chat_completion(
        [{"role": "system", "content": "你是一位专业的故事情节设计师。"},
         {"role": "user", "content": prompt}],
        temperature=0.7, max_tokens=800,
    )
    return {"story_synopsis": resp.strip()}


@router.post("/subsection-descriptions")
def generate_subsection_descriptions(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    _use_key(x_api_key)
    import json
    topic = (body or {}).get("topic", "")
    world_setting = (body or {}).get("world_setting", "")
    story_synopsis = (body or {}).get("story_synopsis", "")
    outline = (body or {}).get("outline", [])
    if not outline:
        raise HTTPException(status_code=400, detail="outline 不能为空")
    llm = get_llm_client()
    prompt = f"""请为以下大纲的每个小节生成一句话梗概（description）。

主题：{topic}
{'世界观：' + world_setting if world_setting.strip() else ''}
{'故事梗概：' + story_synopsis if story_synopsis.strip() else ''}

大纲：
{json.dumps(outline, ensure_ascii=False, indent=2)}

请以 JSON 数组格式输出，每个元素包含 section, subsection, description：
[{{"section": 1, "subsection": 1, "description": "梗概..."}}, ...]"""
    resp = llm.chat_completion(
        [{"role": "system", "content": "你是一位大纲策划编辑。请以 JSON 数组格式输出。"},
         {"role": "user", "content": prompt}],
        temperature=0.5, max_tokens=2000,
    )
    try:
        results = parse_json(resp)
        return {"descriptions": results if isinstance(results, list) else []}
    except ValueError:
        return {"descriptions": [], "raw": resp[:500]}


@router.post("/split-node")
def split_outline_node(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    _use_key(x_api_key)
    import json
    topic = (body or {}).get("topic", "")
    num_children = (body or {}).get("num_children", 2)

    # 兼容前端扁平字段: 重建 node dict
    node = (body or {}).get("node", {})
    if not node:
        node = {
            "title": (body or {}).get("node_title", ""),
            "description": (body or {}).get("node_description", ""),
            "key_points": (body or {}).get("node_key_points", []),
            "target_words": (body or {}).get("parent_target_words", 2000),
        }
    if not node.get("title"):
        raise HTTPException(status_code=400, detail="node_title 不能为空")

    target_per_child = (body or {}).get("target_words_per_child", node.get("target_words", 2000) // num_children)
    world_setting = (body or {}).get("world_setting", "")
    story_synopsis = (body or {}).get("story_synopsis", "")
    split_requirement = (body or {}).get("split_requirement", "")
    sibling_titles = (body or {}).get("sibling_titles", "")
    sibling_content = (body or {}).get("sibling_content", "")

    sibling_constraint = ""
    if sibling_titles or sibling_content:
        sibling_constraint = f"""

## 同级节点（不得重叠）
以下节点与你将要拆分的内容处于同一层级，拆分后的子节点标题和内容必须与它们有明显区分：
同级标题: {sibling_titles or '无'}
同级内容概要: {sibling_content or '无'}

重要约束：拆分后的子节点标题和情节不能与上述同级节点重复或高度相似。例如，如果同级节点已有"误认仙体"，你不能再拆分出"误认云体"或"仙体误会"等内容。
"""

    llm = get_llm_client()
    prompt = f"""请将以下大纲节点拆分为 {num_children} 个独立子节点，每个有各自的叙事焦点。

主题：{topic}
世界观设定：{world_setting or '无'}
故事梗概：{story_synopsis or '无'}
父节点：{json.dumps(node, ensure_ascii=False)}
拆分要求：{split_requirement or '按故事发展自然拆分'}
{sibling_constraint}

请以 JSON 数组格式输出。title 必须是有故事感的四字或六字章节标题（如"绝境求生""暗流涌动""血战帝都"），description 用一句话简述本节点内容，key_points 列出 2-3 个关键情节要点：
[{{"title": "四到六字标题", "description": "一句话简述", "key_points": ["要点1", "要点2"], "target_words": {target_per_child}}}, ...]"""
    resp = llm.chat_completion(
        [{"role": "system", "content": "你是一位大纲策划编辑。请以 JSON 数组格式输出。"},
         {"role": "user", "content": prompt}],
        temperature=0.6, max_tokens=1500,
    )
    try:
        children = parse_json(resp)
        return {"children": children if isinstance(children, list) else []}
    except ValueError:
        return {"children": [], "raw": resp[:500]}


@router.post("/fill-key-points")
def fill_key_points(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    _use_key(x_api_key)
    """AI 一键填充大纲节点的 key_points 和 description。"""
    node_title = (body or {}).get("node_title", "")
    parent_title = (body or {}).get("parent_title", "")
    topic = (body or {}).get("topic", "")
    genre = (body or {}).get("genre", "")
    world_setting = (body or {}).get("world_setting", "")

    if not node_title:
        raise HTTPException(status_code=400, detail="node_title 不能为空")

    genre_hint = f"题材：{genre}。" if genre else ""
    llm = get_llm_client()
    prompt = (
        f"请为以下大纲节点生成关键事件要点和一句话梗概。\n"
        f"主题：{topic or '未指定'}\n"
        f"{genre_hint}"
        f"世界观：{world_setting or '未指定'}\n"
        f"父节点：{parent_title or '根节点'}\n"
        f"当前节点标题：{node_title}\n\n"
        f"输出JSON: {{\"key_points\": [\"要点1(10字内)\", \"要点2\", \"要点3\"], "
        f"\"description\": \"梗概(30字内)\"}}\n"
        f"要点数量3-5个，只输出JSON。"
    )
    resp = llm.chat_completion(
        [{"role": "system", "content": "你是大纲策划编辑。只输出JSON。"},
         {"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=300, json_mode=True,
    )
    import json
    try:
        result = json.loads(resp)
        return {
            "key_points": result.get("key_points", []),
            "description": result.get("description", ""),
        }
    except json.JSONDecodeError:
        return {"key_points": [], "description": "", "raw": resp[:300]}


@router.post("/import-outline")
def import_outline(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    _use_key(x_api_key)
    import json
    text = (body or {}).get("text", "")
    max_depth = (body or {}).get("max_depth", 3)
    if not text.strip():
        raise HTTPException(status_code=400, detail="text 不能为空")
    llm = get_llm_client()
    prompt = f"""请将以下文本解析为树状大纲 JSON。严格支持以下格式：

1. **Markdown 标题**：# 卷名 / ## 章名 / ### 节名
2. **数字编号**：1. / 1.1 / 1.1.1
3. **第X章：标题模式**（章节摘要导入）：每章以"第X章：标题"或"第X章 标题"开头，后面跟着的段落是该章的 description。所有章节必须全部提取，不得遗漏
4. **Markdown 表格**：| 卷 | 章 | 节 | 描述 |
5. **纯列表**：- 或 * 缩进列表
6. **自然语言**：语义提取卷章结构

层级规则（最多 {max_depth} 层）：第1层→卷/部，第2层→章，第3层+→节/小节

输入文本：
{text}

严格返回以下 JSON（不要 markdown 代码块）：
{{
  "outline": [{{"title": "节点标题", "description": "描述文本", "children": [...], "target_words": 2000}}],
  "report": {{
    "total_input_chars": 数字, "parsed_volumes": 数字, "parsed_chapters": 数字,
    "parsed_leaves": 数字, "dropped_lines": ["无法解析的行"],
    "format_detected": "chapter_summary/markdown/outline/natural_language/table"
  }}
}}

关键规则：
- **每条输入必须在 outline 或 dropped_lines 中，绝对不允许静默丢弃**
- 第X章格式的章节摘要：title 取"第X章：标题"，description 取段落文本
- 如果输入没有明显的卷结构，自动创建"第一卷"包裹所有章节
- title 保留原始文本，不要改写或润色"""
    resp = llm.chat_completion(
        [{"role": "system", "content": "你是一位文本结构化专家。核心原则：输入中的每一章、每一节都必须出现在输出中。如果输入有 N 个章节，输出的 outline 必须包含 N 个节点。无法解析的零散内容放入 dropped_lines。"},
         {"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=8000,
    )
    try:
        result = parse_json(resp)
        if isinstance(result, dict) and "outline" in result:
            outline = result["outline"] if isinstance(result["outline"], list) else []
            report = result.get("report", {})
            return {"outline": outline, "report": report}
        # fallback: old format (bare array)
        if isinstance(result, list):
            return {"outline": result, "report": {}}
        return {"outline": [], "report": {}, "raw": resp[:500]}
    except ValueError:
        return {"outline": [], "report": {}, "raw": resp[:500]}
