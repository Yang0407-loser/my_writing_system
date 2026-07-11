"""页面路由。"""

import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index():
    """重定向到新版前端 (v0.9)。"""
    return RedirectResponse(url="/write-ui-v2")


@router.get("/write-ui-v2", response_class=HTMLResponse)
def write_ui_v2_page():
    """新版前端 (v0.9) —— ES模块化架构。"""
    static_index = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
    if os.path.exists(static_index):
        with open(static_index, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>前端资源未找到</h1>", status_code=404)


# 旧路由兼容重定向
@router.get("/v2")
@router.get("/debug")
def legacy_debug_redirect():
    return RedirectResponse(url="/write-ui-v2")


@router.get("/interactive")
def legacy_interactive_redirect():
    return RedirectResponse(url="/write-ui-v2")


@router.get("/write-ui")
def legacy_write_ui_redirect():
    return RedirectResponse(url="/write-ui-v2")
