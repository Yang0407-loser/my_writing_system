from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_checkpoint_shadow import (
    CHECKPOINT_SHADOW_KEY,
    build_shadow_payload,
    merge_shadow,
    read_shadow,
    verify_shadow_payload,
    write_shadow_to_blackboard,
)
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from experiments.world_runtime_writer_canary import wr3_shadow_audit as audit


def _gold_committed():
    gold = build_saturday_bakery_gold_fixture()
    delta, validation = audit._gold_committable(gold)
    return WorldRuntimeStateCommitter().commit(
        idempotency_key="wr3:gold",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )


def test_shadow_payload_is_self_consistent_and_deterministic():
    committed = _gold_committed()
    payload = build_shadow_payload(committed)
    verified, issues = verify_shadow_payload(payload)
    assert verified is True
    assert issues == []
    assert build_shadow_payload(committed) == payload


def test_merge_shadow_preserves_legacy_keys():
    checkpoint = {"handover_json": [], "world_state_json": {}}
    payload = build_shadow_payload(_gold_committed())
    merged = merge_shadow(checkpoint, payload)
    assert merged["handover_json"] == []
    assert merged["world_state_json"] == {}
    assert merged[CHECKPOINT_SHADOW_KEY] == payload
    assert checkpoint == {"handover_json": [], "world_state_json": {}}


def test_shadow_verification_detects_tampering():
    payload = build_shadow_payload(_gold_committed())
    payload["after_facts"][0]["value"] = "tampered"
    verified, issues = verify_shadow_payload(payload)
    assert verified is False
    assert issues


class _FakeBlackboard:
    def __init__(self):
        self.checkpoint = {"legacy_key": "kept"}

    def load_checkpoint(self, task_id):
        return self.checkpoint

    def save_checkpoint(self, task_id, state_dict):
        self.checkpoint = state_dict


def test_write_shadow_to_blackboard_adds_field_only():
    blackboard = _FakeBlackboard()
    write_shadow_to_blackboard(blackboard, "task-1", _gold_committed())
    assert blackboard.checkpoint["legacy_key"] == "kept"
    assert CHECKPOINT_SHADOW_KEY in blackboard.checkpoint
    payload = read_shadow(blackboard.checkpoint)
    assert payload["revision"] == 8
