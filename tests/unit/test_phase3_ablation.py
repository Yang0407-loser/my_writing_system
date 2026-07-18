from tests.quality.phase3_ablation import (
    configuration_grid,
    evaluate_configuration,
    pareto_frontier,
    replay_query,
)


def _candidate(source_id, *, section=1, intents=("event",), vector=1.0, tokens=20):
    return {
        "id": source_id,
        "section": section,
        "subsection": 1,
        "title": source_id,
        "reason": "candidate",
        "matched_intents": list(intents),
        "score_components": {
            "vector": vector,
            "keyword": 0.0,
            "title": 0.0,
            "character": 0.0,
            "chapter_proximity": 0.0,
        },
        "graded_character_score": 0.0,
        "token_estimate": tokens,
    }


def _config(**overrides):
    config = {
        "config_id": "test",
        "intent_variant": "all",
        "character_weight_factor": 1.0,
        "character_mode": "binary",
        "min_score": 0.35,
        "max_queries": 4,
        "max_results": 5,
        "duplicate_penalty": 0.08,
        "token_budget": None,
    }
    config.update(overrides)
    return config


def _query(candidates):
    return {
        "query_index": 1,
        "current_section": 13,
        "elapsed_ms": 100.0,
        "plan": {"queries": [{"intent": "event"}, {"intent": "scene"}]},
        "candidate_trace": candidates,
    }


def test_configuration_grid_covers_declared_matrix():
    configs = configuration_grid()

    assert len(configs) == 10_368
    assert {item["intent_variant"] for item in configs} == {
        "all", "no_scene", "no_character",
    }
    assert {item["token_budget"] for item in configs} == {None, 400, 600, 800}


def test_replay_applies_intent_future_duplicate_and_token_constraints():
    candidates = [
        _candidate("first", section=1, tokens=30),
        _candidate("same-section", section=1, vector=0.9, tokens=30),
        _candidate("scene-only", section=2, intents=("scene",), tokens=30),
        {**_candidate("future", section=3), "reason": "future_section"},
    ]

    selected = replay_query(
        _query(candidates),
        _config(intent_variant="no_scene", token_budget=50),
    )

    assert [item["id"] for item in selected] == ["first"]


def test_evaluation_excludes_unlabeled_from_precision_and_reports_it_separately():
    queries = [_query([_candidate("relevant"), _candidate("unknown", section=2)])]
    labels = {(1, "relevant"): "\u76f8\u5173"}

    result = evaluate_configuration(queries, labels, _config())

    assert result["selected_candidates"] == 2
    assert result["known_relevant_selected"] == 1
    assert result["unlabeled_selected"] == 1
    assert result["closed_set_precision"] == 1.0
    assert result["known_relevant_retention"] == 1.0
    assert result["label_coverage"] == 0.5


def test_pareto_frontier_removes_strictly_dominated_outcome():
    better = {
        "closed_set_precision": 0.7,
        "known_relevant_retention": 0.9,
        "unlabeled_selected": 0,
        "mean_token_estimate": 300,
        "mean_latency_estimate_ms": 100,
    }
    worse = {
        "closed_set_precision": 0.6,
        "known_relevant_retention": 0.8,
        "unlabeled_selected": 1,
        "mean_token_estimate": 400,
        "mean_latency_estimate_ms": 200,
    }

    assert pareto_frontier([worse, better]) == [better]
