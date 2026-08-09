from app.writing.state_frame_service import build_state_frame_artifacts
from app.writing.world_runtime_bakery_gold import build_saturday_bakery_gold_fixture
from app.writing.world_runtime_checkpoint_shadow import (
    CHECKPOINT_SHADOW_KEY,
    build_shadow_payload,
    merge_shadow,
)
from app.writing.world_runtime_contracts import canonical_hash
from app.writing.world_runtime_legacy_projection import project_state_frame
from app.writing.world_runtime_state_committer import WorldRuntimeStateCommitter
from app.writing.world_runtime_state_frame_reader import (
    READER_VERSION,
    read_state_frame_after,
)
from experiments.world_runtime_writer_canary import wr3_shadow_audit as audit


def _gold_committed():
    gold = build_saturday_bakery_gold_fixture()
    delta, validation = audit._gold_committable(gold)
    return WorldRuntimeStateCommitter().commit(
        idempotency_key="wr3.10:reader:gold",
        before=gold.state_before,
        delta=delta,
        validation=validation,
        final_text_hash=gold.output_hash,
    )


def _base_args(**overrides):
    args = {
        "task_id": "saturday-bakery-canary",
        "section": 1,
        "subsection": 1,
    }
    args.update(overrides)
    return args


def test_reader_returns_verified_wr_frame_from_shadow():
    committed = _gold_committed()
    checkpoint = merge_shadow(
        {"legacy_key": "kept"}, build_shadow_payload(committed)
    )
    result = read_state_frame_after(
        checkpoint=checkpoint, committed=committed, **_base_args()
    )
    assert result["schema_version"] == READER_VERSION
    assert result["source"] == "world_runtime_shadow"
    assert result["verified"] is True
    assert "fallback_reason" not in result
    expected = project_state_frame(committed, **_base_args())
    assert result["frame"] == expected.model_dump(mode="json")
    # Read is non-mutating: the shadow payload is untouched.
    assert checkpoint[CHECKPOINT_SHADOW_KEY] == build_shadow_payload(committed)


def test_reader_falls_back_when_committed_missing():
    committed = _gold_committed()
    checkpoint = merge_shadow(
        {"legacy_key": "kept"}, build_shadow_payload(committed)
    )
    result = read_state_frame_after(
        checkpoint=checkpoint, committed=None, **_base_args()
    )
    assert result["source"] == "legacy"
    assert result["verified"] is False
    assert result["fallback_reason"] == "committed_missing"
    expected = build_state_frame_artifacts(**_base_args(checkpoint=checkpoint))
    assert result["frame"] == expected["after"]


def test_reader_falls_back_when_shadow_missing():
    checkpoint = {"legacy_key": "kept"}
    result = read_state_frame_after(
        checkpoint=checkpoint, committed=_gold_committed(), **_base_args()
    )
    assert result["source"] == "legacy"
    assert result["verified"] is False
    assert result["fallback_reason"] == "shadow_missing"
    expected = build_state_frame_artifacts(**_base_args(checkpoint=checkpoint))
    assert result["frame"] == expected["after"]


def test_reader_falls_back_on_tampered_shadow_payload():
    committed = _gold_committed()
    payload = build_shadow_payload(committed)
    payload["after_facts"][0]["value"] = "tampered"
    checkpoint = merge_shadow({"legacy_key": "kept"}, payload)
    result = read_state_frame_after(
        checkpoint=checkpoint, committed=committed, **_base_args()
    )
    assert result["source"] == "legacy"
    assert result["verified"] is False
    assert "shadow_invalid" in result["fallback_reason"]
    assert "payload_hash_mismatch" in result["fallback_reason"]
    expected = build_state_frame_artifacts(**_base_args(checkpoint=checkpoint))
    assert result["frame"] == expected["after"]


def test_reader_falls_back_on_legacy_frame_hash_mismatch():
    committed = _gold_committed()
    payload = build_shadow_payload(committed)
    payload["legacy_frame_hash"] = "0000000000000000000000000000000000000000"
    # Recompute the payload hash so the payload is internally consistent:
    # this isolates the reader's frame-hash guard from payload tampering.
    payload["payload_hash"] = canonical_hash({
        key: value for key, value in payload.items() if key != "payload_hash"
    })
    checkpoint = merge_shadow({"legacy_key": "kept"}, payload)
    result = read_state_frame_after(
        checkpoint=checkpoint, committed=committed, **_base_args()
    )
    assert result["source"] == "legacy"
    assert result["verified"] is False
    assert "shadow_invalid" in result["fallback_reason"]
    assert "legacy_frame_hash_mismatch" in result["fallback_reason"]
    expected = build_state_frame_artifacts(**_base_args(checkpoint=checkpoint))
    assert result["frame"] == expected["after"]
