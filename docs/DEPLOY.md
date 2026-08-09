# 部署指南（阿里云 ECS）

## 前置条件

- 阿里云 ECS 一台（CentOS 7.9 / Ubuntu 22.04，2核4G 足够）
- 一个域名（可选，但强烈建议有，否则搞不了 HTTPS）
- 域名 DNS 已解析到 ECS 公网 IP
- 安全组已放行：**22**（SSH）、**443**（HTTPS）、**80**（HTTP，Let's Encrypt 验证用）

---

## 一、基础环境

### CentOS 7

```bash
# 工具
yum install -y epel-release git wget curl vim

# Python 3.11
yum install -y python3.11 python3.11-pip python3.11-devel
python3.11 -m pip install --upgrade pip

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.cargo/env

# Redis
yum install -y redis
```

### Ubuntu 22.04

```bash
apt update
apt install -y git wget curl vim python3.11 python3.11-venv python3.11-dev redis-server

# uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.cargo/env
```

---

## 二、Redis 安全配置

```bash
vim /etc/redis/redis.conf    # Ubuntu
vim /etc/redis.conf           # CentOS
```

改以下三行：

```
bind 127.0.0.1
requirepass 你的密码
```

启动：

```bash
systemctl enable redis
systemctl start redis
```

验证：

```bash
redis-cli -a 你的密码 ping
# 返回 PONG
```

---

## 三、Ollama + BGE-M3

```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable ollama
systemctl start ollama
ollama pull bge-m3
```

---

## 四、部署项目

```bash
mkdir -p /opt/writer
```

把代码传到服务器（在**你本地**执行）：

```powershell
cd my_writing_system
tar --exclude='.venv' --exclude='chroma_data' --exclude='__pycache__' --exclude='*.db' -czf writer.tar.gz .
scp writer.tar.gz root@你的IP:/opt/writer/
```

在服务器上：

```bash
cd /opt/writer
tar -xzf writer.tar.gz
rm writer.tar.gz
```

创建 `.env`：

```bash
cp .env.example .env
vim .env
```

内容：

```bash
# LLM —— 留空，用户自己在前端填
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-pro

# Embedding —— 用刚装的 Ollama
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=bge-m3

# Redis
REDIS_BROKER_URL=redis://:你的密码@127.0.0.1:6379/0
REDIS_BACKEND_URL=redis://:你的密码@127.0.0.1:6379/1

# 存储
CHROMA_DATA_PATH=./chroma_data
CHARACTER_DB_PATH=./characters.db
TASK_DB_PATH=./tasks.db
```

安装依赖：

```bash
uv sync
```

---

## 五、Systemd Service 文件

### FastAPI

`/etc/systemd/system/writer-api.service`：

```ini
[Unit]
Description=Writer FastAPI
After=network.target redis.service ollama.service
Wants=redis.service ollama.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/writer
Environment="PATH=/root/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/root/.cargo/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Celery Worker

`/etc/systemd/system/writer-worker.service`：

```ini
[Unit]
Description=Writer Celery Worker
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/writer
Environment="PATH=/root/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/root/.cargo/bin/uv run celery -A app.celery_app worker --loglevel=info -P solo -c 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 启动

```bash
systemctl daemon-reload
systemctl enable writer-api writer-worker
systemctl start writer-api writer-worker
```

### 常用命令

```bash
systemctl status writer-api          # 看状态
systemctl restart writer-api         # 重启
journalctl -u writer-api -f          # 实时日志
journalctl -u writer-worker -f       # Worker 日志
```

---

## 六、Nginx + HTTPS

```bash
yum install -y nginx certbot python3-certbot-nginx   # CentOS
apt install -y nginx certbot python3-certbot-nginx    # Ubuntu
```

### Nginx 配置

`/etc/nginx/conf.d/writer.conf`：

```nginx
server {
    listen 80;
    server_name 你的域名;

    # 静态资源直接返回
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-API-Key $http_x_api_key;   # ★ 必须透传
        proxy_read_timeout 600s;                        # 写作任务可能很久
        proxy_buffering off;
    }

    # SSE / Stream 不缓冲
    location /stream/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-API-Key $http_x_api_key;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
    }
}
```

### 申请证书

```bash
systemctl start nginx
certbot --nginx -d 你的域名
# 按提示输入邮箱，选 redirect（HTTP → HTTPS）
```

证书会自动续期，不用管。

### 验证

```bash
curl https://你的域名/write-ui-v2
# 应返回 HTML 页面
```

---

## 七、前端 API 地址

本地开发时 `api.js` 里写的是 `http://localhost:8000`。部署后需要改为你的域名。

**方案 A（推荐）**：改 `api.js` 让它在部署时用相对路径，配合 Nginx 反代自动生效。

```javascript
// api.js 第1行改为
const BASE = window.location.origin;
```

这样本地开发访问 `http://localhost:8000/write-ui-v2` 时 BASE 自动是 `http://localhost:8000`；部署后访问 `https://你的域名/write-ui-v2` 时自动是 `https://你的域名`。

**方案 B**：保持 localhost 写死，然后在 index.html 加载后通过 `persistence.js` 恢复用户上次填的 `apiBase`。但这种做法需要你已经在 localStorage 存过。

---

## 八、验证清单

```bash
# 1. 确认各服务运行
systemctl status redis ollama writer-api writer-worker nginx

# 2. 确认端点可达
curl https://你的域名/api/characters

# 3. 确认前端可访问
# 浏览器打开 https://你的域名/write-ui-v2

# 4. 提交一个写作任务，确认能正常跑
# 在 UI 中填入你的 DeepSeek Key，输入主题，点开始
```

---

## 九、常用维护

```bash
# 查看 Worker 日志
journalctl -u writer-worker -f --since "10 min ago"

# 重启全部
systemctl restart writer-api writer-worker

# 更新代码
cd /opt/writer
systemctl stop writer-api writer-worker
git pull   # 或用 scp 重新传
uv sync
systemctl start writer-api writer-worker

# Redis 清理（最好不要随便清，会丢正在跑的任务）
redis-cli -a 你的密码 FLUSHALL  # 慎用

# 查看磁盘（chroma_data 会慢慢变大）
du -sh /opt/writer/chroma_data
```

---

## 十、安全注意

1. **安全组只放 443 和 80**，不要暴露 8000、6379、11434
2. **Redis 必须有密码**，且 bind 127.0.0.1
3. **Ollama 不用动**，默认只监听 127.0.0.1
4. SSL 证书到期前 certbot 会自动续，前提是 80 端口能通
5. API Key 走 HTTPS 加密传输，服务器不落盘存储
