from app.context_ab_evaluation import deterministic_output_checks


def test_deterministic_output_checks_do_not_retain_prose():
    text = "她想起旧事。\n\n“回去吧。”\n\n她想起旧事。"
    result = deterministic_output_checks(text)

    assert result["characters"] == len(text)
    assert result["paragraph_count"] == 3
    assert result["dialogue_paragraph_count"] == 1
    assert result["duplicate_paragraph_count"] == 1
    assert result["psychological_narration_occurrences"] == {"她想起": 2}
    assert text not in str(result)


def test_deterministic_output_checks_flag_ai_cliches():
    result = deterministic_output_checks("随着时间的推移，这不仅是一种味道，更是一种记忆。")

    assert result["ai_cliche_occurrences"] == {
        "不仅是一种": 1,
        "更是一种": 1,
        "随着时间的推移": 1,
    }
