"""抽卡模式 API。"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel
from ..agents.card_drawer import CardDrawer
from ..utils.llm_client import set_api_key

router = APIRouter(prefix="/api/cards", tags=["cards"])


class DrawRequest(BaseModel):
    step: str  # world_setting | protagonist | outline | outline_refine | writing | subplot
    context: dict = {}
    num_cards: int = 4
    user_requirement: str = ""


class RedrawRequest(BaseModel):
    step: str
    context: dict = {}
    card_index: int = 0
    user_feedback: str = ""


@router.post("/draw")
def draw_cards(req: DrawRequest, x_api_key: str = Header("", alias="X-API-Key")):
    """生成多张方案卡片。"""
    if x_api_key: set_api_key(x_api_key)
    drawer = CardDrawer()
    cards = drawer.draw_cards(
        step=req.step,
        context=req.context,
        num_cards=req.num_cards,
        user_requirement=req.user_requirement,
    )
    return {"step": req.step, "cards": cards, "total": len(cards)}


@router.post("/redraw")
def redraw_card(req: RedrawRequest, x_api_key: str = Header("", alias="X-API-Key")):
    """重抽单张卡片。"""
    if x_api_key: set_api_key(x_api_key)
    drawer = CardDrawer()
    card = drawer.redraw_card(
        step=req.step,
        context=req.context,
        card_index=req.card_index,
        user_feedback=req.user_feedback,
    )
    return {"card": card}


@router.get("/genres")
def list_genres():
    """返回可用题材列表（P8）。"""
    from ..utils.genre_presets import GENRE_LIST, GENRE_INJECTIONS
    return {
        "genres": [{"key": g, "label": g, **GENRE_INJECTIONS.get(g, {})} for g in GENRE_LIST]
    }


@router.get("/steps")
def list_steps():
    """返回可用步骤列表（供前端展示步骤向导）。"""
    return {
        "steps": [
            {"key": "world_setting", "label": "世界观设定", "order": 1,
             "description": "设定小说的世界框架、核心冲突和风格基调"},
            {"key": "protagonist", "label": "主角设定", "order": 2,
             "description": "设计主角的性格、背景、动机和金手指"},
            {"key": "outline", "label": "大纲规划", "order": 3,
             "description": "规划章节结构和关键事件"},
            {"key": "outline_refine", "label": "大纲完善", "order": 4,
             "description": "为具体章节补充场景细节和角色互动"},
            {"key": "writing", "label": "正文写作", "order": 5,
             "description": "根据大纲生成正文，每步提供多个方向选择"},
            {"key": "subplot", "label": "支线故事", "order": 6,
             "description": "设计支线故事丰富主线剧情"},
        ]
    }


# ── 灵感库 ──

INSPIRATIONS = {
    "world_setting": [
        {"name": "修仙宗门流", "description": "主角加入修仙宗门，从杂役弟子一步步修炼成仙",
         "tags": ["修仙", "升级", "宗门"], "example": "凡人修仙传"},
        {"name": "末日废土流", "description": "世界末日后的生存挣扎，资源匮乏，秩序崩溃",
         "tags": ["末日", "生存", "黑暗"], "example": "全球高武"},
        {"name": "赛博朋克流", "description": "高科技低生活的近未来世界，大公司统治，人体改造盛行",
         "tags": ["科幻", "反乌托邦", "赛博"], "example": "赛博英雄传"},
        {"name": "都市异能流", "description": "日常生活背后隐藏着超自然力量，普通人意外获得异能",
         "tags": ["都市", "异能", "隐藏世界"], "example": "龙族"},
        {"name": "重生复仇流", "description": "回到过去重新开始，利用前世记忆改变命运",
         "tags": ["重生", "复仇", "爽文"], "example": "赘婿"},
        {"name": "穿越异世界", "description": "普通人穿越到异世界（奇幻/仙侠/魔法世界）的冒险",
         "tags": ["穿越", "异世界", "冒险"], "example": "斗罗大陆"},
        {"name": "无限流", "description": "主角在多个不同世界中穿梭，完成任务，获得能力",
         "tags": ["无限流", "多世界", "升级"], "example": "无限恐怖"},
        {"name": "种田文", "description": "以建设、发展、种田为核心的慢节奏故事",
         "tags": ["种田", "经营", "慢热"], "example": "放开那个女巫"},
        {"name": "悬疑推理", "description": "以谜题、案件为核心的智力博弈",
         "tags": ["悬疑", "推理", "智力"], "example": "诡秘之主"},
        {"name": "玄幻争霸", "description": "多势力角逐，战争策略，权谋博弈",
         "tags": ["玄幻", "争霸", "权谋"], "example": "雪中悍刀行"},
    ],
    "protagonist": [
        {"name": "废柴逆袭型", "description": "开局极弱，通过努力和机缘一步步变强",
         "tags": ["逆袭", "成长", "励志"]},
        {"name": "重生复仇型", "description": "前世被害，重生后利用先知优势复仇",
         "tags": ["重生", "复仇", "爽文"]},
        {"name": "扮猪吃虎型", "description": "实际很强但刻意隐藏实力，关键时刻爆发",
         "tags": ["隐藏实力", "爆发", "反差"]},
        {"name": "天才流", "description": "天赋异禀，但面临更强大的敌人和挑战",
         "tags": ["天才", "挑战", "竞争"]},
        {"name": "平民英雄型", "description": "普通人因机缘成为英雄，代表普通人视角",
         "tags": ["平凡", "英雄", "共鸣"]},
        {"name": "反英雄型", "description": "灰色道德观，不择手段但有自己的原则",
         "tags": ["灰色", "现实", "复杂"]},
        {"name": "智谋型", "description": "武力不强但智力超群，靠策略取胜",
         "tags": ["智慧", "策略", "谋略"]},
        {"name": "幸运MAX型", "description": "运气极好，总能在关键时刻遇到机缘",
         "tags": ["幸运", "机缘", "轻松"]},
    ],
    "plot_twist": [
        {"name": "恩人即仇人", "description": "一直帮助主角的人其实是陷害主角的真凶"},
        {"name": "身份反转", "description": "主角的隐藏身份被揭示，震动所有人"},
        {"name": "队友背叛", "description": "最信任的伙伴在关键时刻背叛"},
        {"name": "死而复生", "description": "被认为已死的角色突然回归"},
        {"name": "世界真相", "description": "揭示世界本质的惊天秘密"},
        {"name": "双面间谍", "description": "某个角色一直在为两方传递情报"},
        {"name": "时间循环", "description": "揭示主角一直处于时间循环中"},
    ],
    "climax": [
        {"name": "擂台对决", "description": "公开擂台/比武大会上的巅峰对决"},
        {"name": "禁地探险", "description": "进入危险禁地寻找宝物或真相"},
        {"name": "大军压境", "description": "敌方大军围攻主角所在之地"},
        {"name": "登顶之战", "description": "主角冲击最高境界/地位的关键一战"},
        {"name": "智斗对决", "description": "核心矛盾通过智谋和布局而非武力解决"},
        {"name": "身份揭晓", "description": "主角隐藏身份在万众瞩目下揭晓"},
    ],
}


@router.get("/inspirations")
def get_inspirations(category: str = Query("", description="分类筛选")):
    """获取灵感库数据。"""
    if category and category in INSPIRATIONS:
        return {"inspirations": INSPIRATIONS[category], "category": category}
    return {"inspirations": INSPIRATIONS, "categories": list(INSPIRATIONS.keys())}
