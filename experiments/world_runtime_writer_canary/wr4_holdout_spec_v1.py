"""WR4 sealed holdout authored spec (independent author, v1).

This module is the independent-author source for the WR4 unseen gold set.
It was written without consulting the v1 training gold (task 07d1391e) or
the tuned variant's failure analysis beyond the authoring instructions in the
plan (create a new sealed holdout for the unseen task 3a4e561a).

All evidence phrases are taken verbatim from the frozen corpus snapshot for
task 3a4e561a.  The builder fails closed when any phrase is missing from the
claimed gold sections, when a gold section is not strictly prior context
(section < current section), or when the fixture cannot be bound to the
corpus hash.
"""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "wr4-gold-retrieval-holdout-v1"
BUILD_VERSION = "wr4-gold-retrieval-holdout-builder-v1"
TASK_ID = "3a4e561a-2d5d-4679-9da0-892a8a2b52e3"

CHARACTER_NAMES = ("林晚", "周野", "季晴", "顾衍", "吴阿姨")


# Each spec:
#   query_index       stable H01..H20 id
#   tier              story_fact (12) or wr_key_evidence (8)
#   query_intent      subset of SUPPORTED_INTENTS
#   section           current section the writer is producing
#   subsection        current subsection (1)
#   query             writing-requirement style question (no gold text)
#   wr_keys           WR key triples for Tier B
#   gold_sections     strictly prior sections that contain the evidence
#   gold_chunk_keys   human-readable chunk keys used as anchors
#   must_recall_facts facts the retrieval must support
#   fact_evidence     fact -> verbatim phrase list (all phrases must exist
#                     in the union of the gold_sections chunks)
#   requires_causal_retrieval
SPECS: list[dict[str, Any]] = [
    {
        "query_index": "H01",
        "tier": "story_fact",
        "query_intent": ["character", "event", "scene"],
        "section": 5,
        "subsection": 1,
        "query": (
            "周野的职业背景：以前是做什么的？做了几年、后来为什么改行？"
            "野面包为什么只在周六营业，他每天几点开始揉面"
        ),
        "wr_keys": [],
        "gold_sections": [0, 2, 3, 4],
        "gold_chunk_keys": [
            "S0:前作",
            "S2:第一卷",
            "S3:第1章：客户说「感觉不对」的那天",
            "S4:第2章：凌晨三点半的陌生人",
        ],
        "must_recall_facts": [
            "周野以前做建筑设计，是前建筑设计师",
            "野面包只在周六营业，凌晨三点半开始揉面，卖完即止",
        ],
        "fact_evidence": {
            "周野以前做建筑设计，是前建筑设计师": [
                "以前做建筑设计的",
                "前建筑设计师",
            ],
            "野面包只在周六营业，凌晨三点半开始揉面，卖完即止": [
                "周六营业。卖完即止",
                "凌晨三点半开始揉面",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H02",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 4,
        "subsection": 1,
        "query": (
            "林晚辞职当天的经过：客户对第几版文案说了什么？"
            "她最终回复了什么，把辞职信发给了谁"
        ),
        "wr_keys": [],
        "gold_sections": [0, 2, 3],
        "gold_chunk_keys": [
            "S0:前作",
            "S2:第一卷",
            "S3:第1章：客户说「感觉不对」的那天",
        ],
        "must_recall_facts": [
            "客户对第二十版文案仍回复整体感觉还是不太对",
            "林晚最后回复我不改了，并把辞职信发到自己的私人邮箱",
        ],
        "fact_evidence": {
            "客户对第二十版文案仍回复整体感觉还是不太对": [
                "第二十版文案的第七行",
                "整体感觉还是不太对",
            ],
            "林晚最后回复我不改了，并把辞职信发到自己的私人邮箱": [
                "我不改了",
                "收件人填了自己",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H03",
        "tier": "story_fact",
        "query_intent": ["character", "scene"],
        "section": 5,
        "subsection": 1,
        "query": (
            "林晚第一次遇到野面包的经过：她在凌晨几点、因为什么拐进巷子？"
            "她隔着玻璃门看到什么，回家后记下了什么"
        ),
        "wr_keys": [],
        "gold_sections": [2, 3, 4],
        "gold_chunk_keys": [
            "S2:第一卷",
            "S3:第1章：客户说「感觉不对」的那天",
            "S4:第2章：凌晨三点半的陌生人",
        ],
        "must_recall_facts": [
            "林晚凌晨两点四十八分顺着面包香气找到野面包",
            "她隔着玻璃门看到一个男人在揉面，并记下凌晨三点半有人在揉面",
        ],
        "fact_evidence": {
            "林晚凌晨两点四十八分顺着面包香气找到野面包": [
                "凌晨两点四十八分。哪家面包店这个点开门",
            ],
            "她隔着玻璃门看到一个男人在揉面，并记下凌晨三点半有人在揉面": [
                "玻璃门后面，一个男人正在揉面",
                "凌晨三点半。有人在揉面",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H04",
        "tier": "story_fact",
        "query_intent": ["character", "event", "scene"],
        "section": 6,
        "subsection": 1,
        "query": (
            "林晚前几周的蹲守经历：第一周拍到了什么？第二周绕到后窗拍到几张？"
            "第三周周野给了她什么、说了什么"
        ),
        "wr_keys": [],
        "gold_sections": [3, 4, 5],
        "gold_chunk_keys": [
            "S3:第1章：客户说「感觉不对」的那天",
            "S4:第2章：凌晨三点半的陌生人",
            "S5:第3章：三个问题",
        ],
        "must_recall_facts": [
            "第一周林晚照片一张没拍，第二周绕到后窗拍到四张模糊的背影",
            "第三周周野递给她一杯热水，说拍可以、别开闪光灯",
        ],
        "fact_evidence": {
            "第一周林晚照片一张没拍，第二周绕到后窗拍到四张模糊的背影": [
                "照片一张没拍",
                "拍到四张模糊的背影",
            ],
            "第三周周野递给她一杯热水，说拍可以、别开闪光灯": [
                "保温杯",
                "拍可以。",
                "别开闪光灯",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H05",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 7,
        "subsection": 1,
        "query": (
            "老爷爷的买面包习惯：他每周买什么、买几个、付多少钱？"
            "他的孙女几岁、为什么只吃这家的菠萝包"
        ),
        "wr_keys": [],
        "gold_sections": [5, 6],
        "gold_chunk_keys": [
            "S5:第3章：三个问题",
            "S6:第4章：菠萝包与老爷爷",
        ],
        "must_recall_facts": [
            "老爷爷每周买两个菠萝包，付二十块找两个硬币",
            "孙女七岁、爸妈离婚，每周六来爷爷家，管菠萝包叫格子面包",
        ],
        "fact_evidence": {
            "老爷爷每周买两个菠萝包，付二十块找两个硬币": [
                "菠萝包。两个",
                "付二十块，找两个硬币",
            ],
            "孙女七岁、爸妈离婚，每周六来爷爷家，管菠萝包叫格子面包": [
                "孙女七岁",
                "她管它叫格子面包",
                "她爸妈去年离了",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H06",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 8,
        "subsection": 1,
        "query": (
            "两篇生活切片发布后的流量后果：第一篇点赞停在哪里？评论区在问什么？"
            "周野做了什么、说了什么"
        ),
        "wr_keys": [],
        "gold_sections": [5, 6, 7],
        "gold_chunk_keys": [
            "S5:第3章：三个问题",
            "S6:第4章：菠萝包与老爷爷",
            "S7:第5章：意外的流量",
        ],
        "must_recall_facts": [
            "第一篇生活切片点赞停在四万二，评论区没有人写味道",
            "周野在店门口贴出每人限购两个，并说面包是给人吃的、不是给人拍的",
        ],
        "fact_evidence": {
            "第一篇生活切片点赞停在四万二，评论区没有人写味道": [
                "点赞四万二",
                "没有人写味道",
            ],
            "周野在店门口贴出每人限购两个，并说面包是给人吃的、不是给人拍的": [
                "每人限购两个",
                "我做的面包是给人吃的",
                "不是给人拍的",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H07",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 9,
        "subsection": 1,
        "query": (
            "林晚删文之后：她重新发布了什么声明？周野发来什么消息？"
            "她的回复是什么"
        ),
        "wr_keys": [],
        "gold_sections": [7, 8],
        "gold_chunk_keys": [
            "S7:第5章：意外的流量",
            "S8:第6章：删帖与邀请",
        ],
        "must_recall_facts": [
            "林晚删掉两篇动态后发布之前那篇我删了、以后只拍我看到的、不打扰任何人",
            "周野发微信说周六缺人手、来当店员，林晚回复好",
        ],
        "fact_evidence": {
            "林晚删掉两篇动态后发布之前那篇我删了、以后只拍我看到的、不打扰任何人": [
                "之前那篇我删了，以后只拍我看到的，不打扰任何人",
            ],
            "周野发微信说周六缺人手、来当店员，林晚回复好": [
                "周六缺人手，来当店员",
                "好",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H08",
        "tier": "story_fact",
        "query_intent": ["character", "scene"],
        "section": 10,
        "subsection": 1,
        "query": (
            "第一个店员日的细节：林晚几点到店？周野给了她什么颜色的围裙？"
            "她洗了多少只模具，周野给她吃了什么"
        ),
        "wr_keys": [],
        "gold_sections": [8, 9],
        "gold_chunk_keys": [
            "S8:第6章：删帖与邀请",
            "S9:第7章：面粉与模具",
        ],
        "must_recall_facts": [
            "第一个店员日林晚凌晨两点三十一分到店，周野递给她一条深灰色粗麻围裙",
            "林晚洗了三十六只模具，周野让她吃烤过头的边角料",
        ],
        "fact_evidence": {
            "第一个店员日林晚凌晨两点三十一分到店，周野递给她一条深灰色粗麻围裙": [
                "凌晨两点三十一分到店",
                "深灰色",
                "粗麻的",
            ],
            "林晚洗了三十六只模具，周野让她吃烤过头的边角料": [
                "三十六只模具",
                "烤过了",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H09",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 12,
        "subsection": 1,
        "query": (
            "周野的客人笔记本：老爷爷的习惯怎么记的？可颂女生为什么改到周四来？"
            "老爷爷后来为什么两周没来"
        ),
        "wr_keys": [],
        "gold_sections": [9, 10, 11],
        "gold_chunk_keys": [
            "S9:第7章：面粉与模具",
            "S10:第8章：无名童谣",
            "S11:第9章：失恋的可颂",
        ],
        "must_recall_facts": [
            "笔记本记着老爷爷要菠萝包、每次两个、不要袋子",
            "可颂女生找到新工作以后周六要上班，所以周四来买两个；老爷爷摔了一跤在家躺了半个月",
        ],
        "fact_evidence": {
            "笔记本记着老爷爷要菠萝包、每次两个、不要袋子": [
                "老爷爷——菠萝包。每次两个。不要袋子",
            ],
            "可颂女生找到新工作以后周六要上班，所以周四来买两个；老爷爷摔了一跤在家躺了半个月": [
                "说找到新工作了。以后周六要上班",
                "摔了一跤。在家躺了半个月",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H10",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 12,
        "subsection": 1,
        "query": (
            "周野哼唱的无名旋律：他揉面到某个阶段会哼什么？这个旋律是谁教的？"
            "他母亲以前做什么、现在还在吗"
        ),
        "wr_keys": [],
        "gold_sections": [9, 10, 11],
        "gold_chunk_keys": [
            "S9:第7章：面粉与模具",
            "S10:第8章：无名童谣",
            "S11:第9章：失恋的可颂",
        ],
        "must_recall_facts": [
            "周野揉面到某个阶段会哼一段没有词的旋律，是母亲教的",
            "母亲以前在老家开小作坊做馒头包子，哼这个哄面团长大，已经走了七年",
        ],
        "fact_evidence": {
            "周野揉面到某个阶段会哼一段没有词的旋律，是母亲教的": [
                "我妈教的",
                "嗯嗯啊啊",
            ],
            "母亲以前在老家开小作坊做馒头包子，哼这个哄面团长大，已经走了七年": [
                "她以前在老家开过小作坊",
                "做馒头包子。哼这个哄面团长大",
                "走了七年了",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H11",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 13,
        "subsection": 1,
        "query": (
            "周野父亲来店里的冲突：父亲以前教什么？他说了什么反对的话？"
            "收工后周野对林晚说了什么"
        ),
        "wr_keys": [],
        "gold_sections": [11, 12],
        "gold_chunk_keys": [
            "S11:第9章：失恋的可颂",
            "S12:第10章：父亲来了",
        ],
        "must_recall_facts": [
            "周野的父亲教了三十年历史，觉得面包上不了历史书",
            "父亲说做面包没什么出息，还说如果你妈在的话也不会同意",
        ],
        "fact_evidence": {
            "周野的父亲教了三十年历史，觉得面包上不了历史书": [
                "他教了三十年历史",
                "觉得面包上不了历史书",
            ],
            "父亲说做面包没什么出息，还说如果你妈在的话也不会同意": [
                "做面包有什么出息",
                "你妈在的话也不会同意",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H12",
        "tier": "story_fact",
        "query_intent": ["character", "event"],
        "section": 14,
        "subsection": 1,
        "query": (
            "深夜文章《凌晨三点半的厨房》发布后的回应：文章结尾写了什么？"
            "阅读量到多少？周野发来什么消息，下个周六操作台上多了什么"
        ),
        "wr_keys": [],
        "gold_sections": [12, 13],
        "gold_chunk_keys": [
            "S12:第10章：父亲来了",
            "S13:第11章：那篇深夜文章",
        ],
        "must_recall_facts": [
            "文章结尾写到周野做的不是面包、是跟时间谈判的方式，阅读量破百万",
            "周野看到后发来看到了和谢谢，下个周六操作台上放了一杯温水",
        ],
        "fact_evidence": {
            "文章结尾写到周野做的不是面包、是跟时间谈判的方式，阅读量破百万": [
                "他做的不是面包，是跟时间谈判的方式",
                "阅读量破百万了",
            ],
            "周野看到后发来看到了和谢谢，下个周六操作台上放了一杯温水": [
                "看到了",
                "谢谢",
                "操作台上放着一杯水",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H13",
        "tier": "wr_key_evidence",
        "query_intent": ["character", "scene"],
        "section": 5,
        "subsection": 1,
        "query": "林晚住在哪里、搬进来多久？周野在什么地方揉面",
        "wr_keys": [
            ["character_state", "character:lin-wan", "location"],
            ["character_state", "character:zhou-ye", "location"],
        ],
        "gold_sections": [0, 2, 3, 4],
        "gold_chunk_keys": [
            "S0:前作",
            "S2:第一卷",
            "S3:第1章：客户说「感觉不对」的那天",
            "S4:第2章：凌晨三点半的陌生人",
        ],
        "must_recall_facts": [
            "林晚住在自己搬进来三年的房子里",
            "周野在操作间揉面，林晚隔着玻璃门看到",
        ],
        "fact_evidence": {
            "林晚住在自己搬进来三年的房子里": [
                "她搬进来三年",
            ],
            "周野在操作间揉面，林晚隔着玻璃门看到": [
                "玻璃门后面，一个男人正在揉面",
                "男人已经回到操作台前，继续揉面",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H14",
        "tier": "wr_key_evidence",
        "query_intent": ["event", "scene"],
        "section": 7,
        "subsection": 1,
        "query": "野面包的营业时间：周野每周几点开始揉面、几点开窗通风、几点开门？营业日是星期几",
        "wr_keys": [
            ["world_clock", "time", ""],
            ["continuity_state", "bakery:wild-bread", "storefront_open_time"],
        ],
        "gold_sections": [0, 2, 3, 4, 5, 6],
        "gold_chunk_keys": [
            "S0:前作",
            "S2:第一卷",
            "S4:第2章：凌晨三点半的陌生人",
            "S5:第3章：三个问题",
        ],
        "must_recall_facts": [
            "周野每周六凌晨三点半开始揉面，四点半开窗通风，七点开门",
            "野面包只有周六营业，卖完即止",
        ],
        "fact_evidence": {
            "周野每周六凌晨三点半开始揉面，四点半开窗通风，七点开门": [
                "每周六凌晨三点半开始揉面",
                "四点半会开窗通风",
                "七点开门",
            ],
            "野面包只有周六营业，卖完即止": [
                "周六营业。卖完即止",
                "这家店只周六开",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H15",
        "tier": "wr_key_evidence",
        "query_intent": ["event", "scene"],
        "section": 9,
        "subsection": 1,
        "query": "野面包的经营状态与限购：店门口贴了什么？为什么贴",
        "wr_keys": [
            ["continuity_state", "bakery:wild-bread", "operation_state"],
            ["continuity_state", "bakery:wild-bread", "purchase_limit"],
        ],
        "gold_sections": [4, 5, 6, 7, 8],
        "gold_chunk_keys": [
            "S4:第2章：凌晨三点半的陌生人",
            "S7:第5章：意外的流量",
            "S8:第6章：删帖与邀请",
        ],
        "must_recall_facts": [
            "野面包只在周六营业",
            "流量涌入后店门口贴出每人限购两个的告示",
        ],
        "fact_evidence": {
            "野面包只在周六营业": [
                "那家店只周六开",
            ],
            "流量涌入后店门口贴出每人限购两个的告示": [
                "店门口贴了一张纸",
                "每人限购两个",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H16",
        "tier": "wr_key_evidence",
        "query_intent": ["character", "event"],
        "section": 12,
        "subsection": 1,
        "query": "林晚与面包店的关系变化：她怎么开始当店员？她学了哪些做面包的基础",
        "wr_keys": [
            ["character_state", "character:lin-wan", "employment"],
            ["character_state", "character:lin-wan", "skill"],
        ],
        "gold_sections": [8, 9, 10, 11],
        "gold_chunk_keys": [
            "S8:第6章：删帖与邀请",
            "S9:第7章：面粉与模具",
        ],
        "must_recall_facts": [
            "周野邀请林晚周六到店里当店员",
            "林晚开始学做面包：认高筋粉、洗模具、听发酵时的气泡声",
        ],
        "fact_evidence": {
            "周野邀请林晚周六到店里当店员": [
                "周六缺人手，来当店员",
            ],
            "林晚开始学做面包：认高筋粉、洗模具、听发酵时的气泡声": [
                "高筋粉",
                "发酵时听气泡声",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H17",
        "tier": "wr_key_evidence",
        "query_intent": ["character"],
        "section": 13,
        "subsection": 1,
        "query": "周野父亲的身份与父子冲突：父亲教什么、怎么看待周野做面包",
        "wr_keys": [
            ["character_state", "character:zhou-ye-father", "employment"],
            ["character_state", "character:zhou-ye", "family_relationship"],
        ],
        "gold_sections": [11, 12],
        "gold_chunk_keys": [
            "S11:第9章：失恋的可颂",
            "S12:第10章：父亲来了",
        ],
        "must_recall_facts": [
            "周野的父亲教了三十年历史",
            "父亲认为做面包没出息，觉得面包上不了历史书",
        ],
        "fact_evidence": {
            "周野的父亲教了三十年历史": [
                "他教了三十年历史",
            ],
            "父亲认为做面包没出息，觉得面包上不了历史书": [
                "做面包有什么出息",
                "觉得面包上不了历史书",
            ],
        },
        "requires_causal_retrieval": False,
    },
    {
        "query_index": "H18",
        "tier": "wr_key_evidence",
        "query_intent": ["character", "event"],
        "section": 16,
        "subsection": 1,
        "query": "深夜文章的传播与周野的知情状态：阅读量多少？周野看到了吗、发了什么",
        "wr_keys": [
            ["character_state", "character:lin-wan", "article_readership"],
            ["character_state", "character:zhou-ye", "article_awareness"],
        ],
        "gold_sections": [13, 14, 15],
        "gold_chunk_keys": [
            "S13:第11章：那篇深夜文章",
            "S14:第12章：一袋吐司",
            "S15:第13章：流量的背面",
        ],
        "must_recall_facts": [
            "深夜文章阅读量破百万",
            "周野看到文章后发来看到了和谢谢",
        ],
        "fact_evidence": {
            "深夜文章阅读量破百万": [
                "阅读量破百万了",
                "那篇《凌晨三点半的厨房》",
            ],
            "周野看到文章后发来看到了和谢谢": [
                "看到了",
                "谢谢",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H19",
        "tier": "wr_key_evidence",
        "query_intent": ["character", "scene"],
        "section": 17,
        "subsection": 1,
        "query": "社区生活墙的来龙去脉：墙上贴了什么？是谁打印和布置的？纸条写了什么",
        "wr_keys": [
            ["continuity_state", "community:activity-room", "photo_wall"],
            ["character_state", "character:wu-ayi", "action"],
        ],
        "gold_sections": [14, 15, 16],
        "gold_chunk_keys": [
            "S14:第12章：一袋吐司",
            "S15:第13章：流量的背面",
            "S16:第14章：社区生活墙",
        ],
        "must_recall_facts": [
            "吴阿姨在社区活动室为林晚布置了一面贴满照片的墙",
            "墙上纸条写着不是偷窥、你拍的是我们的日子",
        ],
        "fact_evidence": {
            "吴阿姨在社区活动室为林晚布置了一面贴满照片的墙": [
                "墙上贴满了照片",
                "我打印的。彩色打印机。社区那台",
            ],
            "墙上纸条写着不是偷窥、你拍的是我们的日子": [
                "不是偷窥",
                "你拍的是我们的日子",
            ],
        },
        "requires_causal_retrieval": True,
    },
    {
        "query_index": "H20",
        "tier": "wr_key_evidence",
        "query_intent": ["character", "event"],
        "section": 19,
        "subsection": 1,
        "query": "面包边角料戒指与求婚：周野送了什么、戒指内壁刻了什么字？林晚怎么回应？两人随后一起做什么",
        "wr_keys": [
            ["object_state", "bakery:slow-bread:ring", "exists"],
            ["character_state", "character:lin-wan", "relationship_status"],
        ],
        "gold_sections": [15, 16, 17, 18],
        "gold_chunk_keys": [
            "S15:第13章：流量的背面",
            "S16:第14章：社区生活墙",
            "S17:第15章：面包边角料的戒指",
            "S18:第16章：按时吃饭",
        ],
        "must_recall_facts": [
            "周野用面包边角料烤硬雕了一枚戒指，内壁刻着野字",
            "林晚说这算求婚，周野答应；两人一起经营慢面包工坊",
        ],
        "fact_evidence": {
            "周野用面包边角料烤硬雕了一枚戒指，内壁刻着野字": [
                "用面包边角料烤硬了雕成的",
                "圈内壁刻着一个字",
            ],
            "林晚说这算求婚，周野答应；两人一起经营慢面包工坊": [
                "这算求婚",
                "慢面包工坊",
            ],
        },
        "requires_causal_retrieval": True,
    },
]


def tier_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in SPECS:
        counts[spec["tier"]] = counts.get(spec["tier"], 0) + 1
    return counts
