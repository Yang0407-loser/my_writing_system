from app.structured_context_compactor import StructuredContextCompactor, split_paragraph_spans


def _source(text, source_id="s1"):
    return {"source_id": source_id, "text": text, "section": 2, "subsection": 1, "title": "邀请与回应", "final_score": 0.8}


def test_paragraph_spans_preserve_exact_offsets():
    text = "  第一段。\n\n“来吗？”\n\n“好。”  "
    spans = split_paragraph_spans(text)
    assert [text[item.start:item.end] for item in spans] == [item.text for item in spans]
    assert [item.text for item in spans] == ["第一段。", "“来吗？”", "“好。”"]


def test_paragraph_window_keeps_whole_paragraph_and_source_traceability():
    text = "无关开场。\n\n周野邀请林晚周六来帮忙。\n\n林晚回答好。\n\n无关结尾。"
    result = StructuredContextCompactor(strategy="paragraph_window", max_anchors=1).compact(
        query="周野邀请林晚 回答好", sources=[_source(text)]
    )
    assert result["source_retention"] == 1.0
    assert "周野邀请林晚周六来帮忙。" in result["fragments"][0]["text"]
    for fragment in result["fragments"]:
        assert text[fragment["start"]:fragment["end"]] == fragment["text"]


def test_dialogue_block_keeps_question_and_response_together():
    text = "开场。\n\n“周六来帮忙。”\n\n“为什么？”\n\n“因为缺人。”\n\n“好。”\n\n尾声。"
    result = StructuredContextCompactor(strategy="dialogue_narrative_block", max_anchors=1).compact(
        query="周六邀请 回答", sources=[_source(text)]
    )
    kept = "\n".join(item["text"] for item in result["fragments"])
    assert "周六来帮忙" in kept
    assert "为什么" in kept
    assert "因为缺人" in kept


def test_character_window_expands_to_paragraph_boundaries():
    text = "甲" * 180 + "\n\n邀请与回应在这里。\n\n" + "乙" * 180
    result = StructuredContextCompactor(
        strategy="character_span_window", window_radius=20, max_anchors=1
    ).compact(query="邀请 回应", sources=[_source(text)])
    fragment = result["fragments"][0]
    boundaries = {item.start for item in split_paragraph_spans(text)} | {
        item.end for item in split_paragraph_spans(text)
    }
    assert "邀请与回应在这里。" in fragment["text"]
    assert fragment["start"] in boundaries
    assert fragment["end"] in boundaries
    assert text[fragment["start"]:fragment["end"]] == fragment["text"]


def test_short_chunk_falls_back_to_full_text_without_hard_truncation():
    text = "短文本中的唯一证据。"
    result = StructuredContextCompactor(strategy="paragraph_window", short_chunk_chars=30).compact(
        query="唯一证据", sources=[_source(text)]
    )
    assert result["fragments"][0]["text"] == text
    assert result["fallbacks"] == [{"source_id": "s1", "reason": "short_chunk"}]
    assert result["profile"]["hard_truncation"] is False
