"""AI痕迹检测 API。"""

from fastapi import APIRouter
from pydantic import BaseModel
from .. import ai_artifact_detector as detector

router = APIRouter(prefix="/api/ai-detect", tags=["ai_detect"])


class DetectRequest(BaseModel):
    text: str


@router.post("/analyze")
def analyze(req: DetectRequest):
    return detector.analyze_text(req.text)


@router.post("/high-freq")
def high_freq(req: DetectRequest):
    return {"words": detector.detect_high_frequency(req.text)}


@router.post("/patterns")
def patterns(req: DetectRequest):
    return {"patterns": detector.detect_ai_patterns(req.text)}
