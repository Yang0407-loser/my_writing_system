from celery import Celery
from .config import settings

celery_app = Celery(
    "writing_tasks",
    broker=settings.REDIS_BROKER_URL,
    backend=settings.REDIS_BACKEND_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    # ── 容错与恢复 ──
    task_acks_late=True,                   # 任务执行完才确认，worker 崩溃时任务回到队列
    task_reject_on_worker_lost=True,
    task_default_retry_delay=30,           # 默认重试延迟 30s
    task_max_retries=5,                    # 默认最多重试 5 次
    task_soft_time_limit=1800,             # 30 分钟软超时 (SoftTimeLimitExceeded)
    task_time_limit=2400,                  # 40 分钟硬超时 (SIGKILL)
    task_acks_on_failure_or_timeout=False, # 失败/超时任务不确认，可被其他 worker 重拾
    broker_transport_options={
        'visibility_timeout': 3600,        # 1 小时，配合长任务
    },
    # ── 队列拆分 ──
    task_routes={
        'writing_task': {'queue': 'writing'},
        'writing_task_resume': {'queue': 'writing'},
    },
    task_create_missing_queues=True,
)

# 注册任务模块 — worker 启动时必须加载
from . import coordinator  # noqa: E402, F401
