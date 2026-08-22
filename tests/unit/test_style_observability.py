from app.style_observability import analyse_text, sentence_signature, sentence_start_type


def test_start_categories_and_observable_densities():
    text = "周六早晨，林晚听见风声。\n\n林晚觉得空气很冷。\n\n第一次，她说：“我知道。”"
    metrics = analyse_text(text, ["林晚", "周野"])

    assert metrics["sentence_starts"]["time_anchor"] == 1
    assert metrics["sentence_starts"]["character_name"] == 1
    assert metrics["sentence_starts"]["ordinal"] == 1
    assert metrics["dialogue_ratio"] > 0
    assert metrics["sensory_terms_per_1k"] > 0
    assert metrics["psychological_exposition_hits"] == 2


def test_consecutive_short_and_structural_runs_are_traceable():
    text = "林晚走。林晚停。林晚看。"
    metrics = analyse_text(text, ["林晚"])

    assert metrics["consecutive_short_sentence_runs"] == [{"start_sentence": 1, "length": 3}]
    assert metrics["consecutive_isomorphic_sentence_runs"][0]["start_sentence"] == 1
    assert metrics["consecutive_isomorphic_sentence_runs"][0]["length"] == 3


def test_signature_is_structural_proxy():
    assert sentence_signature("林晚走。", ["林晚"]) == sentence_signature("周野停。", ["林晚", "周野"])
    assert sentence_start_type("凌晨三点，她醒了。", []) == "time_anchor"


def test_closing_quote_is_not_counted_as_a_sentence():
    metrics = analyse_text("她问：“走吗？”\n\n他说：“走。”", [])
    assert metrics["sentence_length"]["count"] == 2
