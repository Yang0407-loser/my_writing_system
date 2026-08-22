"""Scene Reality Contract v0 — frozen fact-priority contract for the Writer prompt.

This module is experiment-only and is intentionally NOT imported by the
production Writer. The contract text is frozen as written in the experiment
specification; do not edit it after generation begins.
"""

from __future__ import annotations

import hashlib

SCENE_REALITY_CONTRACT_VERSION = "scene-reality-contract-v0"
SCENE_REALITY_CONTRACT_V01_VERSION = "scene-reality-contract-v0.1"

# The frozen contract text. Its hash is recorded in the experiment JSON so the
# generation can be audited against an unmodified contract.
SCENE_REALITY_CONTRACT_V0_TEXT = """Scene Reality Contract v0

事实优先级：
本合同高于风格指导、氛围描写、修辞偏好和模型自行补充的背景。正文不得用新设定绕过合同。

地点：

1. 林晚工作的写字楼位于国贸。
2. 林晚租住在需要从国贸乘车抵达的老小区。
3. 「野面包」位于林晚居住的老小区附近。
4. 「野面包」不在国贸写字楼街对面。
5. 从国贸闻不到「野面包」正在烘烤的气味。
6. 场景从国贸切换到老小区时，必须出现明确交通或时间过渡。

辞职状态：

1. 发往林晚私人邮箱的"辞职信"只是个人草稿或自我确认。
2. 私人邮箱不能构成向公司正式辞职。
3. 如果正文没有写明辞职信息已经送达主管、老板、HR或人事，则林晚的状态只能是"决定辞职"或"准备辞职"。
4. 后文不得称其为"已经辞职""辞职后"或"辞职那天"，除非正文先补齐正式送达行为。

信息传播：

1. 在林晚主动告诉季晴、把消息发给季晴，或公司正式通知季晴之前，季晴不知道林晚决定辞职。
2. 如果季晴发送"你辞职了？"之类的信息，正文必须在此前明确建立她的信息来源。
3. 不能用"她大概听说了""不知道怎么知道的"等模糊表达绕过信息来源。

营业与作息：

1. 「野面包」只在周六对外营业。
2. 周野在周六凌晨三点半开始揉面。
3. 三点半以前可以亮灯、备料、称重、清洁或预热烤箱，但不能出现揉面动作、揉面声或已经进行中的揉面。
4. 非营业日若存在生产活动，必须来自大纲、世界设定或本合同已有事实。
5. 不得临时发明"试做新品""临时订单""店主加班"等理由解释非营业日香气。
6. 周三店铺关闭时，林晚不能闻到现场刚烘烤、带着温度的新鲜面包香气；可以写她回忆起以前闻到的味道，但必须明确是记忆，不是当前现场气味。

拍摄许可：

1. 林晚可以拍摄店铺外观、招牌、黑板和关闭的窗户。
2. 在周野明确同意以前，林晚不能拍到可识别的周野本人或其清晰背影。
3. "别开闪光灯"只有在第一次拍摄人物之前说出，才能视为附带边界的拍摄许可。
4. 获得许可后仍不得擅自进入私人操作区域，除非周野明确邀请。

制作流程：

1. 三点半刚开始揉制的面团不可能在三点四十分成为烤好的面包。
2. 三点四十分出现的成品只能来自此前已经完成揉制和发酵的另一批面团。
3. 如果正文使用预制批次，必须在成品出炉之前明确交代"前一晚冷藏发酵""上一批面团"或同等事实。
4. 不得让读者通过猜测来补齐批次来源。

禁止事项：

1. 不得新增没有来源的人名。
2. 不得新增没有来源的经营安排。
3. 不得用大段解释修补矛盾。
4. 不得为了满足合同改变四个小节的核心事件。
5. 不得在结尾增加总结主题或升华句来代替事实闭合。"""


def scene_reality_contract_hash(text: str = SCENE_REALITY_CONTRACT_V0_TEXT) -> str:
    """SHA-256 of the exact contract text, used for experiment auditability."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# v0.1 is deliberately subsection-scoped. The failed v0 prompt repeated every
# fact in all four calls and allowed the model to satisfy reality constraints by
# deleting difficult beats. These invariants preserve the semantic event while
# explicitly repairing the two contradictions already present in the source
# outline (S1 location and S2 early kneading).
_EVENT_INVARIANTS_V01 = {
    1: (
        "收到第20版驳回邮件并写辞职信到私人邮箱",
        "从国贸乘车回老小区，在住处附近处理野面包线索",
        "季晴发来辞职相关消息，且此前必须写明她的信息来源",
        "翻到小黑板照片并决定拍摄第一个生活切片",
    ),
    2: (
        "完整保留第一、第二、第三个周六的三次蹲守",
        "第一次只听声音不见人；到3:30后才能出现揉面声",
        "第二次经顾衍指点拍到不可识别的模糊背影",
        "第三次周野先给出拍摄许可，再拍到清晰背影并写记录草稿",
    ),
    3: (
        "周野叮嘱别开闪光灯后继续揉面",
        "林晚退出店外，坐在夜航船台阶写完第一篇记录",
        "文章保留周野的专注细节和林晚的初步反思",
    ),
    4: (
        "失眠顾客偶遇面包店并与周野发生实际交汇",
        "保留凌晨揉面的仪式感",
        "保留书店暖光下的静默",
        "保留社区夜归人交汇；不得改成林晚独自进入面包店",
    ),
}

_FACTS_V01 = {
    1: (
        "国贸是工作地；野面包在乘车抵达的老小区附近，国贸闻不到店内香气",
        "私人邮箱中的辞职信不构成正式离职",
        "周三关店时只能明确回忆旧香气，不能出现现场新鲜烘烤香气",
    ),
    2: (
        "周野周六3:30开始揉面；3:30前只能到达、等待或备料",
        "拍外观无需许可；拍可识别人物前必须先获得许可",
    ),
    3: (
        "别开闪光灯必须发生在第一次可识别人物拍摄之前",
        "辞职仍是决定或准备状态，除非正文明确送达公司",
    ),
    4: (
        "3:40出现成品时，必须先说明来自前一晚或上一批已发酵面团",
        "野面包只在周六对外营业",
    ),
}

_ALLOWED_RESOLUTIONS_V01 = {
    1: (
        "[ALLOW:S1_LOCATION] 将‘下楼路过野面包’改为乘车回老小区后路过，或改成在国贸回忆旧香气；不得删除发现线索和决定拍摄",
        "[ALLOW:S1_KNOWLEDGE] 在季晴消息之前，用一句话明确林晚主动告诉她；不得删除季晴消息",
    ),
    2: (
        "[ALLOW:S2_SCHEDULE] 保留3:00到达，但写成等待到3:30才听见揉面声；不得删除第一次蹲守",
        "[ALLOW:S2_PERMISSION] 第二次只能是不可识别的远景；第三次把‘别开闪光灯’放到清晰拍摄之前",
    ),
    3: (
        "[ALLOW:S3_STATUS] 使用‘决定辞职那晚’而不是‘辞职后/辞职那天’，除非先写明公司已收到",
    ),
    4: (
        "[ALLOW:S4_BATCH] 保留失眠顾客和面包，将成品明确写成前一晚冷藏发酵的批次；不得删除顾客事件",
    ),
}


def render_scene_reality_contract_v01(subsection: int) -> str:
    """Render a compact, event-preserving hard contract for one subsection."""
    if subsection not in _EVENT_INVARIANTS_V01:
        raise ValueError(f"unsupported subsection: {subsection}")
    lines = [
        "Scene Reality Contract v0.1（硬约束；仅限本小节）",
        "优先级：MUST_EVENT > FACT > 风格与氛围。",
        "不得通过删除、替换或跳过 MUST_EVENT 来满足 FACT。",
        "若原大纲的地点或时间表达与 FACT 冲突，只能使用 ALLOW 中的解决方式。",
        "",
        "MUST_EVENT：",
    ]
    lines.extend(f"- {item}" for item in _EVENT_INVARIANTS_V01[subsection])
    lines.append("FACT：")
    lines.extend(f"- {item}" for item in _FACTS_V01[subsection])
    lines.append("ALLOW：")
    lines.extend(f"- {item}" for item in _ALLOWED_RESOLUTIONS_V01[subsection])
    lines.extend(
        [
            "FORBID：不得新增人名、经营安排或解释性背景；不得写大段说明。",
            "完成标准：所有 MUST_EVENT 均在正文中实际发生，且不违反 FACT。",
        ]
    )
    return "\n".join(lines)


def scene_reality_contract_v01_hash(subsection: int) -> str:
    return scene_reality_contract_hash(render_scene_reality_contract_v01(subsection))
