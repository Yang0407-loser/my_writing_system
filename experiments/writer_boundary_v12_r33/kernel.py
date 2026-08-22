from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from experiments.writer_boundary_v12_r3.kernel import aggregate_primary, digest_bytes, digest_json
from experiments.writer_boundary_v12_r32.kernel import ReceiptLedger, canonical_json, consensus

from .models import ExecutionAudit, PreferenceVote


def _token(entropy: bytes, label: str) -> str:
    return hashlib.sha256(entropy + label.encode()).hexdigest()[:24].upper()


def build_artifact_registry(texts: dict[str, bytes]) -> dict[str, Any]:
    return {
        "schema_version": "1.2-r3.3-artifact-registry",
        "artifacts": [
            {
                "private_text_id": text_id,
                "availability": "present",
                "content_sha256": digest_bytes(raw),
                "bytes": len(raw),
                "availability_source": "TEXTS_LOCKED",
            }
            for text_id, raw in sorted(texts.items())
        ],
    }


def validate_registry_bundle(registry: dict[str, Any], texts: dict[str, bytes]) -> None:
    rows = registry["artifacts"]
    if len(rows) != len(texts) or {row["private_text_id"] for row in rows} != set(texts):
        raise ValueError("artifact registry and bundle id sets differ")
    for row in rows:
        raw = texts[row["private_text_id"]]
        if (
            row["availability"] != "present"
            or row["content_sha256"] != digest_bytes(raw)
            or row["bytes"] != len(raw)
        ):
            raise ValueError("artifact registry does not match locked bytes")


def create_role_separated_map(
    matrix: dict[str, Any],
    registry: dict[str, Any],
    preference_reviewers: list[str],
    *,
    entropy: bytes | None = None,
) -> dict[str, Any]:
    entropy = secrets.token_bytes(32) if entropy is None else entropy
    hashes = {item["private_text_id"]: item["content_sha256"] for item in registry["artifacts"]}
    rows = []
    blocks = []
    for block in matrix["blocks"]:
        local = {}
        for arm in ("A", "B", "C"):
            private_id = block["text_ids"][arm]
            row = {
                "private_block_id": block["block_id"],
                "private_text_id": private_id,
                "scene_id": block["scene_id"],
                "arm": arm,
                "execution_public_text_id": f"EX-{_token(entropy, 'execution:'+private_id)}",
                "content_sha256": hashes[private_id],
            }
            rows.append(row)
            local[arm] = row
        reviewer_maps = {}
        for reviewer in preference_reviewers:
            pair = sorted(
                (local["A"], local["C"]),
                key=lambda item: _token(entropy, reviewer + ":order:" + item["private_text_id"]),
            )
            reviewer_maps[reviewer] = {
                "public_block_id": f"PB-{_token(entropy, reviewer+':block:'+block['block_id'])}",
                "candidate_1_private_text_id": pair[0]["private_text_id"],
                "candidate_1_arm": pair[0]["arm"],
                "candidate_1_public_text_id": f"PR-{_token(entropy, reviewer+':text:'+pair[0]['private_text_id'])}",
                "candidate_2_private_text_id": pair[1]["private_text_id"],
                "candidate_2_arm": pair[1]["arm"],
                "candidate_2_public_text_id": f"PR-{_token(entropy, reviewer+':text:'+pair[1]['private_text_id'])}",
            }
        blocks.append(
            {
                "private_block_id": block["block_id"],
                "scene_id": block["scene_id"],
                "reviewer_maps": reviewer_maps,
            }
        )
    rows.sort(key=lambda item: _token(entropy, "shuffle:" + item["private_text_id"]))
    execution_ids = {row["execution_public_text_id"] for row in rows}
    preference_ids = {
        mapping[key]
        for block in blocks
        for mapping in block["reviewer_maps"].values()
        for key in ("candidate_1_public_text_id", "candidate_2_public_text_id")
    }
    block_ids = {
        mapping["public_block_id"]
        for block in blocks
        for mapping in block["reviewer_maps"].values()
    }
    if (
        len(execution_ids) != 36
        or len(preference_ids) != 24 * len(preference_reviewers)
        or len(block_ids) != 12 * len(preference_reviewers)
        or execution_ids & preference_ids
    ):
        raise ValueError("public ids collide or cross role namespaces")
    return {"rows": rows, "blocks": blocks}


def paragraphize(raw: bytes) -> list[dict[str, str]]:
    return [
        {"paragraph_id": f"P{index}", "text": text.strip()}
        for index, text in enumerate(raw.decode().split("\n\n"), 1)
        if text.strip()
    ]


def _catalog(entries: list[str]) -> list[dict[str, str]]:
    return [
        {"id": entry.split(" ", 1)[0], "definition": entry.split(" ", 1)[1]}
        for entry in entries
    ]


def _violation_catalog(forbidden_events: list[str]) -> dict[str, list[str]]:
    catalog = _catalog(forbidden_events)
    return {
        "unauthorized_new_character": [
            item["id"] for item in catalog
            if any(word in item["definition"] for word in ("第三人", "外部援助", "新人物"))
        ],
        "unauthorized_new_solution": [
            item["id"] for item in catalog
            if any(word in item["definition"] for word in ("白名单外", "新保护方案", "第二"))
        ],
        "unauthorized_relationship_change": [
            item["id"] for item in catalog
            if any(word in item["definition"] for word in ("关系", "承诺", "和解", "责任转移"))
        ],
    }


def execution_dispatch(
    *,
    reviewer_id: str,
    private_map: dict[str, Any],
    texts: dict[str, bytes],
    protocol: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    rubrics = {}
    for scene in protocol["scenes"]:
        rubrics[scene["scene_id"]] = {
            "scene_id": scene["scene_id"],
            "characters": scene["characters"],
            "mandatory_catalog": _catalog(scene["mandatory_events"]),
            "forbidden_catalog": _catalog(scene["forbidden_events"]),
            "violation_catalog": _violation_catalog(scene["forbidden_events"]),
            "allowed_decisions": [
                {"value": item["value"], "definition": item["definition"]}
                for item in scene["decision_contract"]["allowed_values"]
            ],
        }
    core = {
        "schema_version": "1.2-r3.3-execution-dispatch",
        "experiment_id": "writer-boundary-v1-2-r3-3",
        "reviewer_id": reviewer_id,
        "required_item_count": 36,
        "generation_package_authorized": False,
        "model_call_authorized": False,
    }
    dispatch_hash = digest_json(core)
    items = []
    for row in private_map["rows"]:
        raw = texts[row["private_text_id"]]
        items.append(
            {
                "public_text_id": row["execution_public_text_id"],
                "scene_id": row["scene_id"],
                "content_sha256": digest_bytes(raw),
                "paragraphs": paragraphize(raw),
            }
        )
    return {
        **core,
        "dispatch_sha256": dispatch_hash,
        "response_schema": ExecutionAudit.model_json_schema(),
        "rubrics": rubrics,
        "items": items,
    }, dispatch_hash


def preference_dispatches(
    *,
    private_map: dict[str, Any],
    texts: dict[str, bytes],
    reviewers: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    packages, hashes = {}, {}
    for reviewer in reviewers:
        core = {
            "schema_version": "1.2-r3.3-preference-dispatch",
            "experiment_id": "writer-boundary-v1-2-r3-3",
            "reviewer_id": reviewer,
            "required_block_count": 12,
            "generation_package_authorized": False,
            "model_call_authorized": False,
        }
        dispatch_hash = digest_json(core)
        blocks = []
        for block in private_map["blocks"]:
            mapping = block["reviewer_maps"][reviewer]

            def candidate(position: int) -> dict[str, Any]:
                private_id = mapping[f"candidate_{position}_private_text_id"]
                raw = texts[private_id]
                return {
                    "public_text_id": mapping[f"candidate_{position}_public_text_id"],
                    "content_sha256": digest_bytes(raw),
                    "paragraphs": paragraphize(raw),
                }

            blocks.append(
                {
                    "public_block_id": mapping["public_block_id"],
                    "scene_id": block["scene_id"],
                    "candidate_1": candidate(1),
                    "candidate_2": candidate(2),
                }
            )
        packages[reviewer] = {
            **core,
            "dispatch_sha256": dispatch_hash,
            "metric_definitions": {
                "naturalness": "哪篇更自然",
                "less_template": "哪篇更不模板化",
                "overall_quality": "哪篇整体质量更高",
            },
            "response_schema": PreferenceVote.model_json_schema(),
            "blocks": blocks,
        }
        hashes[reviewer] = dispatch_hash
    return packages, hashes


def validate_audits(
    audits: list[ExecutionAudit],
    *,
    package: dict[str, Any],
) -> None:
    if len(audits) != 36 or {audit.public_text_id for audit in audits} != {
        item["public_text_id"] for item in package["items"]
    }:
        raise ValueError("execution audit coverage mismatch")
    item_by_id = {item["public_text_id"]: item for item in package["items"]}
    for audit in audits:
        if audit.reviewer_id != package["reviewer_id"] or audit.dispatch_sha256 != package["dispatch_sha256"]:
            raise ValueError("audit dispatch identity mismatch")
        item = item_by_id[audit.public_text_id]
        if audit.scene_id != item["scene_id"] or audit.content_sha256 != item["content_sha256"]:
            raise ValueError("audit item mismatch")
        rubric = package["rubrics"][audit.scene_id]
        allowed_p = {entry["paragraph_id"] for entry in item["paragraphs"]}
        expected_m = {entry["id"] for entry in rubric["mandatory_catalog"]}
        if {entry.m_id for entry in audit.mandatory_events} != expected_m:
            raise ValueError("mandatory evidence must cover every M exactly once")
        if audit.observed_decision not in {item["value"] for item in rubric["allowed_decisions"]} | {"unclear", "other"}:
            raise ValueError("observed decision invalid")
        for entry in audit.mandatory_events:
            if not set(entry.paragraph_ids) <= allowed_p:
                raise ValueError("mandatory evidence paragraph missing")
        for violation in audit.violations:
            if not set(violation.paragraph_ids) <= allowed_p:
                raise ValueError("violation evidence paragraph missing")
            if not set(violation.f_ids) <= set(rubric["violation_catalog"][violation.check_id]):
                raise ValueError("violation F id has wrong type or scene")


def validate_votes(
    votes: list[PreferenceVote],
    *,
    packages: dict[str, dict[str, Any]],
) -> None:
    reviewers = set(packages)
    if {vote.reviewer_id for vote in votes} != reviewers or len(votes) != 36:
        raise ValueError("preference reviewer roster mismatch")
    for reviewer, package in packages.items():
        subset = [vote for vote in votes if vote.reviewer_id == reviewer]
        blocks = {block["public_block_id"]: block for block in package["blocks"]}
        if len(subset) != 12 or {vote.public_block_id for vote in subset} != set(blocks):
            raise ValueError("reviewer block coverage mismatch")
        for vote in subset:
            if vote.dispatch_sha256 != package["dispatch_sha256"]:
                raise ValueError("vote dispatch mismatch")
            block = blocks[vote.public_block_id]
            if (
                vote.candidate_1_id,
                vote.candidate_2_id,
                vote.candidate_1_content_sha256,
                vote.candidate_2_content_sha256,
            ) != (
                block["candidate_1"]["public_text_id"],
                block["candidate_2"]["public_text_id"],
                block["candidate_1"]["content_sha256"],
                block["candidate_2"]["content_sha256"],
            ):
                raise ValueError("vote candidate mismatch")


def unblind_votes(
    votes: list[PreferenceVote],
    *,
    packages: dict[str, dict[str, Any]],
    private_map: dict[str, Any],
) -> list[dict[str, Any]]:
    validate_votes(votes, packages=packages)
    lookup = {}
    for block in private_map["blocks"]:
        for reviewer, mapping in block["reviewer_maps"].items():
            lookup[(reviewer, mapping["public_block_id"])] = (block, mapping)
    normalized = []
    for vote in votes:
        block, mapping = lookup[(vote.reviewer_id, vote.public_block_id)]
        arm = {
            "candidate_1": mapping["candidate_1_arm"],
            "candidate_2": mapping["candidate_2_arm"],
            "tie": "tie",
        }
        normalized.append(
            {
                "reviewer_id": vote.reviewer_id,
                "private_block_id": block["private_block_id"],
                "scene_id": block["scene_id"],
                "naturalness": arm[vote.naturalness],
                "less_template": arm[vote.less_template],
                "overall_quality": arm[vote.overall_quality],
            }
        )
    return normalized


def derive_and_aggregate(
    *,
    matrix: dict[str, Any],
    assignments: dict[str, Any],
    registry: dict[str, Any],
    private_map: dict[str, Any],
    audits: list[ExecutionAudit],
    normalized_votes: list[dict[str, Any]],
    reviewer_roster: list[str],
) -> dict[str, Any]:
    expected_vote_keys = {
        (reviewer, block["block_id"])
        for reviewer in reviewer_roster
        for block in matrix["blocks"]
    }
    actual_vote_keys = {
        (item["reviewer_id"], item["private_block_id"]) for item in normalized_votes
    }
    if len(normalized_votes) != 36 or actual_vote_keys != expected_vote_keys:
        raise ValueError("normalized votes are not exact roster x block product")
    artifact_by_id = {item["private_text_id"]: item for item in registry["artifacts"]}
    audit_by_execution_id = {audit.public_text_id: audit for audit in audits}
    assignment_by_block = {item["block_id"]: item for item in assignments["assignments"]}
    rows_by_block: dict[str, dict[str, Any]] = {}
    for row in private_map["rows"]:
        rows_by_block.setdefault(row["private_block_id"], {})[row["arm"]] = row
    outcomes = []
    for block in matrix["blocks"]:
        routes = rows_by_block[block["block_id"]]
        a_row, c_row = routes["A"], routes["C"]
        a_status = artifact_by_id[a_row["private_text_id"]]["availability"]
        c_status = artifact_by_id[c_row["private_text_id"]]["availability"]
        c_audit = audit_by_execution_id[c_row["execution_public_text_id"]]
        block_votes = [
            item for item in normalized_votes if item["private_block_id"] == block["block_id"]
        ]
        metrics = {
            metric: consensus([item[metric] for item in block_votes])
            for metric in ("naturalness", "less_template", "overall_quality")
        }
        if a_status == "present" and c_status != "present":
            metrics = {key: "A" for key in metrics}
        elif a_status != "present" and c_status == "present":
            metrics = {key: "C" for key in metrics}
        elif a_status != "present" and c_status != "present":
            metrics = {key: "no_evidence" for key in metrics}
        hard = (
            c_status == "present"
            and all(item.passed for item in c_audit.mandatory_events)
            and not any(item.detected for item in c_audit.violations)
            and c_audit.observed_decision == assignment_by_block[block["block_id"]]["selected_value"]
        )
        outcomes.append(
            {
                "block_id": block["block_id"],
                "scene_id": block["scene_id"],
                "a_status": a_status,
                "c_status": c_status,
                **metrics,
                "hard_non_degradation": hard,
            }
        )
    return {
        "outcomes": outcomes,
        "aggregate": aggregate_primary(
            matrix=matrix,
            locked_matrix_hash=digest_json(matrix),
            outcomes=outcomes,
        ),
    }

