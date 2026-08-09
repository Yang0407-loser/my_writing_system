from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / ".world_runtime_writer_canary_v2_runtime"
SOURCE_FIXTURE = ROOT / "experiments/world_runtime_writer_canary/fixtures/canary_v1.json"
CONTRACT_VERSION = "world-runtime-evaluation-v2-posthoc-diagnostic-v1"

Judgment = Literal["pass", "fail", "unresolved"]
SettingCategory = Literal[
    "realization_detail",
    "new_event",
    "new_relationship",
    "new_project_fact",
    "state_change",
    "world_rule_change",
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceSpan(FrozenModel):
    claim: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_span(self):
        if self.end <= self.start:
            raise ValueError("evidence end must follow start")
        return self


class DimensionJudgment(FrozenModel):
    outcome: Judgment
    reason_code: str = Field(min_length=1)
    basis: Literal["evidence", "full_text_absence"] = "evidence"
    evidence: tuple[EvidenceSpan, ...] = ()

    @model_validator(mode="after")
    def require_positive_evidence(self):
        if self.outcome == "pass" and self.basis == "evidence" and not self.evidence:
            raise ValueError("passing judgment requires evidence")
        if self.basis == "full_text_absence" and self.evidence:
            raise ValueError("absence-based judgment cannot carry a positive span")
        return self


class EventEvaluationV2(FrozenModel):
    event_id: str = Field(min_length=1)
    required_outcome: DimensionJudgment
    required_bridge: DimensionJudgment
    evidence_sufficiency: DimensionJudgment
    illegal_transition: DimensionJudgment
    strict_pass: bool

    @model_validator(mode="after")
    def derive_strict_pass(self):
        expected = (
            self.required_outcome.outcome == "pass"
            and self.required_bridge.outcome == "pass"
            and self.evidence_sufficiency.outcome == "pass"
            and self.illegal_transition.outcome == "pass"
        )
        if self.strict_pass != expected:
            raise ValueError("strict_pass must be derived from all four dimensions")
        return self


class RealityEvaluationV2(FrozenModel):
    check_id: str = Field(min_length=1)
    outcome: Judgment
    reason_code: str = Field(min_length=1)
    evidence: tuple[EvidenceSpan, ...] = ()


class SettingCandidateV2(FrozenModel):
    candidate_id: str = Field(min_length=1)
    category: SettingCategory
    review_required: bool
    reason_code: str = Field(min_length=1)
    evidence: EvidenceSpan


class TextEvaluationV2(FrozenModel):
    sample_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    arm: Literal["A", "B"]
    repeat: int = Field(ge=1)
    output_sha256: str = Field(min_length=64, max_length=64)
    visible_characters: int = Field(ge=0)
    events: tuple[EventEvaluationV2, ...] = Field(min_length=1)
    reality_checks: tuple[RealityEvaluationV2, ...] = Field(min_length=1)
    setting_candidates: tuple[SettingCandidateV2, ...] = ()
    field_leakage_detected: bool
    schema_version: str = CONTRACT_VERSION


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _span(text: str, match: re.Match[str], claim: str) -> EvidenceSpan:
    return EvidenceSpan(
        claim=claim,
        start=match.start(),
        end=match.end(),
        excerpt=text[match.start():match.end()],
    )


def _find(text: str, pattern: str, claim: str, *, flags: int = 0):
    match = re.search(pattern, text, flags)
    return _span(text, match, claim) if match else None


def _find_after(
    text: str, pattern: str, claim: str, *, start: int, flags: int = 0
):
    match = re.search(pattern, text[start:], flags)
    if not match:
        return None
    absolute = re.compile(pattern, flags).search(text, start)
    return _span(text, absolute, claim)


def _judgment(
    outcome: Judgment,
    code: str,
    *evidence,
    basis: Literal["evidence", "full_text_absence"] = "evidence",
) -> DimensionJudgment:
    return DimensionJudgment(
        outcome=outcome,
        reason_code=code,
        basis=basis,
        evidence=tuple(item for item in evidence if item is not None),
    )


def _event(
    event_id: str,
    outcome: DimensionJudgment,
    bridge: DimensionJudgment,
    sufficiency: DimensionJudgment,
    illegal: DimensionJudgment,
) -> EventEvaluationV2:
    strict = all(
        item.outcome == "pass" for item in (outcome, bridge, sufficiency, illegal)
    )
    return EventEvaluationV2(
        event_id=event_id,
        required_outcome=outcome,
        required_bridge=bridge,
        evidence_sufficiency=sufficiency,
        illegal_transition=illegal,
        strict_pass=strict,
    )


def _enter_workshop(text: str) -> EventEvaluationV2:
    door = _find(
        text,
        r"(?:周野.{0,30}(?:(?:打开|推开|拉开).{0,10}(?:侧门|操作间.{0,3}门)|(?:侧门|门).{0,4}(?:打开|推开|拉开))|他.{0,20}(?:开锁|钥匙.{0,8}锁孔))",
        "actor opens workshop access",
        flags=re.S,
    )
    entered = _find(
        text,
        r"(?:让|示意|领着).{0,8}林晚.{0,10}(?:进|进去|进入)|林晚.{0,60}(?:进|走进|进入).{0,10}操作间|周野.{0,40}让她进来",
        "Lin Wan enters workshop",
        flags=re.S,
    )
    outcome = _judgment(
        "pass" if entered else "unresolved",
        "explicit_entry" if entered else "entry_not_evidenced",
        entered,
    )
    bridge = _judgment(
        "pass" if door else "unresolved",
        "actor_opened_access" if door else "access_bridge_not_evidenced",
        door,
    )
    sufficiency = _judgment(
        "pass" if entered and door else "unresolved",
        "entry_and_access_spans" if entered and door else "entry_evidence_incomplete",
        door,
        entered,
    )
    illegal = _judgment(
        "pass" if door else "unresolved",
        "no_unexplained_access_jump" if door else "cannot_rule_out_access_jump",
        door,
    )
    return _event("event:enter-workshop", outcome, bridge, sufficiency, illegal)


def _publish_article(text: str) -> EventEvaluationV2:
    published = _find(
        text,
        r"(?:已发布|正式发布|文章.{0,8}(?:发布|上线)|链接生成)",
        "article reaches published state",
        flags=re.S,
    )
    submitted = _find(
        text,
        r"(?:提交审核|点击.{0,3}提交|点下.{0,3}提交|点.{0,3}提交|按下.{0,3}提交|已提交|待审核|审核通过)",
        "submission or review bridge occurs",
        flags=re.S,
    )
    outcome = _judgment(
        "pass" if published else "unresolved",
        "published_state_evidenced" if published else "publication_not_evidenced",
        published,
    )
    bridge_outcome: Judgment = "pass" if submitted else (
        "fail" if published else "unresolved"
    )
    bridge = _judgment(
        bridge_outcome,
        "submit_bridge_evidenced" if submitted else (
            "published_without_submit_bridge" if published else "submit_bridge_not_evidenced"
        ),
        submitted,
        published if published and not submitted else None,
    )
    enough = bool(published and submitted)
    sufficiency = _judgment(
        "pass" if enough else "unresolved",
        "publication_path_has_two_spans" if enough else "publication_path_evidence_incomplete",
        submitted,
        published,
    )
    illegal = _judgment(
        "pass" if enough else ("fail" if published else "unresolved"),
        "declared_publication_path_preserved" if enough else (
            "draft_to_published_bridge_omitted" if published else "publication_path_unknown"
        ),
        submitted,
        published,
    )
    return _event("event:publish-article", outcome, bridge, sufficiency, illegal)


def _share_with_jiqing(text: str) -> EventEvaluationV2:
    published = _find(text, r"(?:已发布|正式发布|链接生成)", "content is available")
    link = _find(text, r"(?:复制|附上|粘贴|贴上).{0,8}(?:文章)?链接|链接.{0,10}(?:发送|发了|发过去|贴|粘贴)", "link prepared or sent", flags=re.S)
    send = _find(text, r"链接.{0,50}(?:发了过去|发过去|发给|发送|按了发送|点了发送)|(?:发了|发送).{0,12}链接", "link reaches recipient channel", flags=re.S)
    jiqing = _find(text, r"季晴", "recipient named")
    perceived = _find(
        text,
        r"(?:季晴.{0,100}(?:读完了|看完了|读到了|看完|读完)|(?:读完了|看完了|读到了).{0,60}季晴|[“\"](?:读到了|看完了|读完了)[。！？”\"]|你写的是我们吧)",
        "recipient explicitly perceives article",
        flags=re.S,
    )
    reached = bool(link and send and jiqing)
    availability_before_send = bool(published and send and published.start < send.start)
    outcome = _judgment(
        "pass" if perceived else "unresolved",
        "recipient_read_evidenced" if perceived else "recipient_read_not_evidenced",
        perceived,
    )
    bridge_ok = availability_before_send and reached
    bridge = _judgment(
        "pass" if bridge_ok else ("fail" if perceived else "unresolved"),
        "available_then_reached" if bridge_ok else (
            "perception_without_complete_delivery_path" if perceived else "delivery_path_incomplete"
        ),
        published,
        link,
        send,
        jiqing,
    )
    enough = bool(perceived and bridge_ok)
    sufficiency = _judgment(
        "pass" if enough else "unresolved",
        "availability_reach_perception_spans" if enough else "knowledge_path_evidence_incomplete",
        published,
        send,
        perceived,
    )
    illegal = _judgment(
        "pass" if enough else ("fail" if perceived and not bridge_ok else "unresolved"),
        "knowledge_path_order_preserved" if enough else (
            "perception_precedes_delivery" if perceived and not bridge_ok else "knowledge_transition_unresolved"
        ),
        published,
        send,
        perceived,
    )
    return _event("event:share-with-jiqing", outcome, bridge, sufficiency, illegal)


def _deliver_resignation(text: str) -> EventEvaluationV2:
    channel = _find(
        text,
        r"(?:公司)?人事(?:部)?(?:的)?(?:邮箱|地址)|HR.{0,4}(?:邮箱|地址)",
        "institutional HR channel named",
        flags=re.I,
    )
    sent = (
        _find_after(
            text,
            r"(?:系统提示|页面|邮件|状态).{0,12}(?:发送成功|已发送|邮件已发送)|(?:点|按).{0,4}(?:发送)",
            "resignation message sent",
            start=channel.end,
            flags=re.S,
        )
        if channel
        else _find(
            text,
            r"(?:系统提示|页面|邮件|状态).{0,12}(?:发送成功|已发送|邮件已发送)|(?:点|按).{0,4}(?:发送)",
            "resignation message sent",
            flags=re.S,
        )
    )
    delivered = bool(channel and sent and channel.start <= sent.end)
    outcome = _judgment(
        "pass" if delivered else "unresolved",
        "notice_sent_to_hr_channel" if delivered else "institutional_delivery_not_evidenced",
        channel,
        sent,
    )
    bridge = _judgment(
        "pass" if channel else "fail" if sent else "unresolved",
        "institutional_delivery_path" if channel else (
            "send_without_institutional_channel" if sent else "delivery_path_unknown"
        ),
        channel,
        sent if sent and not channel else None,
    )
    sufficiency = _judgment(
        "pass" if delivered else "unresolved",
        "channel_and_send_spans" if delivered else "resignation_evidence_incomplete",
        channel,
        sent,
    )
    ended = _find(text, r"(?:辞职|离职)(?:已经)?生效|劳动关系(?:已经)?终止|已经离职", "employment treated as ended")
    acknowledged = _find(text, r"(?:公司|人事).{0,10}(?:确认|批准|同意)|生效日期(?:已经)?到", "effective transition prerequisite")
    illegal_fail = bool(ended and not acknowledged)
    illegal = (
        _judgment(
            "fail",
            "employment_ended_without_prerequisite",
            ended,
        )
        if illegal_fail
        else _judgment(
            "pass",
            "no_termination_jump",
            basis="full_text_absence",
        )
    )
    return _event("event:deliver-resignation", outcome, bridge, sufficiency, illegal)


def _reality_checks(text: str) -> tuple[RealityEvaluationV2, ...]:
    storefront = _find(text, r"(?:开门营业|开始营业|店面开门|店门打开|顾客.{0,4}(?:进店|走进)|招呼顾客)", "public storefront opens before 06:00", flags=re.S)
    publish_positions = [text.find(term) for term in ("已发布", "正式发布", "上线") if term in text]
    first_publish = min(publish_positions) if publish_positions else len(text) + 1
    comment = _find(text[:first_publish], r"(?:出现|收到|有了).{0,5}评论|评论区.{0,5}(?:出现|有)", "public reaction precedes publication", flags=re.S)
    ended = _find(text, r"(?:辞职|离职)(?:已经)?生效|劳动关系(?:已经)?终止|已经离职", "employment ended")
    acknowledged = _find(text, r"(?:公司|人事).{0,10}(?:确认|批准|同意)|生效日期(?:已经)?到", "termination prerequisite")
    coworker = _find(text, r"(?:内网|同事评论|同事们都|同事已经)", "coworker knowledge without shown path")
    values = (
        ("storefront_open_before_0600", storefront),
        ("public_comment_before_publication", comment),
        ("employment_ended_without_effective_transition", ended if ended and not acknowledged else None),
        ("coworker_knows_without_transmission_path", coworker),
    )
    return tuple(
        RealityEvaluationV2(
            check_id=check_id,
            outcome="fail" if evidence else "pass",
            reason_code="violation_evidenced" if evidence else "no_violation_evidence",
            evidence=(evidence,) if evidence else (),
        )
        for check_id, evidence in values
    )


SETTING_PATTERNS: tuple[tuple[str, SettingCategory, bool, str], ...] = (
    (r"季晴是她的编辑", "new_relationship", True, "unsourced_editor_relationship"),
    (r"(?:连锁面包店|过期原料|重新贴标|监控死角|主管.{0,12}别多事)", "new_project_fact", True, "specific_article_investigation_added"),
    (r"最后工作日", "state_change", True, "resignation_effective_date_added"),
    (r"申请于即日解除劳动合同", "state_change", True, "immediate_termination_requested"),
    (r"(?:证据.{0,4}文件夹|删掉.{0,8}草稿箱残留)", "realization_detail", False, "local_interface_detail"),
)


def _setting_candidates(text: str, sample_id: str) -> tuple[SettingCandidateV2, ...]:
    values = []
    ordinal = 0
    for pattern, category, review_required, reason in SETTING_PATTERNS:
        for match in re.finditer(pattern, text, re.S):
            ordinal += 1
            evidence = _span(text, match, reason)
            values.append(
                SettingCandidateV2(
                    candidate_id=f"{sample_id}:setting:{ordinal}",
                    category=category,
                    review_required=review_required,
                    reason_code=reason,
                    evidence=evidence,
                )
            )
    return tuple(values)


def evaluate_text(
    *, sample_id: str, scene_id: str, arm: Literal["A", "B"], repeat: int, text: str
) -> TextEvaluationV2:
    events = (
        _enter_workshop(text),
        _publish_article(text),
        _share_with_jiqing(text),
        _deliver_resignation(text),
    )
    leakage = any(
        term in text for term in (
            "状态锚点", "因果边界", "合法路径", "必写事件",
            "state_revision", "event:publish-article",
        )
    )
    return TextEvaluationV2(
        sample_id=sample_id,
        scene_id=scene_id,
        arm=arm,
        repeat=repeat,
        output_sha256=_hash_bytes(text.encode("utf-8")),
        visible_characters=len(re.sub(r"\s+", "", text)),
        events=events,
        reality_checks=_reality_checks(text),
        setting_candidates=_setting_candidates(text, sample_id),
        field_leakage_detected=leakage,
    )


def _aggregate(items: tuple[TextEvaluationV2, ...]):
    result = {}
    for arm in ("A", "B"):
        arm_items = [item for item in items if item.arm == arm]
        events = [event for item in arm_items for event in item.events]
        result[arm] = {
            "samples": len(arm_items),
            "strict_event_passes": sum(event.strict_pass for event in events),
            "strict_sample_passes": sum(all(event.strict_pass for event in item.events) for item in arm_items),
            "outcome_passes": sum(event.required_outcome.outcome == "pass" for event in events),
            "bridge_passes": sum(event.required_bridge.outcome == "pass" for event in events),
            "evidence_sufficiency_passes": sum(event.evidence_sufficiency.outcome == "pass" for event in events),
            "illegal_transition_failures": sum(event.illegal_transition.outcome == "fail" for event in events),
            "reality_failures": sum(check.outcome == "fail" for item in arm_items for check in item.reality_checks),
            "review_required_setting_candidates": sum(candidate.review_required for item in arm_items for candidate in item.setting_candidates),
            "field_leakage_count": sum(item.field_leakage_detected for item in arm_items),
        }
    return result


def evaluate_runtime(runtime_dir: Path = DEFAULT_RUNTIME):
    manifest = _read(runtime_dir / "private/locked-manifest.json")
    ledger = _read(runtime_dir / "attempt-ledger.json")
    values = []
    integrity_errors = []
    for sample in manifest["samples"]:
        sample_id = sample["sample_id"]
        entry = ledger["samples"][sample_id]
        if entry["status"] != "succeeded" or entry["attempt_count"] != 1:
            integrity_errors.append(f"{sample_id}:ledger_not_single_success")
            continue
        path = runtime_dir / "private/outputs" / f"{sample_id}.txt"
        raw = path.read_bytes()
        if _hash_bytes(raw) != entry.get("output_sha256"):
            integrity_errors.append(f"{sample_id}:output_hash_mismatch")
            continue
        values.append(
            evaluate_text(
                sample_id=sample_id,
                scene_id=sample["scene_id"],
                arm=sample["arm"],
                repeat=sample["repeat"],
                text=raw.decode("utf-8"),
            )
        )
    if integrity_errors:
        raise ValueError(";".join(integrity_errors))
    items = tuple(values)
    result = {
        "schema_version": CONTRACT_VERSION,
        "evaluation_role": "posthoc_diagnostic_not_promotion_evidence",
        "source_evaluation": "world-runtime-writer-canary-evaluation-v1",
        "source_outputs": len(items),
        "items": [item.model_dump(mode="json") for item in items],
        "aggregate": _aggregate(items),
        "promotion_eligible": False,
        "decision": "hold_repair_measurement_then_human_blind_review",
        "limitations": [
            "contract_authored_after_outputs_were_available",
            "deterministic_patterns_cannot_replace_human_semantic_review",
            "absence_of_evidence_is_unresolved_except_declared_bridge_omission",
            "setting_candidates_require_human_classification",
        ],
    }
    _write(runtime_dir / "evaluation-v2.json", result)
    return result


def build_blind_review_package(runtime_dir: Path = DEFAULT_RUNTIME):
    evaluation = _read(runtime_dir / "evaluation-v2.json")
    manifest = _read(runtime_dir / "private/locked-manifest.json")
    source_fixture = _read(SOURCE_FIXTURE)
    scenes = {item["scene_id"]: item for item in source_fixture["scenes"]}
    samples = {item["sample_id"]: item for item in manifest["samples"]}
    ids = sorted(samples)
    random.Random(2026080302).shuffle(ids)
    candidates = []
    private_key = {}
    for ordinal, sample_id in enumerate(ids, 1):
        candidate_id = f"WR1M-C{ordinal:02d}"
        sample = samples[sample_id]
        text = (runtime_dir / "private/outputs" / f"{sample_id}.txt").read_text(encoding="utf-8")
        candidates.append(
            {
                "candidate_id": candidate_id,
                "scene_id": sample["scene_id"],
                "scene_context": {
                    "premise": scenes[sample["scene_id"]]["premise"],
                    "characters": scenes[sample["scene_id"]]["characters"],
                },
                "text": text,
                "review_fields": {
                    "world_consistency": None,
                    "required_outcome_complete": None,
                    "required_bridge_complete": None,
                    "evidence_sufficient": None,
                    "unsourced_setting_severity": None,
                    "prose_naturalness": None,
                    "instructional_feel": None,
                    "notes": "",
                },
            }
        )
        private_key[candidate_id] = {
            "sample_id": sample_id,
            "arm": sample["arm"],
            "repeat": sample["repeat"],
            "request_hash": sample["request_hash"],
        }
    package = {
        "schema_version": "world-runtime-human-review-package-v2",
        "evaluation_hash": hashlib.sha256(
            json.dumps(evaluation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "candidate_count": len(candidates),
        "arms_hidden": True,
        "review_contract": {
            "required_events": [
                "周野提供内部通道，林晚实际进入操作间；不得把临街店面对外营业当作进入桥接。",
                "文章先提交，再达到已发布状态。",
                "已发布内容通过链接到达季晴，并有足够正文证据表明她实际阅读。",
                "辞职通知发送至公司人事通道；发送本身不等于离职已经生效。",
            ],
            "scales": {
                "world_consistency": "1..5，越高越一致",
                "required_outcome_complete": "0..2：缺失/含混/完整",
                "required_bridge_complete": "0..2：缺失/含混/完整",
                "evidence_sufficient": "0..2：不足/含混/充分",
                "unsourced_setting_severity": "0..3：无/仅实现细节/重要新事实/规则关系状态改变",
                "prose_naturalness": "1..5，越高越自然",
                "instructional_feel": "1..5，越高越像执行清单或说明书",
            },
        },
        "instructions": [
            "Review prose only; do not infer arm or inspect the private key.",
            "Score each field before comparing candidates from the same scene.",
            "Treat plausible realization detail separately from new relationship, project fact, state, or world rule.",
            "Do not edit source prose or machine evaluation.",
        ],
        "candidates": candidates,
    }
    _write(runtime_dir / "private/human-review-v2.json", package)
    _write(runtime_dir / "private/human-review-v2-key.json", private_key)
    return {
        "schema_version": "world-runtime-human-review-package-receipt-v2",
        "candidate_count": len(candidates),
        "arms_hidden": True,
        "package_sha256": _hash_bytes((runtime_dir / "private/human-review-v2.json").read_bytes()),
        "key_sha256": _hash_bytes((runtime_dir / "private/human-review-v2-key.json").read_bytes()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("evaluate", "build-review"))
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()
    result = evaluate_runtime(args.runtime) if args.command == "evaluate" else build_blind_review_package(args.runtime)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
