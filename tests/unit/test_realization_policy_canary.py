from pathlib import Path
from unittest import mock

from experiments.realization_policy_canary.experiment import (
    build,
    build_public,
    execute,
    load_json,
    write_json,
    TERMINAL_STATUSES,
)
from experiments.realization_policy_canary.review import (
    RealizationPolicyBlindReview,
)

_FAKE_TEXT = (
    "她伸手碰了碰桌上的信封，指尖在封口处停了一瞬。信封没有拆，纸面上还留着三年前的折痕。"
    "沈砚站在档案馆门口，雨水顺着伞骨滴在门槛上。闸门已经关了大半，工具箱留在水里。"
    "安全线在黑暗中反着微光。他看着她，什么都没说。她也没有开口，只是把信封往他那边推了推。"
    "窗外有鸟叫声。手机屏幕上显示姐姐的语音消息，四十八小时倒计时从昨天就开始算了。"
    "冰箱的嗡鸣声停了，她注意到钟的指针还停在原来的位置。她找出那块旧布，小心翼翼地把钟罩上。"
    "记录本上还空着一栏温度，他没去填。两天后的事没有着落，此刻也没有人急着给答案。"
    "她收回手，桌上的茶杯已经凉了。他不记得是从哪一刻开始不再问她，也不记得她什么时候开始不再等他开口。"
    "走廊尽头传来脚步声，由远及近又渐远。台风已经过境，积水还没退。"
    "她把双手抄进外套口袋，站起来的时候椅子发出轻微声响。他没有跟上去，但也没有立即离开。"
    "灯光跳了一下，又稳住了。她没回头，径直走进档案架子深处的黑暗里。"
    "他伸手拿起桌上那只未拆的信封，指尖感受到纸面微凉的触感，翻过来看了看封口，原样放回桌面。"
    "工具箱沉在闸门另一侧的水底，扳手和万用表散落在泥浆里，钢尺斜插在踏板缝隙间。"
    "她走到铺子角落，从架子上抽出一块深灰色的绒布，展开检查有没有蛀洞，然后走到老钟旁边。"
    "餐馆的冰箱塞满了贴着标签的药盒，顾遥把最后一盒胰岛素放进去，关上玻璃门，温度计显示四度。"
    "梅澈已经翻过安全线，蹲在干爽的楼梯台阶上拧外套的水，抬头看他。他没有回头看她。"
    "语音条还亮着，红点一闪一闪。她没有播放，只是把手机翻了个面扣在腿上。"
    "陈泊在记录本上写下日期和时间，把本子翻过一页，压平，搁在冰箱顶上。"
    "雨水顺着闸门的缝隙渗过来，像一条细线慢慢扩大。他低头看着那些水痕，左膝盖隐隐发酸。"
    "没有回应。也不需要回应。"
) * 3


def test_canary_build_freezes_16_single_variable_samples(tmp_path: Path):
    manifest = build(tmp_path)
    assert manifest["sample_count"] == 16
    assert manifest["scenes"] == 4
    assert manifest["repeats_per_arm"] == 2
    assert manifest["silent_reruns_allowed"] is False
    assert {item["arm"] for item in manifest["samples"]} == {"A", "B"}

    for scene_id in {item["scene_id"] for item in manifest["samples"]}:
        scene = [item for item in manifest["samples"] if item["scene_id"] == scene_id]
        assert len(scene) == 4
        assert len({item["common_input_hash"] for item in scene}) == 1
        assert {item["repeat"] for item in scene} == {1, 2}

    arm_a = next(item for item in manifest["samples"] if item["arm"] == "A")
    arm_b = next(
        item
        for item in manifest["samples"]
        if item["arm"] == "B" and item["scene_id"] == arm_a["scene_id"]
    )
    a_text = "\n".join(item["content"] for item in arm_a["messages"])
    b_text = "\n".join(item["content"] for item in arm_b["messages"])
    assert "Sparse Decision Kernel" not in a_text
    assert "叙述姿态" not in a_text
    assert "Sparse Decision Kernel" in b_text
    assert "叙述姿态" in b_text
    assert "信息选择性" not in b_text
    assert "解释压力" not in b_text


def test_public_builder_requires_all_real_results(tmp_path: Path):
    build(tmp_path)
    summary = {
        "succeeded": 0,
        "failed": 0,
    }
    from experiments.realization_policy_canary.experiment import write_json

    write_json(tmp_path / "run-summary.json", summary)
    try:
        build_public(tmp_path)
    except ValueError as error:
        assert "16" in str(error)
    else:
        raise AssertionError("incomplete run must not produce blind material")

    ledger = load_json(tmp_path / "attempt-ledger.json")
    assert all(item["attempt_count"] == 0 for item in ledger["samples"].values())


def test_review_contract_requires_eight_blind_pairs_and_independence():
    schema = RealizationPolicyBlindReview.model_json_schema()
    assert schema["properties"]["blocks"]["minItems"] == 8
    assert schema["properties"]["blocks"]["maxItems"] == 8
    scope = schema["$defs"]["ReviewScope"]["properties"]
    assert scope["blind_key_accessed"]["const"] is False
    assert scope["other_reviews_accessed"]["const"] is False
    assert scope["public_material_only"]["const"] is True


# ── Recovery / resume tests ──────────────────────────────────────────────

def _mock_client():
    fake = mock.MagicMock()
    fake.chat_completion.return_value = _FAKE_TEXT
    return fake


def _pending_ledger_state(manifest):
    return {
        item["sample_id"]: {
            "request_hash": item["request_hash"],
            "status": "pending",
            "attempt_count": 0,
        }
        for item in manifest["samples"]
    }


def test_execute_succeeded_samples_are_not_rerun(tmp_path: Path):
    """succeeded items must be skipped; only pending items are executed."""
    manifest = build(tmp_path)
    ledger_path = tmp_path / "attempt-ledger.json"
    ledger = load_json(ledger_path)
    for sid in ("RP-01", "RP-02", "RP-03"):
        ledger["samples"][sid]["status"] = "succeeded"
        ledger["samples"][sid]["attempt_count"] = 1
    write_json(ledger_path, ledger)

    with mock.patch(
        "experiments.realization_policy_canary.experiment.get_llm_client",
        return_value=_mock_client(),
    ):
        summary = execute(tmp_path)

    ledger = load_json(ledger_path)
    for sid in ("RP-01", "RP-02", "RP-03"):
        assert ledger["samples"][sid]["status"] == "succeeded", f"{sid} was re-run"
        assert ledger["samples"][sid]["attempt_count"] == 1
    assert summary["already_terminal"] == 3
    assert summary["newly_attempted"] == 13
    assert summary["succeeded"] >= 13


def test_execute_failed_samples_are_not_rerun(tmp_path: Path):
    """failed items must be skipped; only pending items are executed."""
    manifest = build(tmp_path)
    ledger_path = tmp_path / "attempt-ledger.json"
    ledger = load_json(ledger_path)
    for sid in ("RP-05", "RP-06"):
        ledger["samples"][sid]["status"] = "failed"
        ledger["samples"][sid]["attempt_count"] = 1
    write_json(ledger_path, ledger)

    with mock.patch(
        "experiments.realization_policy_canary.experiment.get_llm_client",
        return_value=_mock_client(),
    ):
        summary = execute(tmp_path)

    ledger = load_json(ledger_path)
    for sid in ("RP-05", "RP-06"):
        assert ledger["samples"][sid]["status"] == "failed", f"{sid} was re-run"
        assert ledger["samples"][sid]["attempt_count"] == 1
    assert summary["already_terminal"] == 2
    assert summary["newly_attempted"] == 14


def test_execute_attempted_samples_are_not_rerun(tmp_path: Path):
    """attempted items (interrupted mid-flight) must be skipped."""
    manifest = build(tmp_path)
    ledger_path = tmp_path / "attempt-ledger.json"
    ledger = load_json(ledger_path)
    for sid in ("RP-09", "RP-10"):
        ledger["samples"][sid]["status"] = "attempted"
        ledger["samples"][sid]["attempt_count"] = 1
    write_json(ledger_path, ledger)

    with mock.patch(
        "experiments.realization_policy_canary.experiment.get_llm_client",
        return_value=_mock_client(),
    ):
        summary = execute(tmp_path)

    ledger = load_json(ledger_path)
    for sid in ("RP-09", "RP-10"):
        assert ledger["samples"][sid]["status"] == "attempted", f"{sid} was re-run"
        assert ledger["samples"][sid]["attempt_count"] == 1
    assert summary["already_terminal"] == 2
    assert summary["newly_attempted"] == 14


def test_execute_only_continues_pending(tmp_path: Path):
    """After resume, every non-pending item is skipped; only pending ones run."""
    manifest = build(tmp_path)
    ledger_path = tmp_path / "attempt-ledger.json"
    ledger = load_json(ledger_path)
    expected_skip = 0
    for sid, entry in ledger["samples"].items():
        if sid.startswith("RP-0"):
            entry["status"] = "succeeded"
            entry["attempt_count"] = 1
            expected_skip += 1
        elif sid.startswith("RP-1"):
            entry["status"] = "failed"
            entry["attempt_count"] = 1
            expected_skip += 1
    write_json(ledger_path, ledger)

    with mock.patch(
        "experiments.realization_policy_canary.experiment.get_llm_client",
        return_value=_mock_client(),
    ):
        summary = execute(tmp_path)

    assert summary["already_terminal"] == 16
    assert summary["newly_attempted"] == 0
    assert summary["pending"] == 0

    ledger = load_json(ledger_path)
    for sid, entry in ledger["samples"].items():
        assert entry["status"] in TERMINAL_STATUSES, f"{sid} not terminal"
        assert entry["attempt_count"] <= 1, f"{sid} re-requested"


def test_execute_attempt_count_never_exceeds_one(tmp_path: Path):
    """No sample is ever requested more than once, regardless of resume count."""
    manifest = build(tmp_path)
    ledger_path = tmp_path / "attempt-ledger.json"

    with mock.patch(
        "experiments.realization_policy_canary.experiment.get_llm_client",
        return_value=_mock_client(),
    ):
        summary1 = execute(tmp_path)
    assert summary1["newly_attempted"] == 16

    ledger = load_json(ledger_path)
    for entry in ledger["samples"].values():
        assert entry["attempt_count"] == 1

    with mock.patch(
        "experiments.realization_policy_canary.experiment.get_llm_client",
        return_value=_mock_client(),
    ):
        summary2 = execute(tmp_path)
    assert summary2["already_terminal"] == 16
    assert summary2["newly_attempted"] == 0

    ledger = load_json(ledger_path)
    for entry in ledger["samples"].values():
        assert entry["attempt_count"] == 1, "attempt_count exceeded 1 after resume"
