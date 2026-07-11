"""风格分析 API。"""

from fastapi import APIRouter, Header, HTTPException
from ..agents.style_analyzer import StyleAnalyzer, STYLE_PRESETS
from ..utils.llm_client import set_api_key

router = APIRouter(prefix="/api/style", tags=["style"])


@router.post("/analyze")
def analyze_style(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    """分析参考文本，返回 50 维风格特征。"""
    if x_api_key: set_api_key(x_api_key)
    reference_text = (body or {}).get("reference_text", "")
    if not reference_text or not reference_text.strip():
        raise HTTPException(status_code=400, detail="reference_text 不能为空")
    sa = StyleAnalyzer()
    profile = sa.analyze(reference_text)
    profile["style_brief"] = sa.build_brief(profile)
    return {"style_profile": profile}


@router.post("/preset")
def get_style_preset(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    """获取预设风格。"""
    if x_api_key: set_api_key(x_api_key)
    preset_name = (body or {}).get("preset_name", "中性")
    profile = StyleAnalyzer.get_preset(preset_name)
    sa = StyleAnalyzer()
    profile["style_brief"] = sa.build_brief(profile)
    profile["preset_name"] = preset_name
    return {"style_profile": profile, "available_presets": StyleAnalyzer.list_presets()}


@router.post("/brief")
def regenerate_brief(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    """根据现有风格参数重新生成简报。"""
    if x_api_key: set_api_key(x_api_key)
    profile = (body or {}).get("style_profile", {})
    if not profile:
        raise HTTPException(status_code=400, detail="style_profile 不能为空")
    sa = StyleAnalyzer()
    return {"style_brief": sa.build_brief(profile)}
