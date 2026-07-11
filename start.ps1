$host.UI.RawUI.WindowTitle = "多智能体协作写作系统"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  多智能体协作写作系统 — 一键启动" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ==========================================
# 1. 检查 .env
# ==========================================
if (-not (Test-Path ".env")) {
    Write-Host "[WARN] .env 不存在，正在从 .env.example 复制..." -ForegroundColor Yellow
    Copy-Item .env.example .env
    Write-Host "[INFO] 请编辑 .env 填入 DeepSeek API Key 后重新运行" -ForegroundColor Yellow
    Start-Process notepad .env
    Pause
    exit 1
}

# ==========================================
# 2. 检查 Redis
# ==========================================
Write-Host "[1/4] 检查 Redis 服务..." -ForegroundColor Green
$redisOk = $false

# 尝试连接已有的 Redis
try {
    $null = redis-cli ping 2>$null
    if ($LASTEXITCODE -eq 0) {
        $redisOk = $true
        Write-Host "  Redis 已运行" -ForegroundColor Green
    }
} catch {}

# 尝试启动 Windows 原生 Redis (E:\Redis)
if (-not $redisOk) {
    $redisExe = "E:\Redis\redis-server.exe"
    if (Test-Path $redisExe) {
        Write-Host "  正在启动 Windows 原生 Redis..." -ForegroundColor Yellow
        Start-Process $redisExe -WindowStyle Minimized
        Start-Sleep -Seconds 2
        try {
            $null = & "E:\Redis\redis-cli.exe" ping 2>$null
            if ($LASTEXITCODE -eq 0) {
                $redisOk = $true
                Write-Host "  Redis 已启动 (Windows 原生)" -ForegroundColor Green
            }
        } catch {}
    }
}

# 尝试通过 docker-compose 启动
if (-not $redisOk) {
    Write-Host "  正在通过 docker-compose 启动 Redis..." -ForegroundColor Yellow
    docker-compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] 无法启动 Redis。请手动安装 Redis 或 Docker Desktop" -ForegroundColor Red
        Pause
        exit 1
    }
    Write-Host "  Redis 已启动 (Docker)" -ForegroundColor Green
}

# ==========================================
# 3. 检查依赖
# ==========================================
Write-Host "[2/4] 检查 Python 依赖..." -ForegroundColor Green
$depsOk = $false
try {
    uv run python -c "import fastapi, celery, chromadb, sentence_transformers" 2>$null
    if ($LASTEXITCODE -eq 0) { $depsOk = $true }
} catch {}

if (-not $depsOk) {
    Write-Host "  正在安装依赖（首次需下载 BGE-M3 模型，约 2 分钟）..." -ForegroundColor Yellow
    uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] 依赖安装失败" -ForegroundColor Red
        Pause
        exit 1
    }
}
Write-Host "  依赖就绪" -ForegroundColor Green

# ==========================================
# 4. 启动 Celery Worker
# ==========================================
Write-Host "[3/4] 启动 Celery Worker..." -ForegroundColor Green
$celeryCmd = "cd /d `"$ProjectDir`" && uv run celery -A app.celery_app worker --loglevel=info -P solo"
Start-Process cmd -ArgumentList "/c", $celeryCmd -WindowStyle Normal

# ==========================================
# 5. 启动 FastAPI
# ==========================================
Write-Host "[4/4] 启动 FastAPI 服务..." -ForegroundColor Green
$apiCmd = "cd /d `"$ProjectDir`" && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
Start-Process cmd -ArgumentList "/c", $apiCmd -WindowStyle Normal

# ==========================================
# 等待并验证
# ==========================================
Write-Host ""
Write-Host "  等待服务就绪..." -ForegroundColor Yellow
Start-Sleep -Seconds 4

try {
    $null = Invoke-WebRequest -Uri "http://localhost:8000/docs" -UseBasicParsing -TimeoutSec 3
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  启动完成！" -ForegroundColor Green
    Write-Host ""
    Write-Host "  API 文档 : http://localhost:8000/docs" -ForegroundColor White
    Write-Host "  提交写作 : POST http://localhost:8000/write" -ForegroundColor White
    Write-Host "  查询状态 : GET  http://localhost:8000/status/{task_id}" -ForegroundColor White
    Write-Host ""
    Write-Host "  按任意键打开浏览器..." -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Cyan
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    Start-Process http://localhost:8000/docs
} catch {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  服务正在启动中，请稍候..." -ForegroundColor Yellow
    Write-Host "  API 文档: http://localhost:8000/docs" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Cyan
    Pause
}
