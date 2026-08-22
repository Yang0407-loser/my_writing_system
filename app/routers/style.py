"""风格分析 API。"""

from fastapi import APIRouter, Header, HTTPException
from ..agents.style_analyzer import StyleAnalyzer, STYLE_PRESETS
from ..utils.llm_client import set_api_key

router = APIRouter(prefix="/api/style", tags=["style"])


@router.post("/analyze")
def analyze_style(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    """分析参考文本，返回 4 维风格参数。"""
    if x_api_key: set_api_key(x_api_key)
    reference_text = (body or {}).get("reference_text", "")
    if not reference_text or not reference_text.strip():
        raise HTTPException(status_code=400, detail="reference_text 不能为空")
    sa = StyleAnalyzer()
    profile = sa.analyze(reference_text)
    return {"style_profile": profile}


@router.post("/preset")
def get_style_preset(body: dict, x_api_key: str = Header("", alias="X-API-Key")):
    """获取预设风格。"""
    if x_api_key: set_api_key(x_api_key)
    preset_name = (body or {}).get("preset_name", "中性")
    profile = StyleAnalyzer.get_preset(preset_name)
    profile["preset_name"] = preset_name
    return {"style_profile": profile, "available_presets": StyleAnalyzer.list_presets()}
