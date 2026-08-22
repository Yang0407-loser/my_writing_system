from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes, digest_json

from .models import NeutralExecutionAudit, NeutralPreferenceVote


STATES = [
    "DESIGN_LOCKED",
    "ASSIGNMENTS_LOCKED",
    "REQUESTS_LOCKED",
    "TEXTS_LOCKED",
    "ANONYMITY_MAP_LOCKED",
    "EXECUTION_PACK_LOCKED",
    "AUDITS_LOCKED",
    "PREFERENCE_PACK_LOCKED",
    "VOTES_LOCKED",
    "IDENTITY_UNBLINDED",
    "AGGREGATED",
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class R31Ledger:
    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects(
                    sha256 TEXT NOT NULL, kind TEXT NOT NULL, payload BLOB NOT NULL,
                    visibility TEXT NOT NULL CHECK(visibility IN ('public','private')),
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
        self,
        state: str,
        objects: dict[str, tuple[bytes, str]],
        payload: dict[str, Any],
    ) -> str:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute(
                "SELECT sequence,receipt_sha256 FROM receipts ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 0 if last is None else last[0] + 1
            if sequence >= len(STATES) or state != STATES[sequence]:
                raise ValueError("transaction state out of order")
            previous = None if last is None else last[1]
            object_hashes: dict[str, str] = {}
            for kind, (raw, visibility) in objects.items():
                sha = digest_bytes(raw)
                db.execute(
                    "INSERT OR IGNORE INTO objects VALUES(?,?,?,?)",
                    (sha, kind, raw, visibility),
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
                (sequence, state, previous, receipt_hash, canonical_json(receipt_payload)),
            )
            db.commit()
            return receipt_hash

    def states(self) -> list[str]:
        with closing(sqlite3.connect(self.path)) as db:
            return [row[0] for row in db.execute("SELECT state FROM receipts ORDER BY sequence")]

    def verify(self) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT sequence,state,previous_sha256,receipt_sha256,payload_json "
                "FROM receipts ORDER BY sequence"
            ).fetchall()
            previous = None
            for sequence, state, prior, receipt, payload_json in rows:
                if state != STATES[sequence] or prior != previous:
                    raise ValueError("ledger sequence mismatch")
                payload = json.loads(payload_json)
                if digest_json(payload) != receipt:
                    raise ValueError("ledger receipt mismatch")
                for kind, sha in payload["object_hashes"].items():
                    row = db.execute(
                        "SELECT payload FROM objects WHERE sha256=? AND kind=?", (sha, kind)
                    ).fetchone()
                    if row is None or digest_bytes(row[0]) != sha:
                        raise ValueError("ledger object mismatch")
                previous = receipt


def _token(entropy: bytes, label: str, length: int = 12) -> str:
    return hashlib.sha256(entropy + label.encode("utf-8")).hexdigest()[:length].upper()


def create_anonymity_map(
    matrix: dict[str, Any],
    content_hashes: dict[str, str],
    *,
    entropy: bytes | None = None,
) -> dict[str, Any]:
    entropy = secrets.token_bytes(32) if entropy is None else entropy
    rows: list[dict[str, Any]] = []
    preference_blocks: list[dict[str, Any]] = []
    for block in matrix["blocks"]:
        block_token = _token(entropy, f"block:{block['block_id']}")
        public_block_id = f"PB-{block_token}"
        local: dict[str, dict[str, Any]] = {}
        for arm in ("A", "B", "C"):
            private_text_id = block["text_ids"][arm]
            public_text_id = f"PT-{_token(entropy, f'text:{private_text_id}')}"
            row = {
                "private_block_id": block["block_id"],
                "scene_id": block["scene_id"],
                "arm": arm,
                "private_text_id": private_text_id,
                "public_text_id": public_text_id,
                "content_sha256": content_hashes[private_text_id],
            }
            rows.append(row)
            local[arm] = row
        ordered = sorted(
            (local["A"], local["C"]),
            key=lambda row: _token(entropy, f"display:{row['private_text_id']}"),
        )
        preference_blocks.append(
            {
                "private_block_id": block["block_id"],
                "scene_id": block["scene_id"],
                "public_block_id": public_block_id,
                "candidate_1_public_text_id": ordered[0]["public_text_id"],
                "candidate_1_arm": ordered[0]["arm"],
                "candidate_2_public_text_id": ordered[1]["public_text_id"],
                "candidate_2_arm": ordered[1]["arm"],
            }
        )
    private = {"rows": rows, "preference_blocks": preference_blocks}
    return {
        **private,
        "commitment_sha256": digest_json(
            {"entropy_sha256": digest_bytes(entropy), "private_map": private}
        ),
    }


def build_public_packs(
    anonymity_map: dict[str, Any],
    text_bytes: dict[str, bytes],
) -> tuple[dict[str, Any], dict[str, Any]]:
    audit_items = []
    public_by_id: dict[str, dict[str, Any]] = {}
    for row in anonymity_map["rows"]:
        raw = text_bytes[row["private_text_id"]]
        item = {
            "public_text_id": row["public_text_id"],
            "scene_id": row["scene_id"],
            "content_sha256": digest_bytes(raw),
            "text": raw.decode("utf-8"),
        }
        audit_items.append(item)
        public_by_id[row["public_text_id"]] = item
    preference_items = []
    for block in anonymity_map["preference_blocks"]:
        one = public_by_id[block["candidate_1_public_text_id"]]
        two = public_by_id[block["candidate_2_public_text_id"]]
        preference_items.append(
            {
                "public_block_id": block["public_block_id"],
                "scene_id": block["scene_id"],
                "candidate_1": one,
                "candidate_2": two,
            }
        )
    return (
        {
            "schema_version": "1.2-r3.1-public-execution-pack",
            "instructions": "仅依据公开文本完成硬检查；不得推测生成路线或读取偏好票。",
            "items": audit_items,
        },
        {
            "schema_version": "1.2-r3.1-public-preference-pack",
            "instructions": "candidate_1 与 candidate_2 的显示顺序随机；不得推测生成路线。",
            "blocks": preference_items,
        },
    )


def bind_neutral_audit(
    audit: NeutralExecutionAudit,
    *,
    anonymity_map: dict[str, Any],
    text_bytes: dict[str, bytes],
    allowed_values_by_scene: dict[str, set[str]],
) -> str:
    matches = [
        row for row in anonymity_map["rows"]
        if row["public_text_id"] == audit.public_text_id
    ]
    if len(matches) != 1:
        raise ValueError("audit public identity missing or duplicated")
    row = matches[0]
    if audit.scene_id != row["scene_id"]:
        raise ValueError("audit scene mismatch")
    raw = text_bytes[row["private_text_id"]]
    if audit.content_sha256 != digest_bytes(raw):
        raise ValueError("audit content mismatch")
    if audit.observed_decision not in allowed_values_by_scene[audit.scene_id] | {"unclear", "other"}:
        raise ValueError("audit observed decision invalid")
    return digest_json(audit.model_dump(mode="json"))


def bind_neutral_vote(
    vote: NeutralPreferenceVote,
    *,
    anonymity_map: dict[str, Any],
    public_contents: dict[str, bytes],
) -> str:
    blocks = [
        block for block in anonymity_map["preference_blocks"]
        if block["public_block_id"] == vote.public_block_id
    ]
    if len(blocks) != 1:
        raise ValueError("vote block missing or duplicated")
    block = blocks[0]
    expected = (
        block["candidate_1_public_text_id"],
        block["candidate_2_public_text_id"],
    )
    if (vote.candidate_1_id, vote.candidate_2_id) != expected:
        raise ValueError("candidate order or block membership mismatch")
    for candidate_id, claimed in (
        (vote.candidate_1_id, vote.candidate_1_content_sha256),
        (vote.candidate_2_id, vote.candidate_2_content_sha256),
    ):
        if candidate_id not in public_contents or digest_bytes(public_contents[candidate_id]) != claimed:
            raise ValueError("vote content mismatch")
    return digest_json(vote.model_dump(mode="json"))


def unblind_votes(
    votes: list[NeutralPreferenceVote],
    *,
    anonymity_map: dict[str, Any],
    locked_states: list[str],
) -> list[dict[str, Any]]:
    if "VOTES_LOCKED" not in locked_states:
        raise ValueError("identity cannot be unblinded before votes lock")
    blocks = {
        item["public_block_id"]: item for item in anonymity_map["preference_blocks"]
    }
    reviewer_ids = {vote.reviewer_id for vote in votes}
    if len(reviewer_ids) != 3 or len(votes) != len(blocks) * 3:
        raise ValueError("vote set must contain three complete reviewer ballots")
    for reviewer_id in reviewer_ids:
        reviewer_blocks = [
            vote.public_block_id for vote in votes if vote.reviewer_id == reviewer_id
        ]
        if len(reviewer_blocks) != len(set(reviewer_blocks)) or set(reviewer_blocks) != set(blocks):
            raise ValueError("reviewer ballot has duplicate or missing blocks")
    normalized = []
    for vote in votes:
        block = blocks[vote.public_block_id]
        arm_by_choice = {
            "candidate_1": block["candidate_1_arm"],
            "candidate_2": block["candidate_2_arm"],
            "tie": "tie",
        }
        normalized.append(
            {
                "reviewer_id": vote.reviewer_id,
                "private_block_id": block["private_block_id"],
                "scene_id": block["scene_id"],
                **{
                    metric: arm_by_choice[getattr(vote, metric)]
                    for metric in ("naturalness", "less_template", "overall_quality")
                },
            }
        )
    return normalized


def assert_public_pack_neutral(value: Any) -> None:
    forbidden_keys = {
        "arm", "private_text_id", "private_block_id", "request_sha256",
        "assignment_sha256", "candidate_1_arm", "candidate_2_arm",
        "public_a_id", "public_c_id",
    }
    if isinstance(value, dict):
        leaked = forbidden_keys & set(value)
        if leaked:
            raise ValueError(f"public pack leaks private keys: {sorted(leaked)}")
        for child in value.values():
            assert_public_pack_neutral(child)
    elif isinstance(value, list):
        for child in value:
            assert_public_pack_neutral(child)
