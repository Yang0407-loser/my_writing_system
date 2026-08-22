"""Evidence-producing WR1 evaluator V2 and isolated micro-benchmark.

V2 evaluates four separate dimensions and always returns exact source spans for
positive claims.  It is an offline diagnostic component: passing its synthetic
holdout does not authorize a generation run or production integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr1e_evaluator_calibration_v1.json"
HOLDOUT = ROOT / "experiments/world_runtime_writer_canary/fixtures/wr1e_evaluator_holdout_v1.json"
DEFAULT_REPORT = ROOT / "reports/world-runtime-wr1e-evaluator-v2-holdout-2026-08-04.json"
SCENE_CHECKS = {
    "adversarial-storefront-hours": ("storefront_public_open_before_0600",),
    "adversarial-unpublished-knowledge": (
        "public_reaction_before_publication",
        "coworker_knows_without_transmission_path",
    ),
    "adversarial-object-and-repeat": (
        "object_changes_without_actor",
        "completed_event_repeated",
    ),
    "adversarial-employment-transition": (
        "employment_ended_without_acknowledgement",
    ),
}


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Span(FrozenModel):
    claim: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("span end must follow start")
        return self


class BinaryResult(FrozenModel):
    value: bool
    reason_code: str = Field(min_length=1)
    basis: Literal["evidence", "counterevidence", "full_text_absence"]
    evidence: tuple[Span, ...] = ()

    @model_validator(mode="after")
    def evidence_rule(self):
        if self.value and (self.basis != "evidence" or not self.evidence):
            raise ValueError("positive result requires exact evidence")
        if self.basis == "full_text_absence" and self.evidence:
            raise ValueError("absence result cannot contain evidence")
        return self


class EvaluationV2(FrozenModel):
    scene_id: str
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_event_completed: BinaryResult
    hard_reality_violations: dict[str, BinaryResult]
    task_evasion: BinaryResult
    unsourced_setting: BinaryResult
    schema_version: Literal["world-runtime-evaluator-v2-wr1e"] = (
        "world-runtime-evaluator-v2-wr1e"
    )


def _span(text: str, match: re.Match[str], claim: str) -> Span:
    return Span(
        claim=claim,
        start=match.start(),
        end=match.end(),
        excerpt=text[match.start():match.end()],
    )


def _first(text: str, patterns: tuple[str, ...], claim: str) -> Span | None:
    matches = [re.search(pattern, text, re.S) for pattern in patterns]
    matches = [match for match in matches if match]
    return _span(text, min(matches, key=lambda item: item.start()), claim) if matches else None


def _result(value: bool, code: str, *spans: Span | None) -> BinaryResult:
    evidence = tuple(span for span in spans if span is not None)
    return BinaryResult(
        value=value,
        reason_code=code,
        basis="evidence" if value else ("counterevidence" if evidence else "full_text_absence"),
        evidence=evidence,
    )


def _storefront(text: str):
    entry = _first(
        text,
        (
            r"林晚.{0,24}(?:进入|走进|进了|进到).{0,8}操作间",
            r"林晚.{0,20}(?:推开|打开).{0,8}操作间.{0,3}门",
            r"林晚.{0,28}侧面的小门.{0,24}操作间里",
        ),
        "Lin Wan enters the workshop",
    )
    visitor = _first(
        text,
        (
            r"(?:钱|纸币).{0,12}(?:退回|推回|拿回|被抽走)",
            r"(?:退回|推回|拿回).{0,8}(?:钱|纸币)",
            r"六点.{0,12}(?:再来|开门|营业|留着)",
            r"(?:趁热装|装了|递出|交给|纸袋放).{0,18}(?:两个|面包|可颂|门外)",
            r"(?:拒绝|不卖|不能卖).{0,12}(?:路人|来客|顾客|面包)?",
        ),
        "visitor request receives an outcome",
    )
    event = _result(bool(entry and visitor), "entry_and_visitor_outcome" if entry and visitor else "required_component_missing", entry, visitor)

    payment = _first(
        text,
        (
            r"(?:收下|接过|留下).{0,8}(?:现金|钱|纸币)",
            r"(?:现金|钱|纸币).{0,24}(?:零钱铁盒|钱箱|收银台)",
            r"两张十元.{0,30}(?:零钱|铁盒)",
        ),
        "payment is accepted or retained",
    )
    goods = _first(
        text,
        (
            r"趁热装两个",
            r"(?:递出|交给).{0,12}(?:面包|可颂|纸袋)",
            r"(?:纸袋|面包|可颂).{0,16}(?:放在门外|交到|递给)",
            r"把纸袋放在门外",
        ),
        "goods are delivered",
    )
    public_entry = _first(
        text,
        (r"(?:卷帘门|临街门|店门).{0,15}(?:打开|拉开|升起).{0,20}(?:顾客|来客|路人).{0,10}(?:进|买)",),
        "public storefront opens for a customer",
    )
    violation_value = bool((payment and goods) or public_entry)
    violation = _result(
        violation_value,
        "preopening_sale_or_public_entry" if violation_value else "no_preopening_public_transaction",
        payment,
        goods,
        public_entry,
    )
    defer = _first(text, (r"(?:没有处理|不作回应|略过).{0,12}(?:敲门|来客|要求)", r"小节到此结束"), "required visitor outcome is avoided")
    evasion = _result(not event.value and bool(defer), "visitor_outcome_avoided" if defer else "no_task_evasion", defer)
    return event, {"storefront_public_open_before_0600": violation}, evasion


def _knowledge(text: str):
    draft = _first(text, (r"草稿", r"文章", r"文档", r"全文"), "draft or article is present")
    jiqing = _first(text, (r"季晴",), "Ji Qing channel is present")
    response = _first(
        text,
        (
            r"(?:同事|工作群|公司群|陈晨|周哲).{0,35}(?:指出|回复|说|写道|看完).{0,35}(?:错字|正文里|文章里|你写的是|结尾|第.{0,3}段|那个阿姨|那条线)",
            r"我刚看完.{0,16}全文.{0,40}(?:你写的是|我见过|第.{0,3}段|错字)",
            r"我刚看完季晴转给我的全文.{0,50}(?:你写的是|我见过|第.{0,3}段|错字)",
        ),
        "coworker gives a body-specific response",
    )
    event = _result(bool(draft and jiqing and response), "two_channels_and_body_response" if draft and jiqing and response else "body_response_missing", draft, jiqing, response)

    reaction = _first(
        text,
        (
            r"评论区.{0,10}(?:已经|出现|有了|冒出)",
            r"(?:阅读量|转发数|点赞数).{0,8}(?:已经|涨到|增加|跳到)",
            r"收到.{0,6}(?:点赞|评论|转发)",
        ),
        "public reaction exists",
    )
    published = _first(text, (r"已发布", r"正式发布", r"审核通过", r"文章上线"), "publication occurs")
    public_violation = bool(reaction and (not published or reaction.start < published.start))
    public_result = _result(public_violation, "reaction_precedes_publication" if public_violation else "no_public_reaction_before_publication", reaction if public_violation else published)

    path = _first(
        text,
        (
            r"(?:发到|贴到|上传到).{0,12}(?:工作群|公司群)",
            r"把.{0,20}(?:正文|文章|草稿|段落).{0,12}发.{0,8}(?:工作群|公司群|同事)",
            r"(?:季晴转给|问季晴要|季晴发给)",
            r"朋友圈.{0,20}(?:截图|长图)|发过.{0,12}(?:截图|长图)",
        ),
        "transmission path is explicit",
    )
    no_path = bool(response and not path)
    path_result = _result(no_path, "body_known_without_path" if no_path else "path_present_or_no_body_knowledge", response if no_path else path)

    defer = _first(
        text,
        (r"写完了?发我看看", r"以后再发", r"只看.{0,8}(?:标题|截图|片段)", r"群里没有人回应正文"),
        "body response is deferred or replaced",
    )
    evasion = _result(not event.value and bool(defer), "body_response_avoided" if defer else "no_task_evasion", defer)
    return event, {
        "public_reaction_before_publication": public_result,
        "coworker_knows_without_transmission_path": path_result,
    }, evasion


def _object_repeat(text: str):
    wait = _first(text, (r"等待", r"等着", r"消息", r"回复"), "waiting aftermath is present")
    status = _first(text, (r"已发布", r"发布状态", r"已送达", r"发件箱", r"打开后台", r"确认"), "completed statuses are checked")
    bowl = _first(text, (r"绿豆汤", r"汤碗", r"碗"), "home bowl cutaway is present")
    event = _result(bool(wait and status and bowl), "aftermath_and_bowl_cutaway" if wait and status and bowl else "aftermath_component_missing", wait, status, bowl)

    changed = _first(text, (r"(?:空碗|碗空了|碗里空了|洗干净|汤没了|只剩空碗)",), "bowl state changes")
    actor = _first(text, (r"(?:她|林晚|周野|有人).{0,12}(?:喝|倒|洗|收|拿走|端走|打翻).{0,10}(?:汤|碗)", r"(?:喝|倒|洗|收|拿走|端走|打翻).{0,12}(?:绿豆汤|汤碗|碗)"), "actor causes bowl change")
    object_violation = bool(changed and not actor)
    object_result = _result(object_violation, "object_changed_without_actor" if object_violation else "object_unchanged_or_actor_present", changed if object_violation else actor)

    repeated = _first(text, (r"(?:再次|重新|又).{0,12}(?:提交|发布|发送辞职|发出辞职)", r"又点下发布", r"重新发送.{0,8}辞职"), "completed event is repeated")
    repeat_result = _result(bool(repeated), "completed_event_repeated" if repeated else "no_completed_event_repeat", repeated)
    defer = _first(text, (r"(?:略过|没有切回|不再写).{0,12}(?:住处|家中|碗)", r"到此结束"), "required home cutaway is avoided")
    evasion = _result(not event.value and bool(defer), "home_cutaway_avoided" if defer else "no_task_evasion", defer)
    return event, {
        "object_changes_without_actor": object_result,
        "completed_event_repeated": repeat_result,
    }, evasion


def _employment(text: str):
    ack_unknown = _first(
        text,
        (
            r"(?:人事|公司).{0,12}(?:没有|没|尚未|暂时没有|依然没有).{0,10}(?:回复|确认|回音)",
            r"(?:没有|没|尚未|还没)收到.{0,8}(?:回复|确认)",
            r"(?:人事|公司).{0,8}(?:回复|确认).{0,8}(?:没有来|未知)",
            r"还在等.{0,8}(?:答复|回复|确认)",
            r"仍然在职",
        ),
        "acknowledgement remains absent or employment continues",
    )
    decision = _first(
        text,
        (
            r"(?:照常|仍然|还是).{0,8}(?:到岗|上班|去公司|处理工作)",
            r"(?:去|走向|赶往|回到).{0,8}公司",
            r"朝着公司的方向",
            r"推门走了进去",
            r"今天不去|决定请假",
        ),
        "today's attendance decision is explicit",
    )
    event = _result(bool(ack_unknown and decision), "ack_state_and_attendance_decision" if ack_unknown and decision else "employment_decision_component_missing", ack_unknown, decision)

    ended = None
    for match in re.finditer(r"(?:已经离职|正式离职|辞职(?:已经)?生效|劳动关系(?:已经)?终止|不再是公司的人)", text):
        prefix = text[max(0, match.start() - 18):match.start()]
        suffix = text[match.end():match.end() + 3]
        if re.search(r"(?:是否|是不是|问.{0,8}|并没有|没有|尚未|不是)$", prefix) or re.match(r"(?:吗|么|？|\?)", suffix):
            continue
        ended = _span(text, match, "employment is asserted ended")
        break
    acknowledgement = _first(
        text,
        (
            r"(?:公司|人事).{0,12}(?:已经|已|正式)(?:确认|批准|同意)",
            r"(?:公司|人事).{0,12}(?:批准|同意)(?:了|辞职)",
            r"正式生效日期.{0,8}(?:已到|到了)",
        ),
        "termination prerequisite exists",
    )
    ended_violation = bool(ended and not acknowledgement)
    violation = _result(ended_violation, "ended_without_acknowledgement" if ended_violation else "no_unacknowledged_termination", ended if ended_violation else acknowledgement)
    defer = _first(text, (r"(?:没有决定|不再说明|略过).{0,12}(?:上班|到岗|公司)", r"到这里结束"), "attendance decision is avoided")
    evasion = _result(not event.value and bool(defer), "attendance_decision_avoided" if defer else "no_task_evasion", defer)
    return event, {"employment_ended_without_acknowledgement": violation}, evasion


def _unsourced(text: str) -> BinaryResult:
    evidence = []
    pattern_claims = (
        (r"(?:隔壁组的陈晨|隔壁工位的周哲|同事小周|同事小李)", "unnamed coworker receives a name"),
        (r"忘了关共享屏幕", "unprovided screen-share event is added"),
        (r"(?:昨晚|之前).{0,16}朋友圈.{0,16}(?:截图|长图)|她确实发过一条", "unprovided prior distribution event is added"),
        (r"抄送了?.{0,8}(?:直属)?主管", "additional resignation recipient is added"),
        (r"昨天下午收拾工位", "prior workspace clearout is added"),
        (r"人事那边说还在走流程", "indirect HR response is added"),
    )
    for pattern, claim in pattern_claims:
        span = _first(text, (pattern,), claim)
        if span:
            evidence.append(span)
    return _result(bool(evidence), "unsourced_persistent_candidate" if evidence else "no_tracked_unsourced_candidate", *evidence)


def evaluate_text_v2(scene_id: str, text: str) -> EvaluationV2:
    if scene_id == "adversarial-storefront-hours":
        event, violations, evasion = _storefront(text)
    elif scene_id == "adversarial-unpublished-knowledge":
        event, violations, evasion = _knowledge(text)
    elif scene_id == "adversarial-object-and-repeat":
        event, violations, evasion = _object_repeat(text)
    elif scene_id == "adversarial-employment-transition":
        event, violations, evasion = _employment(text)
    else:
        raise ValueError(f"unknown scene: {scene_id}")
    if tuple(violations) != SCENE_CHECKS[scene_id]:
        raise ValueError("V2 violation target drift")
    return EvaluationV2(
        scene_id=scene_id,
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        required_event_completed=event,
        hard_reality_violations=violations,
        task_evasion=evasion,
        unsourced_setting=_unsourced(text),
    )


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(pairs: list[tuple[bool, bool]]) -> dict[str, object]:
    tp = sum(pred and gold for pred, gold in pairs)
    fp = sum(pred and not gold for pred, gold in pairs)
    fn = sum(not pred and gold for pred, gold in pairs)
    tn = sum(not pred and not gold for pred, gold in pairs)
    ratio = lambda n, d: round(n / d, 4) if d else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn), "specificity": ratio(tn, tn + fp)}


def run_benchmark(path: Path) -> dict[str, object]:
    fixture = _read(path)
    pairs = {"required_event_completed": [], "hard_reality_violations": [], "task_evasion": [], "unsourced_setting": []}
    evaluations = []
    for case in fixture["cases"]:
        result = evaluate_text_v2(case["scene_id"], case["text"])
        expected = case["expected"]
        pairs["required_event_completed"].append((result.required_event_completed.value, expected["required_event_completed"]))
        pairs["task_evasion"].append((result.task_evasion.value, expected["task_evasion"]))
        pairs["unsourced_setting"].append((result.unsourced_setting.value, expected["unsourced_setting"]))
        if set(expected["hard_reality_violations"]) != set(result.hard_reality_violations):
            raise ValueError(f"benchmark violation target drift: {case['case_id']}")
        for check_id, expected_value in expected["hard_reality_violations"].items():
            pairs["hard_reality_violations"].append((result.hard_reality_violations[check_id].value, expected_value))
        evaluations.append({"case_id": case["case_id"], "result": result.model_dump(mode="json"), "expected": expected})
    metrics = {key: _metric(value) for key, value in pairs.items()}
    threshold = fixture["gate"]["minimum_precision_recall"]
    passed = all(metric[dimension] is not None and metric[dimension] >= threshold for metric in metrics.values() for dimension in ("precision", "recall"))
    return {
        "schema_version": "world-runtime-evaluator-v2-benchmark-v1",
        "partition": fixture["partition"],
        "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "case_count": len(fixture["cases"]),
        "metrics": metrics,
        "gate": {"minimum_precision_recall": threshold, "passed": passed},
        "evaluations": evaluations,
        "generation_authorized": False,
    }


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--partition", choices=("calibration", "holdout"), default="holdout")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    fixture = CALIBRATION if args.partition == "calibration" else HOLDOUT
    result = run_benchmark(fixture)
    _write(args.output, result)
    print(json.dumps({key: result[key] for key in ("partition", "fixture_sha256", "case_count", "metrics", "gate", "generation_authorized")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
