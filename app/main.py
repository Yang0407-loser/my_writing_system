"""多智能体协作写作系统 —— FastAPI 入口。"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from .routers import pages, tasks, generate, style, characters, history, analysis
from .routers import rules, foreshadowings, cards, dialogue, experience, items, subplots, ai_detect, map, projects, factions, character_relations


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """应用生命周期：启动时初始化预设规则、校验配置。"""
    from .rule_store import ensure_presets_seeded
    from .config import settings
    ensure_presets_seeded()
    warnings = settings.validate()
    if warnings:
        import logging
        lg = logging.getLogger("writing_system")
        for w in warnings:
            lg.warning(f"[config] {w}")
    yield


app = FastAPI(title="多智能体协作写作系统", version="0.9.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 前端静态文件 (v0.9 新UI)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(pages.router)
app.include_router(tasks.router)
app.include_router(generate.router)
app.include_router(style.router)

app.include_router(characters.router)
app.include_router(history.router)
app.include_router(analysis.router)
app.include_router(rules.router)
app.include_router(foreshadowings.router)
app.include_router(cards.router)
app.include_router(dialogue.router)
app.include_router(experience.router)
app.include_router(items.router)
app.include_router(subplots.router)
app.include_router(ai_detect.router)
app.include_router(map.router)
app.include_router(projects.router)
app.include_router(factions.router)
app.include_router(character_relations.router)
