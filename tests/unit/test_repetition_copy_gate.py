"""Deterministic anti-copy layer for the subsection repetition gate.

Incident (2026-07-26, task 1f6581ee): S1.4's generation was a byte-identical
copy of S1.3. TF-IDF correctly flagged similarity 1.0, but the LLM beat judge
compares prev_text[-300:] against curr_text[:600] — for a full copy those
windows never overlap, the copy's opening reads as new content, and the judge
returned advanced=true. These tests pin the fix: exact and near copies must be
rejected BEFORE the LLM judge is consulted, and its verdict must not be able
to override them.
"""

import pytest

from app import repetition_checker as rc
from app.repetition_checker import (
    EXACT_COPY_SIMILARITY,
    check_exact_copy,
    check_subsection_quality,
)


# Long enough to exceed any similarity floor; distinct storylines.
TEXT_A = (
    "林晚在凌晨两点收到第二十版文案的驳回邮件。她盯着屏幕看了很久，"
    "打开新文档写下辞职信。走出写字楼时，风把她的头发吹乱了。"
    "街角面包店的灯还亮着，麦子发酵的香气涌过来，她停住了脚步。"
) * 4
TEXT_B = (
    "周野凌晨三点半开始揉面。掌根推出去，面团在案板上展开又收拢，"
    "面粉从指缝间漏下去。百叶窗外似乎有人停留，他没有抬头，"
    "继续用重复的动作维持内心的秩序。烤箱的预热灯亮了。"
) * 4
TEXT_C = (
    "吴阿姨提着菜篮子下楼，看见台阶上坐着写字的年轻姑娘。"
    "她把刚买的肉包子递过去，说早上凉，吃点热的。"
    "广场舞的音乐从社区活动室飘出来，槐树影子晃在红砖墙上。"
) * 4


def _judge_must_not_run(prev, curr):
    raise AssertionError("LLM beat judge must not be consulted on this path")


# ---------------------------------------------------------------------------
# the incident itself
# ---------------------------------------------------------------------------


def test_byte_identical_copy_rejected_without_llm(monkeypatch):
    """The exact S1.4 shape: current text == an earlier subsection, verbatim."""
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)

    result = check_subsection_quality(TEXT_C, [TEXT_A, TEXT_B, TEXT_C], TEXT_C)

    assert result["pass"] is False
    assert result["deterministic_reject"] == "exact_text"
    assert result["beat_check"] is None
    assert result["repetition"]["max_similarity"] == 1.0
    assert result["repetition"]["similar_section"] == 2


def test_copy_of_non_adjacent_subsection_rejected(monkeypatch):
    """A copy of an EARLIER (non-previous) subsection: the beat judge's
    prev-tail window would be a different text entirely — the blind spot is
    even wider. The deterministic layer must not care which slot matched."""
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)

    result = check_subsection_quality(TEXT_A, [TEXT_A, TEXT_B, TEXT_C], TEXT_C)

    assert result["pass"] is False
    assert result["deterministic_reject"] == "exact_text"
    assert result["repetition"]["similar_section"] == 0


def test_trailing_whitespace_does_not_defeat_exact_match(monkeypatch):
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)
    result = check_subsection_quality(TEXT_B + "\n\n  ", [TEXT_B], TEXT_B)
    assert result["pass"] is False
    assert result["deterministic_reject"] == "exact_text"


def test_long_copy_beyond_tfidf_truncation_still_caught(monkeypatch):
    """check_repetition truncates to 2000 chars; the exact layer must compare
    full text so a >2000-char copy cannot slip past on the tail."""
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)
    long_text = (TEXT_A + TEXT_B + TEXT_C) * 3  # far beyond 2000 chars
    assert len(long_text) > 2000

    result = check_subsection_quality(long_text, [long_text], long_text)

    assert result["pass"] is False
    assert result["deterministic_reject"] == "exact_text"


def test_exact_layer_immune_to_tokenizer_failure(monkeypatch):
    """check_repetition fails open (repeated=False) when TF-IDF explodes.
    A byte copy must still be rejected because the exact layer runs first
    and never touches the tokenizer."""
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)

    def boom(texts):
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(rc, "_build_tfidf_matrix", boom)

    result = check_subsection_quality(TEXT_A, [TEXT_A], TEXT_A)

    assert result["pass"] is False
    assert result["deterministic_reject"] == "exact_text"


# ---------------------------------------------------------------------------
# near-copy band (>= EXACT_COPY_SIMILARITY, not byte-identical)
# ---------------------------------------------------------------------------


def test_near_copy_similarity_rejected_without_llm(monkeypatch):
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)
    monkeypatch.setattr(
        rc,
        "check_repetition",
        lambda text, prev: {
            "repeated": True,
            "max_similarity": 0.981,
            "similar_section": 1,
        },
    )

    result = check_subsection_quality(TEXT_A, [TEXT_B, TEXT_C], TEXT_C)

    assert result["pass"] is False
    assert result["deterministic_reject"] == "near_copy_similarity"
    assert result["beat_check"] is None


def test_threshold_boundary_is_inclusive(monkeypatch):
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)
    monkeypatch.setattr(
        rc,
        "check_repetition",
        lambda text, prev: {
            "repeated": True,
            "max_similarity": EXACT_COPY_SIMILARITY,
            "similar_section": 0,
        },
    )
    result = check_subsection_quality(TEXT_A, [TEXT_B], TEXT_B)
    assert result["pass"] is False
    assert result["deterministic_reject"] == "near_copy_similarity"


# ---------------------------------------------------------------------------
# the grey zone still belongs to the LLM judge
# ---------------------------------------------------------------------------


def _grey_zone(monkeypatch, advanced):
    monkeypatch.setattr(
        rc,
        "check_repetition",
        lambda text, prev: {
            "repeated": True,
            "max_similarity": 0.90,
            "similar_section": 0,
        },
    )
    calls = []

    def fake_judge(prev, curr):
        calls.append((prev, curr))
        return {"advanced": advanced, "what": "测试"}

    monkeypatch.setattr(rc, "llm_check_beat_advancement", fake_judge)
    result = check_subsection_quality(TEXT_A, [TEXT_B], TEXT_B)
    return result, calls


def test_grey_zone_judge_pass_is_respected(monkeypatch):
    result, calls = _grey_zone(monkeypatch, advanced=True)
    assert result["pass"] is True
    assert len(calls) == 1
    assert result["deterministic_reject"] is None


def test_grey_zone_judge_fail_triggers_retry(monkeypatch):
    result, calls = _grey_zone(monkeypatch, advanced=False)
    assert result["pass"] is False
    assert len(calls) == 1
    assert result["beat_check"]["advanced"] is False


# ---------------------------------------------------------------------------
# ordinary paths unchanged
# ---------------------------------------------------------------------------


def test_distinct_texts_pass_without_llm(monkeypatch):
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)
    result = check_subsection_quality(TEXT_C, [TEXT_A, TEXT_B], TEXT_B)
    assert result["pass"] is True
    assert result["deterministic_reject"] is None
    assert result["beat_check"] is None


def test_empty_previous_texts_pass(monkeypatch):
    monkeypatch.setattr(rc, "llm_check_beat_advancement", _judge_must_not_run)
    result = check_subsection_quality(TEXT_A, [], "")
    assert result["pass"] is True


def test_consumer_contract_fields_always_present(monkeypatch):
    """generation_controller formats repetition.max_similarity with :.2f and
    reads similar_section — every return shape must carry them."""
    monkeypatch.setattr(
        rc, "llm_check_beat_advancement",
        lambda prev, curr: {"advanced": True, "what": ""},
    )
    shapes = [
        check_subsection_quality(TEXT_A, [TEXT_A], TEXT_A),          # exact
        check_subsection_quality(TEXT_C, [TEXT_A], TEXT_A),          # distinct
        check_subsection_quality(TEXT_A, [], ""),                     # empty
    ]
    for result in shapes:
        assert "deterministic_reject" in result
        rep = result["repetition"]
        assert f"{rep['max_similarity']:.2f}"  # formattable
        assert "similar_section" in rep


def test_check_exact_copy_handles_none_and_empty():
    assert check_exact_copy("", [TEXT_A])["copied"] is False
    assert check_exact_copy("   ", [TEXT_A])["copied"] is False
    assert check_exact_copy(TEXT_A, [])["copied"] is False
    assert check_exact_copy(TEXT_A, ["", None if False else ""])["copied"] is False
