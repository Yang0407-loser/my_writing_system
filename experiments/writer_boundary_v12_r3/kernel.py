from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .models import ExecutionAudit, PreferenceVote, ProviderReceipt


STATES = [
    "DESIGN_LOCKED",
    "ASSIGNMENTS_LOCKED",
    "REQUESTS_LOCKED",
    "TEXTS_LOCKED",
    "AUDITS_LOCKED",
    "BLIND_JOIN_LOCKED",
    "VOTES_LOCKED",
    "IDENTITY_UNBLINDED",
    "AGGREGATED",
]
COMMON_BOUNDARY_SENTENCE = "一旦方案确定，不得改变该内容边界。"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    return digest_bytes(canonical_json(value).encode("utf-8"))


class TransactionLedger:
    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path)) as db:
            with db:
                db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects(
                    sha256 TEXT NOT NULL, kind TEXT NOT NULL, payload BLOB NOT NULL,
                    PRIMARY KEY(sha256, kind)
                );
                CREATE TABLE IF NOT EXISTS receipts(
                    sequence INTEGER PRIMARY KEY, state TEXT UNIQUE NOT NULL,
                    previous_sha256 TEXT, receipt_sha256 TEXT UNIQUE NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
                )

    def commit(
        self, state: str, objects: dict[str, bytes], payload: dict[str, Any]
    ) -> str:
        if state not in STATES:
            raise ValueError("unknown state")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute(
                "SELECT sequence, state, receipt_sha256 FROM receipts ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 0 if last is None else last[0] + 1
            if state != STATES[sequence]:
                raise ValueError("transaction state out of order")
            previous = None if last is None else last[2]
            object_hashes = {}
            for kind, raw in objects.items():
                sha = digest_bytes(raw)
                existing = db.execute(
                    "SELECT payload FROM objects WHERE sha256=? AND kind=?", (sha, kind)
                ).fetchone()
                if existing and existing[0] != raw:
                    raise ValueError("content-address collision")
                db.execute(
                    "INSERT OR IGNORE INTO objects(sha256,kind,payload) VALUES(?,?,?)",
                    (sha, kind, raw),
                )
                object_hashes[kind] = sha
            receipt_payload = {
                "sequence": sequence,
                "state": state,
                "previous_sha256": previous,
                "object_hashes": object_hashes,
                "payload": payload,
            }
            receipt_hash = digest_json(receipt_payload)
            db.execute(
                "INSERT INTO receipts VALUES(?,?,?,?,?)",
                (
                    sequence,
                    state,
                    previous,
                    receipt_hash,
                    canonical_json(receipt_payload),
                ),
            )
            db.commit()
            return receipt_hash

    def verify(self) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT sequence,state,previous_sha256,receipt_sha256,payload_json "
                "FROM receipts ORDER BY sequence"
            ).fetchall()
            previous = None
            for sequence, state, prior, receipt, payload_json in rows:
                if sequence >= len(STATES) or state != STATES[sequence] or prior != previous:
                    raise ValueError("ledger sequence mismatch")
                payload = json.loads(payload_json)
                if digest_json(payload) != receipt:
                    raise ValueError("ledger receipt hash mismatch")
                for kind, sha in payload["object_hashes"].items():
                    obj = db.execute(
                        "SELECT kind,payload FROM objects WHERE sha256=? AND kind=?",
                        (sha, kind),
                    ).fetchone()
                    if obj is None or obj[0] != kind or digest_bytes(obj[1]) != sha:
                        raise ValueError("ledger object mismatch")
                previous = receipt

    def states(self) -> list[str]:
        with closing(sqlite3.connect(self.path)) as db:
            return [
                row[0]
                for row in db.execute("SELECT state FROM receipts ORDER BY sequence")
            ]


def make_assignment(matrix: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    matrix_hash = digest_json(matrix)
    scenes = {scene["scene_id"]: scene for scene in protocol["scenes"]}
    assignments = []
    for index, block in enumerate(matrix["blocks"], start=1):
        scene = scenes[block["scene_id"]]
        option = scene["decision_contract"]["allowed_values"][
            block["assigned_option_index_for_b_c"]
        ]
        assignments.append(
            {
                "assignment_id": f"ASSIGN-{index:02d}",
                "block_id": block["block_id"],
                "scene_id": scene["scene_id"],
                "decision_id": scene["decision_contract"]["decision_id"],
                "selected_value": option["value"],
                "selected_definition": option["definition"],
                "selected_summary": option["selected_summary"],
                "matrix_sha256": matrix_hash,
            }
        )
    return {"matrix_sha256": matrix_hash, "assignments": assignments}


def build_request(
    protocol: dict[str, Any],
    matrix: dict[str, Any],
    assignments: dict[str, Any],
    block_id: str,
    arm: str,
) -> tuple[dict[str, Any], str]:
    block = next((item for item in matrix["blocks"] if item["block_id"] == block_id), None)
    if block is None or arm not in {"A", "B", "C"}:
        raise ValueError("unregistered block or arm")
    scene = next(item for item in protocol["scenes"] if item["scene_id"] == block["scene_id"])
    base = {
        key: scene[key]
        for key in (
            "scene_id", "scene", "characters", "world_facts", "primary_obligation",
            "decision_shape", "long_term_problem", "mandatory_events",
            "forbidden_events", "style_signature", "target_chars"
        )
    }
    assignment = next(
        item for item in assignments["assignments"] if item["block_id"] == block_id
    )
    expected_assignments = make_assignment(matrix, protocol)
    if assignments != expected_assignments:
        raise ValueError("assignment ledger is not canonical")
    common = COMMON_BOUNDARY_SENTENCE + "不得输出分析、规则、枚举名或检查清单。"
    if arm == "A":
        instruction = "从共享合同的两个合法方案中自行选择且只选择一个。" + common
        input_payload = {**base, "shared_decision_contract": scene["decision_contract"]}
        assignment_hash = None
    elif arm == "B":
        instruction = common
        input_payload = {
            **base,
            "shared_decision_contract": scene["decision_contract"],
            "locked_assignment": assignment,
        }
        assignment_hash = digest_json(assignment)
    else:
        instruction = common
        input_payload = {**base, "locked_content_boundary": assignment["selected_summary"]}
        assignment_hash = digest_json(assignment)
    envelope = {
        "schema_version": "1.2-r3-request",
        "experiment_id": "writer-boundary-v1-2-r3",
        "block_id": block_id,
        "scene_id": block["scene_id"],
        "text_id": block["text_ids"][arm],
        "arm": arm,
        "provider_config": protocol["provider_config"],
        "protocol_sha256": digest_json(protocol),
        "matrix_sha256": digest_json(matrix),
        "assignment_sha256": assignment_hash,
        "messages": [{"role": "user", "content": {"instruction": instruction, "input": input_payload}}],
    }
    return envelope, digest_json(envelope)


class FakeProviderGateway:
    def consume(
        self, envelope: dict[str, Any], expected_hash: str, *, mismatch: bool = False
    ) -> ProviderReceipt:
        consumed = digest_json(envelope)
        if mismatch:
            consumed = "0" * 64
        if consumed != expected_hash:
            raise ValueError("provider consumed-envelope mismatch")
        return ProviderReceipt(
            request_id=f"SYNTH-{envelope['text_id']}",
            expected_envelope_sha256=expected_hash,
            consumed_envelope_sha256=consumed,
            capability_status="synthetic_only",
            retry_count=0,
            synthetic=True,
        )


def create_private_join(
    matrix: dict[str, Any], entropy: bytes | None = None
) -> tuple[list[dict[str, Any]], str]:
    entropy = secrets.token_bytes(32) if entropy is None else entropy
    scored = []
    for block in matrix["blocks"]:
        for arm in ("A", "B", "C"):
            score = digest_bytes(entropy + f"{block['block_id']}:{arm}".encode())
            scored.append((score, block, arm))
    scored.sort()
    public_ids = {item[0]: f"PUB-{index:03d}" for index, item in enumerate(scored, 1)}
    rows = []
    for block in matrix["blocks"]:
        local = sorted(
            (item for item in scored if item[1]["block_id"] == block["block_id"]),
            key=lambda item: item[0],
        )
        for position, (score, _, arm) in enumerate(local, 1):
            rows.append(
                {
                    "block_id": block["block_id"],
                    "scene_id": block["scene_id"],
                    "private_text_id": block["text_ids"][arm],
                    "arm": arm,
                    "public_text_id": public_ids[score],
                    "public_position": position,
                }
            )
    commitment = digest_json({"entropy_sha256": digest_bytes(entropy), "rows": rows})
    return rows, commitment


def bind_audit(
    audit: ExecutionAudit,
    *,
    text_bytes: bytes,
    envelope: dict[str, Any],
    matrix: dict[str, Any],
    allowed_values: set[str],
) -> str:
    block = next((item for item in matrix["blocks"] if item["block_id"] == audit.block_id), None)
    if block is None or block["scene_id"] != audit.scene_id:
        raise ValueError("audit matrix identity mismatch")
    if block["text_ids"][audit.arm] != audit.text_id:
        raise ValueError("audit text identity mismatch")
    if audit.request_sha256 != digest_json(envelope):
        raise ValueError("audit request mismatch")
    if audit.content_sha256 != digest_bytes(text_bytes):
        raise ValueError("audit content mismatch")
    if audit.observed_decision not in allowed_values | {"unclear", "other"}:
        raise ValueError("audit observed decision invalid")
    return digest_json(audit.model_dump(mode="json"))


def bind_vote(
    vote: PreferenceVote,
    public_contents: dict[str, bytes],
) -> str:
    if vote.public_a_id not in public_contents or vote.public_c_id not in public_contents:
        raise ValueError("vote public identity missing")
    if digest_bytes(public_contents[vote.public_a_id]) != vote.public_a_content_sha256:
        raise ValueError("vote A content mismatch")
    if digest_bytes(public_contents[vote.public_c_id]) != vote.public_c_content_sha256:
        raise ValueError("vote C content mismatch")
    return digest_json(vote.model_dump(mode="json"))


def aggregate_primary(
    *,
    matrix: dict[str, Any],
    locked_matrix_hash: str,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    if digest_json(matrix) != locked_matrix_hash:
        raise ValueError("aggregate matrix hash mismatch")
    expected = {
        (block["block_id"], block["scene_id"]) for block in matrix["blocks"]
    }
    actual = {(item["block_id"], item["scene_id"]) for item in outcomes}
    if len(outcomes) != 12 or actual != expected:
        raise ValueError("aggregate analysis set mismatch")
    for item in outcomes:
        a_present = item["a_status"] == "present"
        c_present = item["c_status"] == "present"
        expected_preference = None
        if a_present and not c_present:
            expected_preference = "A"
        elif not a_present and c_present:
            expected_preference = "C"
        elif not a_present and not c_present:
            expected_preference = "no_evidence"
        if expected_preference and any(
            item[metric] != expected_preference
            for metric in ("naturalness", "less_template", "overall_quality")
        ):
            raise ValueError("missing/failure preference truth table violated")
        if not c_present and item["hard_non_degradation"]:
            raise ValueError("missing C cannot pass hard non-degradation")
    score_map = {"A": 0.0, "tie": 0.5, "C": 1.0, "no_evidence": 0.0}
    metrics = {}
    for metric in ("naturalness", "less_template", "overall_quality"):
        score = sum(score_map[item[metric]] for item in outcomes)
        scene_ok = sum(
            sum(
                item[metric] in {"C", "tie"}
                and item["a_status"] == item["c_status"] == "present"
                for item in outcomes if item["scene_id"] == scene
            ) >= 2
            for scene in {item["scene_id"] for item in outcomes}
        )
        metrics[metric] = {"score": score, "threshold": score >= 8, "scene_count": scene_ok}
    evaluable = sum(
        item["a_status"] == item["c_status"] == "present" for item in outcomes
    )
    hard_ok = all(item["hard_non_degradation"] for item in outcomes)
    expand = (
        evaluable == 12
        and hard_ok
        and all(value["threshold"] and value["scene_count"] >= 3 for value in metrics.values())
    )
    return {
        "fixed_denominator": 12,
        "evaluable_pairs": evaluable,
        "metrics": metrics,
        "hard_non_degradation": hard_ok,
        "conclusion": "directional_expand_signal" if expand else "do_not_expand",
        "single_composite_score": None,
        "confirmatory_causal_claim_allowed": False,
    }
