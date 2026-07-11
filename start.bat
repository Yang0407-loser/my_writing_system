@echo off
setlocal enabledelayedexpansion

title Writer Launcher v0.9.0

set "PROJDIR=%~dp0"
if "%PROJDIR:~-1%"=="\" set "PROJDIR=%PROJDIR:~0,-1%"
cd /d "%PROJDIR%"
set "REDIS=E:\Redis"

echo.
echo ============================================================
echo   Multi-Agent Writing System v0.9.0
echo ============================================================
echo.

:: ==========================================
:: 1. .env check
:: ==========================================
if not exist ".env" (
    echo [WARN] .env not found, copying from .env.example...
    copy .env.example .env >nul 2>&1
    echo [INFO] Please edit .env and set LLM_API_KEY before submitting a write task.
)
echo [OK] .env

:: ==========================================
:: 2. Redis
:: ==========================================
echo [1/4] Starting Redis...
"%REDIS%\redis-cli.exe" ping >nul 2>&1
if !errorlevel! neq 0 (
    start "Writer-Redis" /min "%REDIS%\redis-server.exe"
    timeout /t 3 /nobreak >nul
    "%REDIS%\redis-cli.exe" ping >nul 2>&1
    if !errorlevel! neq 0 (
        echo [FAIL] Redis failed to start. Check E:\Redis\redis-server.exe
        pause
        exit /b 1
    )
)
echo [OK] Redis

:: ==========================================
:: 3. Dependencies
:: ==========================================
echo [2/4] Checking dependencies...
uv run python -c "print('deps ok')" >nul 2>&1
if !errorlevel! neq 0 (
    echo   Installing dependencies...
    uv sync
    if !errorlevel! neq 0 (
        echo [FAIL] uv sync failed
        pause
        exit /b 1
    )
)
echo [OK] Dependencies

:: ==========================================
:: 4. Celery
:: ==========================================
echo [3/4] Starting Celery Worker...
start "Writer-Celery" /d "%PROJDIR%" cmd /k "uv run celery -A app.celery_app worker --loglevel=info -P solo"
echo [OK] Celery launched

:: ==========================================
:: 5. FastAPI
:: ==========================================
echo [4/4] Starting FastAPI...
start "Writer-FastAPI" /d "%PROJDIR%" cmd /k "uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
echo [OK] FastAPI launched

:: ==========================================
:: Done
:: ==========================================
echo.
echo   Waiting for server to be ready...
timeout /t 5 /nobreak >nul

echo.
echo ============================================================
echo   All services started!  v0.9.0
echo.
echo   Main UI v2:     http://localhost:8000/write-ui-v2
echo   Main UI:         http://localhost:8000/write-ui
echo   Classic:         http://localhost:8000
echo   Interactive:     http://localhost:8000/interactive
echo   API Docs:        http://localhost:8000/docs
echo ============================================================
echo.
echo   Phase 1 - Foundation:
echo     - Storyline constraints (immutable narrative skeleton)
echo     - Foreshadowing management (plant -^> resolve lifecycle)
echo     - Rule center (10 built-in presets + import/export)
echo.
echo   Phase 2 - Card Drawing:
echo     - Multi-option generation (3-5 cards per step)
echo     - Inspiration library (45+ tropes)
echo     - Redraw / modify / skip per card
echo.
echo   Phase 3 - Deep Memory:
echo     - Experience timeline (protagonist long-term memory)
echo     - Scene-level outline (detailed mode)
echo     - Dialogue mode (context-aware AI brainstorming)
echo.
echo   Phase 4 - Quality:
echo     - Chapter veins (narrative task assignment)
echo     - AI artifact detection (high-freq + pattern matching)
echo     - Item inventory system (gain -^> transfer -^> consume)
echo     - Outline logic evaluation
echo.
echo   Phase 5 - Subplot System:
echo     - 7-element subplot model
echo     - Chapter heat map (prevent overload)
echo     - Enhanced character system (power levels, survival status)
echo.
echo   Phase 6 - World Building:
echo     - Enhanced interactive writing (section/paragraph granularity)
echo     - Story map (infinite nodes + protagonist route)
echo     - Cytoscape.js visualization ready
echo.
echo   Close the Celery and FastAPI windows to stop.

start http://localhost:8000/write-ui-v2
pause
