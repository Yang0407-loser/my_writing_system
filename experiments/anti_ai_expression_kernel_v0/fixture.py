"""Frozen single-scene content fixture; no commercial or reality policy."""

from __future__ import annotations


FIXTURE_VERSION = "anti-ai-expression-fixture-v0"
TARGET_CHARACTERS = 800

SCENE_CONTENT = {
    "title": "第一篇草稿",
    "viewpoint": "第三人称近距离，跟随林晚",
    "characters": [
        {"name": "林晚", "state": "刚决定离开长期加班的工作，开始记录社区生活"},
        {"name": "周野", "state": "凌晨在面包店操作间揉面，话少，专注工作"},
        {"name": "顾衍", "state": "夜航船书店老板，凌晨仍在店内"},
    ],
    "must_happen": [
        "周野在林晚拍摄前说别开闪光灯，随后继续揉面",
        "林晚退出面包店，坐到夜航船书店门口的台阶上",
        "林晚用手机写下《一个只肯把时间分给面包的人》",
        "草稿写到周野工作的具体动作，也触及林晚对自己工作状态的不满",
        "小节结束时林晚保存草稿，没有发布",
    ],
    "must_not_happen": [
        "不新增人物或背景设定",
        "不让林晚和周野发展成爱情关系",
        "不发布文章，不产生网络流量",
        "不改变人物目标和事件顺序",
    ],
    "allowed_end_state": "林晚保存第一篇未发布的草稿，仍坐在或刚离开书店门口。",
}
