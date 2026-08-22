"""Frozen cross-scene fixtures for expression-only replication."""

from __future__ import annotations


FIXTURE_VERSION = "anti-ai-expression-replication-fixtures-v0"
TARGET_CHARACTERS = 800

SCENES = (
    {
        "scene_id": "handover",
        "title": "交接单",
        "viewpoint": "第三人称近距离，跟随陈默",
        "characters": [
            {"name": "陈默", "state": "准备离开工作三年的公司，已经完成交接材料"},
            {"name": "唐主管", "state": "陈默的直属主管，留下来处理最后的离职手续"},
        ],
        "must_happen": [
            "陈默把装着交接材料的文件夹推给唐主管",
            "唐主管问陈默是否考虑清楚，陈默明确回答考虑清楚了",
            "唐主管在交接单最后一页签字",
            "陈默把门禁卡留在文件夹上",
            "陈默独自离开办公室并进入电梯",
        ],
        "must_not_happen": [
            "不新增挽留、争吵、哭泣或反悔",
            "不解释陈默辞职后的去向",
            "不新增人物或关系",
            "不改变事件顺序",
        ],
        "allowed_end_state": "电梯门合上；门禁卡仍留在唐主管面前的文件夹上。",
    },
    {
        "scene_id": "bicycle_chain",
        "title": "雨里的链条",
        "viewpoint": "第三人称近距离，跟随贺舟",
        "characters": [
            {"name": "贺舟", "state": "雨夜送餐途中，自行车链条突然脱落，订单仍在倒计时"},
            {"name": "路人", "state": "撑伞经过，不认识贺舟"},
        ],
        "must_happen": [
            "贺舟停车，把自行车翻过来检查脱落的链条",
            "路人停下来询问是否需要帮助",
            "贺舟不让路人修车，只请对方用手机照亮齿盘",
            "贺舟把链条从后轮处解开，重新套上齿盘，并转动脚踏确认恢复",
            "贺舟向路人道谢，扶正自行车继续赶订单",
        ],
        "must_not_happen": [
            "不新增交通事故、受伤、赔偿或感情发展",
            "不让路人替贺舟完成修理",
            "不让订单凭空取消或时间停止",
            "不新增人物或改变事件顺序",
        ],
        "allowed_end_state": "自行车恢复可骑，订单倒计时仍在继续，贺舟已经重新上路。",
    },
)


def scene_by_id(scene_id: str) -> dict:
    for scene in SCENES:
        if scene["scene_id"] == scene_id:
            return scene
    raise KeyError(scene_id)

