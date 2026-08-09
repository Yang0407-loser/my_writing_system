from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import digest_bytes, digest_json
from experiments.writer_boundary_v12_r3.kernel import aggregate_primary

from .models import NeutralAudit, NeutralVote


STATES = [
    "DESIGN_LOCKED",
    "ASSIGNMENTS_LOCKED",
    "REQUESTS_LOCKED",
    "TEXTS_LOCKED",
    "ANONYMITY_MAP_LOCKED",
    "EXECUTION_DISTRIBUTION_LOCKED",
    "AUDITS_LOCKED",
    "PREFERENCE_DISTRIBUTION_LOCKED",
    "VOTES_LOCKED",
    "IDENTITY_UNBLINDED",
    "AGGREGATED",
]
STATE_ROLES = {
    "DESIGN_LOCKED": "custodian",
    "ASSIGNMENTS_LOCKED": "custodian",
    "REQUESTS_LOCKED": "custodian",
    "TEXTS_LOCKED": "text_ingestor",
    "ANONYMITY_MAP_LOCKED": "blind_pack_custodian",
    "EXECUTION_DISTRIBUTION_LOCKED": "blind_pack_custodian",
    "AUDITS_LOCKED": "execution_auditor",
    "PREFERENCE_DISTRIBUTION_LOCKED": "blind_pack_custodian",
    "VOTES_LOCKED": "preference_coordinator",
    "IDENTITY_UNBLINDED": "identity_custodian",
    "AGGREGATED": "aggregator",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_roster(roster: dict[str, Any]) -> None:
    required = set(STATE_ROLES.values())
    if set(roster["actors"]) != required:
        raise ValueError("role roster is incomplete")
    actors = list(roster["actors"].values())
    reviewers = roster["preference_reviewers"]
    if len(actors) != len(set(actors)) or len(reviewers) != 3 or len(set(reviewers)) != 3:
        raise ValueError("roles and reviewers must be distinct")
    if set(actors) & set(reviewers):
        raise ValueError("preference reviewers cannot hold transaction roles")


class ReceiptLedger:
    def __init__(self, path: Path, roster: dict[str, Any]):
        validate_roster(roster)
        self.path = path
        self.roster = roster
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects(
                    sha256 TEXT NOT NULL, kind TEXT NOT NULL, payload BLOB NOT NULL,
                    visibility TEXT NOT NULL CHECK(visibility IN ('public','private')),
                    PRIMARY KEY(sha256,kind)
                );
                CREATE TABLE IF NOT EXISTS receipts(
                    sequence INTEGER PRIMARY KEY, state TEXT UNIQUE NOT NULL,
                    actor_id TEXT NOT NULL, role TEXT NOT NULL,
                    previous_sha256 TEXT, receipt_sha256 TEXT UNIQUE NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def commit(
        self,
        state: str,
        *,
        actor_id: str,
        role: str,
        objects: dict[str, tuple[bytes, str]],
        payload: dict[str, Any],
    ) -> str:
        if STATE_ROLES.get(state) != role or self.roster["actors"].get(role) != actor_id:
            raise PermissionError("actor is not authorized for transition")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute(
                "SELECT sequence,receipt_sha256 FROM receipts ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 0 if last is None else last[0] + 1
            if sequence >= len(STATES) or STATES[sequence] != state:
                raise ValueError("transaction state out of order")
            previous = None if last is None else last[1]
            descriptors = []
            for kind, (raw, visibility) in objects.items():
                sha = digest_bytes(raw)
                existing = db.execute(
                    "SELECT payload,visibility FROM objects WHERE sha256=? AND kind=?",
                    (sha, kind),
                ).fetchone()
                if existing and (existing[0] != raw or existing[1] != visibility):
                    raise ValueError("object collision or visibility drift")
                db.execute(
                    "INSERT OR IGNORE INTO objects VALUES(?,?,?,?)",
                    (sha, kind, raw, visibility),
                )
                descriptors.append(
                    {"kind": kind, "sha256": sha, "visibility": visibility, "bytes": len(raw)}
                )
            receipt_payload = {
                "sequence": sequence,
                "state": state,
                "actor_id": actor_id,
                "role": role,
                "previous_sha256": previous,
                "objects": sorted(descriptors, key=lambda item: item["kind"]),
                "payload": payload,
            }
            receipt_hash = digest_json(receipt_payload)
            db.execute(
                "INSERT INTO receipts VALUES(?,?,?,?,?,?,?)",
                (
                    sequence, state, actor_id, role, previous, receipt_hash,
                    canonical_json(receipt_payload),
                ),
            )
            db.commit()
            return receipt_hash

    def verify(self, *, expected_terminal_state: str, checkpoint_sha256: str) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT sequence,state,actor_id,role,previous_sha256,receipt_sha256,payload_json "
                "FROM receipts ORDER BY sequence"
            ).fetchall()
            terminal_index = STATES.index(expected_terminal_state)
            if len(rows) != terminal_index + 1:
                raise ValueError("ledger does not have exact expected terminal state")
            previous = None
            referenced: set[tuple[str, str]] = set()
            for sequence, state, actor_id, role, prior, receipt, payload_json in rows:
                payload = json.loads(payload_json)
                if (
                    state != STATES[sequence]
                    or STATE_ROLES[state] != role
                    or self.roster["actors"][role] != actor_id
                    or prior != previous
                    or digest_json(payload) != receipt
                ):
                    raise ValueError("receipt chain, role, or actor mismatch")
                for descriptor in payload["objects"]:
                    row = db.execute(
                        "SELECT payload,visibility FROM objects WHERE sha256=? AND kind=?",
                        (descriptor["sha256"], descriptor["kind"]),
                    ).fetchone()
                    if (
                        row is None
                        or digest_bytes(row[0]) != descriptor["sha256"]
                        or row[1] != descriptor["visibility"]
                        or len(row[0]) != descriptor["bytes"]
                    ):
                        raise ValueError("object descriptor mismatch")
                    referenced.add((descriptor["sha256"], descriptor["kind"]))
                previous = receipt
            actual_objects = {
                (row[0], row[1]) for row in db.execute("SELECT sha256,kind FROM objects")
            }
            if actual_objects != referenced:
                raise ValueError("orphan or unreceipted object")
            if previous != checkpoint_sha256:
                raise ValueError("external checkpoint mismatch")

    def latest(self) -> tuple[str, str]:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT state,receipt_sha256 FROM receipts ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError("empty ledger")
            return row[0], row[1]

    def read_kind(self, state: str, kind: str) -> tuple[bytes, str, str]:
        with closing(sqlite3.connect(self.path)) as db:
            receipt = db.execute(
                "SELECT receipt_sha256,payload_json FROM receipts WHERE state=?", (state,)
            ).fetchone()
            if receipt is None:
                raise ValueError("receipt missing")
            payload = json.loads(receipt[1])
            descriptor = next(
                (item for item in payload["objects"] if item["kind"] == kind), None
            )
            if descriptor is None:
                raise ValueError("object kind missing")
            row = db.execute(
                "SELECT payload,visibility FROM objects WHERE sha256=? AND kind=?",
                (descriptor["sha256"], kind),
            ).fetchone()
            return row[0], row[1], receipt[0]


def _token(entropy: bytes, label: str) -> str:
    return hashlib.sha256(entropy + label.encode()).hexdigest()[:16].upper()


def create_private_map(
    matrix: dict[str, Any],
    text_manifest: dict[str, Any],
    *,
    entropy: bytes | None = None,
) -> dict[str, Any]:
    entropy = secrets.token_bytes(32) if entropy is None else entropy
    rows = []
    blocks = []
    hashes = {item["private_text_id"]: item["content_sha256"] for item in text_manifest["texts"]}
    for block in matrix["blocks"]:
        local = {}
        for arm in ("A", "B", "C"):
            private_id = block["text_ids"][arm]
            row = {
                "private_block_id": block["block_id"],
                "private_text_id": private_id,
                "scene_id": block["scene_id"],
                "arm": arm,
                "public_text_id": f"PT-{_token(entropy, 'text:'+private_id)}",
                "content_sha256": hashes[private_id],
            }
            rows.append(row)
            local[arm] = row
        pair = sorted(
            (local["A"], local["C"]),
            key=lambda item: _token(entropy, "display:" + item["private_text_id"]),
        )
        blocks.append(
            {
                "private_block_id": block["block_id"],
                "scene_id": block["scene_id"],
                "public_block_id": f"PB-{_token(entropy, 'block:'+block['block_id'])}",
                "candidate_1_public_text_id": pair[0]["public_text_id"],
                "candidate_1_arm": pair[0]["arm"],
                "candidate_2_public_text_id": pair[1]["public_text_id"],
                "candidate_2_arm": pair[1]["arm"],
            }
        )
    rows.sort(key=lambda item: _token(entropy, "execution:" + item["private_text_id"]))
    private = {"rows": rows, "preference_blocks": blocks}
    private["commitment_sha256"] = digest_json(
        {"entropy_sha256": digest_bytes(entropy), "map": private}
    )
    return private


def paragraphize(raw: bytes) -> list[dict[str, str]]:
    chunks = [item.strip() for item in raw.decode("utf-8").split("\n\n") if item.strip()]
    return [{"paragraph_id": f"P{index}", "text": value} for index, value in enumerate(chunks, 1)]


def make_execution_distribution(
    private_map: dict[str, Any],
    texts: dict[str, bytes],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    rubrics = {}
    for scene in protocol["scenes"]:
        rubrics[scene["scene_id"]] = {
            "scene_id": scene["scene_id"],
            "scene": scene["scene"],
            "characters": scene["characters"],
            "world_facts": scene["world_facts"],
            "primary_obligation": scene["primary_obligation"],
            "mandatory_events": scene["mandatory_events"],
            "forbidden_events": scene["forbidden_events"],
            "allowed_decisions": [
                {"value": item["value"], "definition": item["definition"]}
                for item in scene["decision_contract"]["allowed_values"]
            ],
            "hard_check_definitions": {
                "mandatory_events": "全部 M 义务是否完成",
                "unauthorized_new_character": "是否未引入未授权人物",
                "unauthorized_new_solution": "是否未引入白名单外新方案",
                "unauthorized_relationship_change": "是否未产生未授权关系变化",
            },
        }
    items = []
    for row in private_map["rows"]:
        raw = texts[row["private_text_id"]]
        items.append(
            {
                "public_text_id": row["public_text_id"],
                "scene_id": row["scene_id"],
                "content_sha256": digest_bytes(raw),
                "paragraphs": paragraphize(raw),
            }
        )
    return {
        "schema_version": "1.2-r3.2-execution-distribution",
        "paragraph_rule": "paragraphs 中的 paragraph_id 是唯一可引用的 P 编号。",
        "failure_rule": "失败时只能引用对应 scene mandatory_events 中存在的 M 编号。",
        "response_schema": NeutralAudit.model_json_schema(),
        "rubrics": rubrics,
        "items": items,
    }


def make_preference_distribution(
    private_map: dict[str, Any],
    execution_distribution: dict[str, Any],
) -> dict[str, Any]:
    by_id = {item["public_text_id"]: item for item in execution_distribution["items"]}
    blocks = []
    for private in private_map["preference_blocks"]:
        blocks.append(
            {
                "public_block_id": private["public_block_id"],
                "scene_id": private["scene_id"],
                "candidate_1": by_id[private["candidate_1_public_text_id"]],
                "candidate_2": by_id[private["candidate_2_public_text_id"]],
            }
        )
    return {
        "schema_version": "1.2-r3.2-preference-distribution",
        "metric_definitions": {
            "naturalness": "哪篇更自然",
            "less_template": "哪篇更不模板化",
            "overall_quality": "哪篇整体质量更高",
        },
        "response_schema": NeutralVote.model_json_schema(),
        "blocks": blocks,
    }


def distribution_manifest(
    *, recipient_role: str, files: dict[str, bytes], prohibited: list[str]
) -> dict[str, Any]:
    return {
        "recipient_role": recipient_role,
        "files": [
            {"path": path, "sha256": digest_bytes(raw), "bytes": len(raw)}
            for path, raw in sorted(files.items())
        ],
        "prohibited_materials": sorted(prohibited),
    }


def lock_anonymity_map_from_ledger(
    ledger: ReceiptLedger,
    *,
    checkpoint_sha256: str,
    actor_id: str,
    entropy: bytes | None = None,
) -> tuple[dict[str, Any], str]:
    ledger.verify(expected_terminal_state="TEXTS_LOCKED", checkpoint_sha256=checkpoint_sha256)
    matrix_raw, _, _ = ledger.read_kind("DESIGN_LOCKED", "matrix")
    manifest_raw, _, texts_receipt = ledger.read_kind("TEXTS_LOCKED", "text_manifest")
    private_map = create_private_map(
        json.loads(matrix_raw), json.loads(manifest_raw), entropy=entropy
    )
    receipt = ledger.commit(
        "ANONYMITY_MAP_LOCKED",
        actor_id=actor_id,
        role="blind_pack_custodian",
        objects={"private_map": (canonical_json(private_map).encode(), "private")},
        payload={"source_texts_receipt_sha256": texts_receipt},
    )
    return private_map, receipt


def lock_execution_distribution_from_ledger(
    ledger: ReceiptLedger,
    *,
    checkpoint_sha256: str,
    actor_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes], str]:
    ledger.verify(
        expected_terminal_state="ANONYMITY_MAP_LOCKED",
        checkpoint_sha256=checkpoint_sha256,
    )
    protocol_raw, _, _ = ledger.read_kind("DESIGN_LOCKED", "protocol")
    assignments_raw, _, _ = ledger.read_kind("ASSIGNMENTS_LOCKED", "assignments")
    bundle_raw, _, texts_receipt = ledger.read_kind("TEXTS_LOCKED", "text_bundle")
    map_raw, _, map_receipt = ledger.read_kind("ANONYMITY_MAP_LOCKED", "private_map")
    private_map = json.loads(map_raw)
    texts = {
        key: value.encode("utf-8") for key, value in json.loads(bundle_raw).items()
    }
    distribution = make_execution_distribution(
        private_map, texts, json.loads(protocol_raw)
    )
    private_ids = (
        {item["private_text_id"] for item in private_map["rows"]}
        | {item["private_block_id"] for item in private_map["rows"]}
        | {
            item["assignment_id"]
            for item in json.loads(assignments_raw)["assignments"]
        }
    )
    assert_public_neutral(
        distribution,
        private_identifiers=private_ids,
        private_arm_sequence=[item["arm"] for item in private_map["rows"]],
    )
    path = "public/execution-reviewer/distribution.json"
    raw = (json.dumps(distribution, ensure_ascii=False, indent=2) + "\n").encode()
    files = {path: raw}
    manifest = distribution_manifest(
        recipient_role="execution_auditor",
        files=files,
        prohibited=[
            "private/ledger", "private/anonymity-map", "requests",
            "assignments", "other-reviewer-results",
        ],
    )
    receipt = ledger.commit(
        "EXECUTION_DISTRIBUTION_LOCKED",
        actor_id=actor_id,
        role="blind_pack_custodian",
        objects={
            "execution_distribution": (canonical_json(distribution).encode(), "public"),
            "execution_distribution_manifest": (canonical_json(manifest).encode(), "public"),
        },
        payload={
            "source_texts_receipt_sha256": texts_receipt,
            "source_map_receipt_sha256": map_receipt,
        },
    )
    return distribution, manifest, files, receipt


def lock_preference_distribution_from_ledger(
    ledger: ReceiptLedger,
    *,
    checkpoint_sha256: str,
    actor_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes], str]:
    ledger.verify(expected_terminal_state="AUDITS_LOCKED", checkpoint_sha256=checkpoint_sha256)
    map_raw, _, map_receipt = ledger.read_kind("ANONYMITY_MAP_LOCKED", "private_map")
    execution_raw, _, execution_receipt = ledger.read_kind(
        "EXECUTION_DISTRIBUTION_LOCKED", "execution_distribution"
    )
    _, _, audits_receipt = ledger.read_kind("AUDITS_LOCKED", "locked_audits")
    private_map = json.loads(map_raw)
    distribution = make_preference_distribution(
        private_map, json.loads(execution_raw)
    )
    assert_public_neutral(
        distribution,
        private_identifiers=(
            {item["private_text_id"] for item in private_map["rows"]}
            | {item["private_block_id"] for item in private_map["rows"]}
        ),
    )
    path = "public/preference-reviewer/distribution.json"
    raw = (json.dumps(distribution, ensure_ascii=False, indent=2) + "\n").encode()
    files = {path: raw}
    manifest = distribution_manifest(
        recipient_role="preference_reviewer",
        files=files,
        prohibited=[
            "private/ledger", "private/anonymity-map", "requests",
            "assignments", "execution-audits", "other-reviewer-results",
        ],
    )
    receipt = ledger.commit(
        "PREFERENCE_DISTRIBUTION_LOCKED",
        actor_id=actor_id,
        role="blind_pack_custodian",
        objects={
            "preference_distribution": (canonical_json(distribution).encode(), "public"),
            "preference_distribution_manifest": (canonical_json(manifest).encode(), "public"),
        },
        payload={
            "source_map_receipt_sha256": map_receipt,
            "source_execution_receipt_sha256": execution_receipt,
            "source_audits_receipt_sha256": audits_receipt,
        },
    )
    return distribution, manifest, files, receipt


def assert_public_neutral(
    value: Any,
    *,
    private_identifiers: set[str],
    private_arm_sequence: list[str] | None = None,
) -> None:
    forbidden_keys = {
        "arm", "private_text_id", "private_block_id", "request_sha256",
        "assignment_sha256", "candidate_1_arm", "candidate_2_arm",
        "public_a_id", "public_c_id",
    }
    strings: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if forbidden_keys & set(item):
                raise ValueError("public key leakage")
            for key, child in item.items():
                strings.append(str(key))
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            strings.append(item)

    walk(value)
    joined = "\n".join(strings)
    if any(identifier and identifier in joined for identifier in private_identifiers):
        raise ValueError("public value leakage")
    if private_arm_sequence and private_arm_sequence == ["A", "B", "C"] * 12:
        raise ValueError("fixed route order leakage")


def validate_audit_against_distribution(
    audit: NeutralAudit,
    *,
    distribution: dict[str, Any],
) -> None:
    items = [item for item in distribution["items"] if item["public_text_id"] == audit.public_text_id]
    if len(items) != 1:
        raise ValueError("public text missing or duplicated")
    item = items[0]
    if audit.scene_id != item["scene_id"] or audit.content_sha256 != item["content_sha256"]:
        raise ValueError("audit identity or content mismatch")
    rubric = distribution["rubrics"][audit.scene_id]
    if audit.artifact_status != "present":
        return
    allowed_p = {item["paragraph_id"] for item in item["paragraphs"]}
    allowed_m = {entry.split(" ", 1)[0] for entry in rubric["mandatory_events"]}
    allowed_decisions = {entry["value"] for entry in rubric["allowed_decisions"]}
    if audit.observed_decision not in allowed_decisions | {"unclear", "other"}:
        raise ValueError("decision outside public rubric")
    for evidence in audit.hard_checks:
        if not set(evidence.paragraph_ids) <= allowed_p:
            raise ValueError("paragraph evidence does not exist")
        if not set(evidence.failure_m_ids) <= allowed_m:
            raise ValueError("M evidence does not exist in scene")


def validate_votes(
    votes: list[NeutralVote],
    *,
    distribution: dict[str, Any],
    reviewer_roster: list[str],
) -> None:
    if {vote.reviewer_id for vote in votes} != set(reviewer_roster):
        raise ValueError("vote reviewer roster mismatch")
    blocks = {item["public_block_id"]: item for item in distribution["blocks"]}
    if len(votes) != len(blocks) * len(reviewer_roster):
        raise ValueError("vote count mismatch")
    for reviewer in reviewer_roster:
        subset = [vote for vote in votes if vote.reviewer_id == reviewer]
        if {vote.public_block_id for vote in subset} != set(blocks) or len(subset) != len(blocks):
            raise ValueError("duplicate or missing reviewer block")
    for vote in votes:
        block = blocks[vote.public_block_id]
        expected = (block["candidate_1"], block["candidate_2"])
        if (vote.candidate_1_id, vote.candidate_2_id) != tuple(
            item["public_text_id"] for item in expected
        ):
            raise ValueError("candidate membership or order mismatch")
        if (
            vote.candidate_1_content_sha256,
            vote.candidate_2_content_sha256,
        ) != tuple(item["content_sha256"] for item in expected):
            raise ValueError("candidate content mismatch")


def lock_audits_from_ledger(
    ledger: ReceiptLedger,
    audits: list[NeutralAudit],
    *,
    checkpoint_sha256: str,
    actor_id: str,
) -> str:
    ledger.verify(
        expected_terminal_state="EXECUTION_DISTRIBUTION_LOCKED",
        checkpoint_sha256=checkpoint_sha256,
    )
    distribution_raw, _, distribution_receipt = ledger.read_kind(
        "EXECUTION_DISTRIBUTION_LOCKED", "execution_distribution"
    )
    distribution = json.loads(distribution_raw)
    expected_ids = {item["public_text_id"] for item in distribution["items"]}
    if len(audits) != len(expected_ids) or {audit.public_text_id for audit in audits} != expected_ids:
        raise ValueError("audit set is duplicate, missing, or foreign")
    for audit in audits:
        validate_audit_against_distribution(audit, distribution=distribution)
    return ledger.commit(
        "AUDITS_LOCKED",
        actor_id=actor_id,
        role="execution_auditor",
        objects={
            "locked_audits": (
                canonical_json([audit.model_dump(mode="json") for audit in audits]).encode(),
                "private",
            )
        },
        payload={"source_distribution_receipt_sha256": distribution_receipt},
    )


def lock_votes_from_ledger(
    ledger: ReceiptLedger,
    votes: list[NeutralVote],
    *,
    checkpoint_sha256: str,
    actor_id: str,
) -> str:
    ledger.verify(
        expected_terminal_state="PREFERENCE_DISTRIBUTION_LOCKED",
        checkpoint_sha256=checkpoint_sha256,
    )
    distribution_raw, _, distribution_receipt = ledger.read_kind(
        "PREFERENCE_DISTRIBUTION_LOCKED", "preference_distribution"
    )
    roster_raw, _, design_receipt = ledger.read_kind("DESIGN_LOCKED", "role_roster")
    roster = json.loads(roster_raw)
    validate_votes(
        votes,
        distribution=json.loads(distribution_raw),
        reviewer_roster=roster["preference_reviewers"],
    )
    return ledger.commit(
        "VOTES_LOCKED",
        actor_id=actor_id,
        role="preference_coordinator",
        objects={
            "locked_votes": (
                canonical_json([vote.model_dump(mode="json") for vote in votes]).encode(),
                "private",
            )
        },
        payload={
            "source_distribution_receipt_sha256": distribution_receipt,
            "source_roster_receipt_sha256": design_receipt,
        },
    )


def unblind_from_ledger(
    ledger: ReceiptLedger,
    *,
    checkpoint_sha256: str,
    actor_id: str,
) -> tuple[list[dict[str, Any]], str]:
    ledger.verify(expected_terminal_state="VOTES_LOCKED", checkpoint_sha256=checkpoint_sha256)
    votes_raw, _, votes_receipt = ledger.read_kind("VOTES_LOCKED", "locked_votes")
    map_raw, _, _ = ledger.read_kind("ANONYMITY_MAP_LOCKED", "private_map")
    votes = [NeutralVote.model_validate(item) for item in json.loads(votes_raw)]
    private_map = json.loads(map_raw)
    by_block = {
        item["public_block_id"]: item for item in private_map["preference_blocks"]
    }
    normalized = []
    for vote in votes:
        block = by_block[vote.public_block_id]
        choice_to_arm = {
            "candidate_1": block["candidate_1_arm"],
            "candidate_2": block["candidate_2_arm"],
            "tie": "tie",
        }
        normalized.append(
            {
                "reviewer_id": vote.reviewer_id,
                "private_block_id": block["private_block_id"],
                "scene_id": block["scene_id"],
                "naturalness": choice_to_arm[vote.naturalness],
                "less_template": choice_to_arm[vote.less_template],
                "overall_quality": choice_to_arm[vote.overall_quality],
            }
        )
    raw = canonical_json(
        {"votes_receipt_sha256": votes_receipt, "normalized_votes": normalized}
    ).encode()
    receipt = ledger.commit(
        "IDENTITY_UNBLINDED",
        actor_id=actor_id,
        role="identity_custodian",
        objects={"normalized_votes": (raw, "private")},
        payload={"source_votes_receipt_sha256": votes_receipt},
    )
    return normalized, receipt


def consensus(values: list[str]) -> str:
    counts = {value: values.count(value) for value in ("A", "C", "tie")}
    maximum = max(counts.values())
    winners = [key for key, count in counts.items() if count == maximum]
    return winners[0] if len(winners) == 1 else "tie"


def derive_outcomes(
    *,
    matrix: dict[str, Any],
    assignments: dict[str, Any],
    private_map: dict[str, Any],
    audits: list[NeutralAudit],
    normalized_votes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assignment_by_block = {
        item["block_id"]: item for item in assignments["assignments"]
    }
    audit_by_public = {audit.public_text_id: audit for audit in audits}
    if len(audit_by_public) != len(audits):
        raise ValueError("duplicate audit public id")
    map_by_block: dict[str, dict[str, Any]] = {}
    for row in private_map["rows"]:
        map_by_block.setdefault(row["private_block_id"], {})[row["arm"]] = row
    outcomes = []
    for block in matrix["blocks"]:
        routes = map_by_block[block["block_id"]]
        a_audit = audit_by_public[routes["A"]["public_text_id"]]
        c_audit = audit_by_public[routes["C"]["public_text_id"]]
        a_present = a_audit.artifact_status == "present"
        c_present = c_audit.artifact_status == "present"
        block_votes = [
            item for item in normalized_votes if item["private_block_id"] == block["block_id"]
        ]
        if len(block_votes) != 3:
            raise ValueError("normalized vote count mismatch")
        values = {
            metric: consensus([item[metric] for item in block_votes])
            for metric in ("naturalness", "less_template", "overall_quality")
        }
        if a_present and not c_present:
            values = {key: "A" for key in values}
        elif not a_present and c_present:
            values = {key: "C" for key in values}
        elif not a_present and not c_present:
            values = {key: "no_evidence" for key in values}
        c_checks_pass = c_present and all(item.passed for item in c_audit.hard_checks)
        decision_fidelity = (
            c_present
            and c_audit.observed_decision
            == assignment_by_block[block["block_id"]]["selected_value"]
        )
        outcomes.append(
            {
                "block_id": block["block_id"],
                "scene_id": block["scene_id"],
                "a_status": "present" if a_present else a_audit.artifact_status,
                "c_status": "present" if c_present else c_audit.artifact_status,
                **values,
                "hard_non_degradation": bool(c_checks_pass and decision_fidelity),
            }
        )
    return outcomes


def aggregate_from_ledger(
    ledger: ReceiptLedger,
    *,
    checkpoint_sha256: str,
    actor_id: str,
) -> tuple[dict[str, Any], str]:
    ledger.verify(
        expected_terminal_state="IDENTITY_UNBLINDED",
        checkpoint_sha256=checkpoint_sha256,
    )
    matrix_raw, _, design_receipt = ledger.read_kind("DESIGN_LOCKED", "matrix")
    assignments_raw, _, _ = ledger.read_kind("ASSIGNMENTS_LOCKED", "assignments")
    map_raw, _, _ = ledger.read_kind("ANONYMITY_MAP_LOCKED", "private_map")
    audits_raw, _, audits_receipt = ledger.read_kind("AUDITS_LOCKED", "locked_audits")
    normalized_raw, _, identity_receipt = ledger.read_kind(
        "IDENTITY_UNBLINDED", "normalized_votes"
    )
    matrix = json.loads(matrix_raw)
    audits = [NeutralAudit.model_validate(item) for item in json.loads(audits_raw)]
    normalized_bundle = json.loads(normalized_raw)
    outcomes = derive_outcomes(
        matrix=matrix,
        assignments=json.loads(assignments_raw),
        private_map=json.loads(map_raw),
        audits=audits,
        normalized_votes=normalized_bundle["normalized_votes"],
    )
    aggregate = aggregate_primary(
        matrix=matrix,
        locked_matrix_hash=digest_bytes(matrix_raw),
        outcomes=outcomes,
    )
    bundle = {
        "source_design_receipt_sha256": design_receipt,
        "source_audits_receipt_sha256": audits_receipt,
        "source_identity_receipt_sha256": identity_receipt,
        "outcomes": outcomes,
        "aggregate": aggregate,
    }
    receipt = ledger.commit(
        "AGGREGATED",
        actor_id=actor_id,
        role="aggregator",
        objects={"aggregate": (canonical_json(bundle).encode(), "private")},
        payload={
            "source_design_receipt_sha256": design_receipt,
            "source_audits_receipt_sha256": audits_receipt,
            "source_identity_receipt_sha256": identity_receipt,
        },
    )
    return bundle, receipt
