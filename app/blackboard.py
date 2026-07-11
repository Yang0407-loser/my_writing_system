import json
import redis
from .config import settings


class Blackboard:
    """基于 Redis 的状态黑板 + 流式事件推送 + 控制队列。

    每个写入任务对应一个 Redis Hash，key 格式为 task_id。
    Hash 字段包括：status, style, outline, draft, review, error。
    Stream 用于流式推送 token 和事件。
    List 用于检查点决策队列。
    """

    def __init__(self):
        self._redis = redis.Redis.from_url(settings.REDIS_BACKEND_URL)

    # ── Hash 操作 ──────────────────────────────────────────────

    def set(self, task_id: str, key: str, value: object) -> None:
        serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        self._redis.hset(task_id, key, serialized)

    def get(self, task_id: str, key: str) -> str | None:
        val = self._redis.hget(task_id, key)
        if val is not None:
            return val.decode("utf-8") if isinstance(val, bytes) else val
        return None

    def get_all(self, task_id: str) -> dict:
        data = self._redis.hgetall(task_id)
        result = {}
        for k, v in data.items():
            key = k.decode("utf-8") if isinstance(k, bytes) else k
            val = v.decode("utf-8") if isinstance(v, bytes) else v
            try:
                parsed = json.loads(val)
                if isinstance(parsed, (dict, list)):
                    result[key] = parsed
                else:
                    result[key] = val
            except (json.JSONDecodeError, TypeError):
                result[key] = val
        return result

    def delete(self, task_id: str) -> None:
        self._redis.delete(task_id)

    # ── Stream 操作 ────────────────────────────────────────────

    def stream_key(self, task_id: str) -> str:
        return f"{task_id}:stream"

    def xadd_event(self, task_id: str, event: dict) -> str:
        """向任务 Stream 推送一个事件。返回消息 ID。"""
        data = {}
        for k, v in event.items():
            data[k] = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        return self._redis.xadd(self.stream_key(task_id), data, maxlen=10000)

    def xread_events(self, task_id: str, last_id: str = "0-0", count: int = 100) -> list:
        """从 Stream 读取新事件。

        Returns:
            list of (msg_id, dict) tuples.
        """
        key = self.stream_key(task_id)
        try:
            result = self._redis.xread({key: last_id}, count=count, block=500)
        except Exception:
            return []

        events = []
        if result:
            for stream_name, messages in result:
                for msg_id, fields in messages:
                    decoded = {}
                    for k, v in fields.items():
                        key_str = k.decode("utf-8") if isinstance(k, bytes) else k
                        val_str = v.decode("utf-8") if isinstance(v, bytes) else v
                        try:
                            decoded[key_str] = json.loads(val_str)
                        except (json.JSONDecodeError, TypeError):
                            decoded[key_str] = val_str
                    msg_id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else msg_id
                    events.append((msg_id_str, decoded))
        return events

    def stream_trim(self, task_id: str, maxlen: int = 10000) -> None:
        self._redis.xtrim(self.stream_key(task_id), maxlen=maxlen)

    def stream_delete(self, task_id: str) -> None:
        self._redis.delete(self.stream_key(task_id))

    # ── 控制队列 ───────────────────────────────────────────────

    def decision_queue_key(self, task_id: str, phase: str) -> str:
        return f"{task_id}:{phase}_decision_queue"

    def push_decision(self, task_id: str, phase: str, decision: dict) -> None:
        """向决策队列推送一个消息。"""
        key = self.decision_queue_key(task_id, phase)
        self._redis.rpush(key, json.dumps(decision, ensure_ascii=False))

    def wait_for_decision(self, task_id: str, phase: str, timeout: int = 300) -> dict | None:
        """阻塞等待用户决策，超时返回 None。

        Args:
            task_id: 任务 ID
            phase: 阶段名 (如 'outline', 'section')
            timeout: 超时秒数，默认 300 (5 分钟)
        """
        key = self.decision_queue_key(task_id, phase)
        # 先消费可能已经存在的旧消息，再阻塞等待新消息
        result = self._redis.blpop(key, timeout=timeout)
        if result is None:
            return None
        _, raw = result
        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(raw_str)

    def pop_decision(self, task_id: str, phase: str) -> dict | None:
        """非阻塞读取决策队列中的第一个消息。"""
        key = self.decision_queue_key(task_id, phase)
        result = self._redis.lpop(key)
        if result is None:
            return None
        raw_str = result.decode("utf-8") if isinstance(result, bytes) else result
        return json.loads(raw_str)

    def clear_decision_queue(self, task_id: str, phase: str) -> None:
        self._redis.delete(self.decision_queue_key(task_id, phase))

    # ── 检查点 ──────────────────────────────────────────────────

    def checkpoint_key(self, task_id: str) -> str:
        return f"checkpoint:{task_id}"

    def save_checkpoint(self, task_id: str, state_dict: dict) -> None:
        """保存任务状态快照到 Redis Hash。"""
        key = self.checkpoint_key(task_id)
        mapping = {}
        for field, value in state_dict.items():
            mapping[field] = json.dumps(value, ensure_ascii=False, default=str)
        self._redis.hset(key, mapping=mapping)
        self._redis.expire(key, 86400)

    def load_checkpoint(self, task_id: str) -> dict | None:
        """从 Redis 加载任务状态快照。"""
        key = self.checkpoint_key(task_id)
        data = self._redis.hgetall(key)
        if not data:
            return None
        result = {}
        for k, v in data.items():
            field = k.decode("utf-8") if isinstance(k, bytes) else k
            val = v.decode("utf-8") if isinstance(v, bytes) else v
            try:
                result[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                result[field] = val
        return result

    def delete_checkpoint(self, task_id: str) -> None:
        self._redis.delete(self.checkpoint_key(task_id))

    # ── 通知队列（替代轮询） ───────────────────────────────────

    def _notify_key(self, task_id: str, channel: str) -> str:
        return f"{task_id}:notify:{channel}"

    def wait_for_notification(self, task_id: str, channel: str, timeout: int = 600) -> bool:
        """阻塞等待通知，超时返回 False。

        用于替代轮询——当等待的事件发生时，另一端 push_notification，
        这里立刻被唤醒。
        """
        key = self._notify_key(task_id, channel)
        result = self._redis.blpop(key, timeout=timeout)
        return result is not None

    def push_notification(self, task_id: str, channel: str) -> None:
        """推送通知，唤醒 wait_for_notification 的阻塞者。"""
        key = self._notify_key(task_id, channel)
        self._redis.rpush(key, "1")
        self._redis.expire(key, 30)  # 短 TTL，防止残留
