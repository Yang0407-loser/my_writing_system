"""展示窗保尾（2026-07-27）：generated_subsection 渲染窗口从头 3000 改为尾 3000。

背景（Demo #5 / 任务 be663720 取证）：S1.4 正文 3,492 字，保头窗口把节尾
492 字切出模型视野，而 handover 的提取目标恰是节尾状态——S1.4 全部 4 条
quote_not_found 均为模型对未见结尾的外推。保尾使展示方向与提取目标一致。

不变式：展示文本必须是原文的连续子串——模型从展示中逐字复制的任何短引，
find() 都能在全文中定位。头尾拼接式窗口会破坏该不变式，故为纯尾切片。
"""

from app.writing.handover_contract_v2 import (
    build_handover_sources,
    compile_next_boundary,
)
from app.writing.handover_contract_v21 import (
    build_compact_source_registry,
    render_v21_prompt_context,
    restore_and_validate_v22,
)


CURRENT = {
    "subsection": 1,
    "title": "长小节",
    "description": "超长正文的展示窗行为",
    "key_points": ["窗口"],
}

HEAD_MARKER = "开头哨兵句在这里。"
TAIL_MARKER = "程砚把白瓷杯放回窗台。"


def _registry_for(text):
    current = dict(CURRENT)
    current["_section"] = 1
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline=current,
        next_outline=None,
        arc_milestones=(),
    )
    return build_compact_source_registry(sources, arc_milestones=()), current


def _displayed_subsection_text(registry):
    rendered = render_v21_prompt_context(registry)["source_registry"]
    # 渲染为 canonical JSON 行数组；generated_subsection 是 index 0 的行。
    import json

    rows = json.loads(rendered)
    for row in rows:
        if row[1] == "generated_subsection":
            return row[4]
    raise AssertionError("generated_subsection row missing")


def _long_text(total_chars):
    filler_unit = "她数着烘焙机的滴答声。"
    body = HEAD_MARKER
    while len(body) + len(TAIL_MARKER) < total_chars:
        body += filler_unit
    return body[: total_chars - len(TAIL_MARKER)] + TAIL_MARKER


def test_short_text_displayed_in_full():
    text = _long_text(2000)
    registry, _ = _registry_for(text)
    assert _displayed_subsection_text(registry) == text


def test_exactly_3000_displayed_in_full():
    text = _long_text(3000)
    assert len(text) == 3000
    registry, _ = _registry_for(text)
    assert _displayed_subsection_text(registry) == text


def test_long_text_displays_tail_not_head():
    text = _long_text(4200)
    registry, _ = _registry_for(text)
    displayed = _displayed_subsection_text(registry)
    assert len(displayed) == 3000
    assert displayed == text[-3000:]
    assert TAIL_MARKER in displayed
    assert HEAD_MARKER not in displayed


def test_boundary_3001_drops_exactly_first_char():
    text = _long_text(3001)
    registry, _ = _registry_for(text)
    displayed = _displayed_subsection_text(registry)
    assert displayed == text[1:]
    assert len(displayed) == 3000


def test_displayed_text_is_contiguous_substring_of_source():
    """不变式：展示内容永远可被 find() 在全文定位。"""
    for total in (500, 2999, 3000, 3001, 3492, 6000):
        text = _long_text(total)
        registry, _ = _registry_for(text)
        displayed = _displayed_subsection_text(registry)
        assert text.find(displayed) >= 0


def test_tail_quote_round_trips_to_acceptance():
    """端到端：模型从保尾窗口逐字复制的节尾短引，经真实恢复链全收。
    这正是 Demo #5 S1.4 失败场景的镜像——结尾可见后引文可定位。"""
    text = _long_text(3492)  # Demo #5 S1.4 的真实长度
    current = dict(CURRENT)
    current["_section"] = 1
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=text,
        current_outline=current,
        next_outline=None,
        arc_milestones=(),
    )
    registry = build_compact_source_registry(sources, arc_milestones=())
    boundary = compile_next_boundary(
        section=1, subsection=1, current_outline=current, next_outline=None
    )

    displayed = _displayed_subsection_text(registry)
    quote = "程砚把白瓷杯放回窗台"
    assert quote in displayed  # 保尾后模型能看到它

    result = restore_and_validate_v22(
        {
            "v": "2.2",
            "s": [[0, quote, "os", "c", "c", "程砚|放回|白瓷杯"]],
            "o": [], "f": [], "a": [],
        },
        registry=registry,
        next_boundary=boundary,
    )
    assert result.accepted_claim_count == 1
    assert not result.rejection_counts
