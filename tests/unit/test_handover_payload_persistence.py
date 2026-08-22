"""Sidecar 原始 compact payload 持久化：工程 gate。

金标冻结批次（2026-07-27）记录在案的缺口：payload 未持久化导致任何真实运行
都无法冻结到 validator 层。本批次单变量：解析成功的 payload dict 进入
observation→record 持久化链。本文件三类证明：

1. 捕获正确性——成功路径与"解析成功但恢复层抛错"路径都携带 payload，
   截断/解析失败路径为 None；hash 自证（复算 == compact_payload_hash）；
2. 持久化完整性——JSON 落盘 round-trip 后 payload 字节级等价（canonical
   hash 不变），旧记录（无此字段）向后兼容；
3. 重放能力——持久化 payload + 重建 registry 经 restore_and_validate_v22
   重放，与实况 validation 的 contract_hash/计数/note 投影全等。
   这是本批次存在的理由：未来 gold 任务可全量冻结到 validator 层。
"""

import json

from app.writing.handover_contract_v2 import (
    adapt_v2_to_legacy_handover_note,
    build_handover_sources,
    compile_next_boundary,
    sha256_json as sha256_handover_json,
)
from app.writing.handover_contract_v21 import (
    build_compact_source_registry,
    prompt_example_payload_v22,
    prompt_example_premise_v22,
    restore_and_validate_v22,
)
from app.writing.subsection_handover_history import (
    MAX_COMPACT_PAYLOAD_PERSIST_CHARS,
    HandoverExtractionObservation,
    SubsectionHandoverRecord,
    payload_for_persistence,
)
from app.writing.subsection_handover_persistence import (
    SubsectionHandoverHistoryRecorder,
    normalize_history,
)


CURRENT_OUTLINE = {
    "subsection": 1,
    "title": "等待回应",
    "description": "林晚在店门口等待周野的答复",
    "key_points": ["等待回应"],
}
NEXT_OUTLINE = {
    "subsection": 2,
    "title": "清晨的答复",
    "description": "周野给出答复",
    "key_points": ["答复"],
}


def _registry_and_boundary(section_text: str):
    current = dict(CURRENT_OUTLINE)
    current["_section"] = 1
    following = dict(NEXT_OUTLINE)
    following["_section"] = 1
    sources = build_handover_sources(
        section=1,
        subsection=1,
        generated_text=section_text,
        current_outline=current,
        next_outline=following,
        arc_milestones=(),
    )
    registry = build_compact_source_registry(sources, arc_milestones=())
    boundary = compile_next_boundary(
        section=1,
        subsection=1,
        current_outline=current,
        next_outline=following,
    )
    return registry, boundary


# ---------------------------------------------------------------- 捕获层


def test_payload_for_persistence_identity_for_normal_dict():
    payload = prompt_example_payload_v22()
    assert payload_for_persistence(payload) is payload


def test_payload_for_persistence_rejects_non_dict():
    assert payload_for_persistence(None) is None
    assert payload_for_persistence("{}") is None
    assert payload_for_persistence([["not", "a", "dict"]]) is None


def test_payload_for_persistence_size_guard():
    oversized = {"v": "2.2", "s": [], "o": [], "f": ["x" * MAX_COMPACT_PAYLOAD_PERSIST_CHARS], "a": []}
    assert payload_for_persistence(oversized) is None
    normal = {"v": "2.2", "s": [], "o": [], "f": ["x" * 100], "a": []}
    assert payload_for_persistence(normal) is normal


# ---------------------------------------------------------------- writer 提取路径


class _FakeLLM:
    def __init__(self, response: str, finish_reason: str = "stop"):
        self.response = response
        self.finish_reason = finish_reason

    def chat_completion(self, messages, **kwargs):
        sink = kwargs.get("completion_metadata_sink")
        if sink is not None:
            sink({"finish_reason": self.finish_reason, "output_tokens": 64})
        return self.response


def _bare_writer(response: str, finish_reason: str = "stop"):
    from app.agents.writer import Writer

    writer = Writer.__new__(Writer)
    writer.llm = _FakeLLM(response, finish_reason)
    return writer


def _run_v22_extraction(response: str, finish_reason: str = "stop"):
    from app.config import settings

    writer = _bare_writer(response, finish_reason)
    original = settings.WRITER_HANDOVER_CONTRACT_VERSION
    settings.WRITER_HANDOVER_CONTRACT_VERSION = "v2.2"
    try:
        return writer._extract_handover_v21_with_observation(
            section_text=prompt_example_premise_v22(),
            section_num=1,
            sub_num=1,
            event_graph=None,
            current_subsection=dict(CURRENT_OUTLINE),
            next_subsection=dict(NEXT_OUTLINE),
            task_id="payload-persist-test",
        )
    finally:
        settings.WRITER_HANDOVER_CONTRACT_VERSION = original


def test_writer_success_path_persists_payload_with_hash_self_check():
    payload = prompt_example_payload_v22()
    note, observation = _run_v22_extraction(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    assert note is not None
    assert observation.execution_status == "completed_with_changes"
    assert observation.compact_payload == payload
    assert (
        sha256_handover_json(observation.compact_payload)
        == observation.compact_payload_hash
    )


def test_writer_parse_ok_restore_error_still_persists_payload():
    """解析成功但顶层版本非法：error 路径也携带 payload——这正是取证需要。"""
    bad_top_level = {"v": "9.9", "s": [], "o": [], "f": [], "a": []}
    note, observation = _run_v22_extraction(
        json.dumps(bad_top_level, ensure_ascii=False)
    )
    assert note is None
    assert observation.execution_status == "error"
    assert observation.compact_payload == bad_top_level
    assert (
        sha256_handover_json(observation.compact_payload)
        == observation.compact_payload_hash
    )


def test_writer_truncation_path_has_no_payload():
    payload = prompt_example_payload_v22()
    note, observation = _run_v22_extraction(
        json.dumps(payload, ensure_ascii=False), finish_reason="length"
    )
    assert note is None
    assert observation.execution_status == "error"
    assert observation.compact_payload is None
    assert observation.compact_payload_hash is None


def test_writer_unparseable_response_has_no_payload():
    note, observation = _run_v22_extraction("这不是 JSON")
    assert note is None
    assert observation.execution_status == "error"
    assert observation.compact_payload is None


# ---------------------------------------------------------------- 持久化层


class _FakeBlackboard:
    def __init__(self):
        self.store = {}

    def get(self, task_id, key):
        return self.store.get((task_id, key))

    def set(self, task_id, key, value):
        self.store[(task_id, key)] = value

    def load_checkpoint(self, task_id):
        return {}


def _observation_with_payload(payload):
    return HandoverExtractionObservation(
        executed=True,
        execution_status="completed_with_changes",
        note_hash="a" * 64,
        contract_version="v2.2",
        payload_version="2.2",
        compact_payload_hash=sha256_handover_json(payload),
        compact_payload=payload,
        accepted_claim_count=2,
        rejected_claim_count=0,
        restored_claim_count=2,
        locally_rejected_claim_count=0,
    )


def test_capture_committed_passes_payload_into_record():
    payload = prompt_example_payload_v22()
    blackboard = _FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(blackboard, "payload-persist-test")
    record_id = recorder.capture_committed(
        section=1,
        subsection=1,
        output_sha256="b" * 64,
        prompt_messages_hash="c" * 64,
        commit_idempotency_key="key-1",
        handover_note=None,
        observation=_observation_with_payload(payload),
    )
    assert record_id is not None
    stored = blackboard.store[
        ("payload-persist-test", "subsection_handover_history_v1")
    ]
    envelope = normalize_history(stored)
    record = envelope.records[record_id]
    assert record.compact_payload == payload
    assert sha256_handover_json(record.compact_payload) == record.compact_payload_hash


def test_record_json_round_trip_preserves_payload_hash():
    """落盘等价性：record → JSON 文本 → 回读后 payload 的 canonical hash 不变。"""
    payload = prompt_example_payload_v22()
    blackboard = _FakeBlackboard()
    recorder = SubsectionHandoverHistoryRecorder(blackboard, "payload-persist-test")
    recorder.capture_committed(
        section=1,
        subsection=1,
        output_sha256="b" * 64,
        prompt_messages_hash="c" * 64,
        commit_idempotency_key="key-1",
        handover_note=None,
        observation=_observation_with_payload(payload),
    )
    stored = blackboard.store[
        ("payload-persist-test", "subsection_handover_history_v1")
    ]
    round_tripped = normalize_history(json.loads(json.dumps(stored, ensure_ascii=False)))
    record = next(iter(round_tripped.records.values()))
    assert record.compact_payload == payload
    assert sha256_handover_json(record.compact_payload) == record.compact_payload_hash


def test_legacy_records_without_payload_field_still_validate():
    """向后兼容：金标任务 3650fd64 的既有记录没有该字段，读取语义为 None。"""
    legacy = {
        "record_id": "subsection-handover:x:S1.1:hash",
        "task_id_hash": "d" * 64,
        "section": 1,
        "subsection": 1,
        "output_sha256": "b" * 64,
        "prompt_messages_hash": "c" * 64,
        "commit_idempotency_key": "key-1",
        "handover_source_id": "writer-handover:x:S1.1:hash",
        "execution_status": "completed_no_change",
        "field_count": 0,
        "created_at": "2026-07-27T00:00:00+00:00",
    }
    record = SubsectionHandoverRecord.model_validate(legacy)
    assert record.compact_payload is None


# ---------------------------------------------------------------- 重放能力


def test_replay_from_persisted_payload_reproduces_validation():
    """本批次存在的理由：持久化 payload + 重建 registry = validator 层完整重放。"""
    payload = prompt_example_payload_v22()
    registry, boundary = _registry_and_boundary(prompt_example_premise_v22())

    live = restore_and_validate_v22(
        payload, registry=registry, next_boundary=boundary
    )
    persisted = json.loads(json.dumps(payload, ensure_ascii=False))
    replayed = restore_and_validate_v22(
        persisted, registry=registry, next_boundary=boundary
    )

    assert replayed.contract.contract_hash == live.contract.contract_hash
    assert replayed.accepted_claim_count == live.accepted_claim_count == 2
    assert replayed.rejected_claim_count == live.rejected_claim_count
    assert replayed.rejection_counts == live.rejection_counts
    assert adapt_v2_to_legacy_handover_note(replayed) == adapt_v2_to_legacy_handover_note(
        live
    )
