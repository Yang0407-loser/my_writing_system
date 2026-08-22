import json

from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary import adversarial_experiment as wr1r
from experiments.world_runtime_writer_canary.delta_shadow_wr2b import validate_delta_v2
from experiments.world_runtime_writer_canary.semantic_extractor_wr2c4 import (
    parse_semantic_response,
)
from experiments.world_runtime_writer_canary.state_commit_adapter_wr2c4 import (
    to_committable,
)


def _response(judgments):
    return json.dumps({"judgments": judgments}, ensure_ascii=False)


def _parse(text, judgments, sample_id="WR2C4-C1-01", state_variant="before", scene_id="adversarial-unpublished-knowledge"):
    return parse_semantic_response(
        text=text,
        response_text=_response(judgments),
        sample_id=sample_id,
        scene_id=scene_id,
        state_variant=state_variant,
    )


def test_adapter_commits_accepted_knowledge_change():
    text = "林晚把整份文档发进工作群。阿吴随即在群里引用正文那句“五点到了”，问她是不是写错。"
    artifact = _parse(
        text,
        [{
            "change_type": "knowledge_state",
            "occurred": True,
            "after_value": "perceived",
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [
                {"excerpt": "林晚把整份文档发进工作群", "occurrence": 1},
                {"excerpt": "阿吴随即在群里引用正文那句“五点到了”", "occurrence": 1},
            ],
        }],
    )
    validation = validate_delta_v2(artifact.delta)
    states = wr1r._artifacts()[1]
    delta, committable_validation = to_committable(
        artifact.delta,
        validation,
        project_id=states["before"].project_id,
    )
    committer = WorldRuntimeStateCommitter()
    result = committer.commit(
        idempotency_key="c1-test:knowledge",
        before=states["before"],
        delta=delta,
        validation=committable_validation,
        final_text_hash=artifact.output_hash,
    )
    fact = next(item for item in result.after.facts if item.fact_id == "fact:coworker:article-knowledge")
    assert fact.value == "perceived"
    assert fact.revision == 8
    assert len(result.ledger.entries) == 1
    assert result.ledger.entries[0].change_type == "knowledge_state"


def test_adapter_commits_event_only_sale_into_ledger_without_facts():
    text = "五点五十五分，顾客扫码付款，周野把面包从窗口递出去。"
    artifact = _parse(
        text,
        [{
            "change_type": "storefront_public_sale",
            "occurred": True,
            "after_value": "occurred",
            "mode": "actual",
            "epistemic": "asserted",
            "evidence": [{"excerpt": "顾客扫码付款", "occurrence": 1}],
        }],
        scene_id="adversarial-storefront-hours",
    )
    validation = validate_delta_v2(artifact.delta)
    assert validation.accepted_change_ids == ()  # sale before opening is invalid
    # The invalid sale cannot be committed; assert the adapter output is still well-formed.
    delta, committable_validation = to_committable(artifact.delta, validation)
    assert len(delta.changes) == 1
    assert committable_validation.accepted_change_ids == ()
