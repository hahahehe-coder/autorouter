"""
SessionStore — 内存字典,key=hash(auth+msg_prefix),存上次 tier+时间戳。
anti_downgrade 用来判断"同一会话"的上一档。惰性淘汰 TTL 条目。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time


def compute_session_key(auth: str, messages: list) -> str:
    """
    OpenSquilla 风格:hash auth + messages 前缀(去掉最后 2 条)。
    前缀相同 = 同一会话 — 比纯 api_key 准(同一用户多会话可区分)。
    """
    h = hashlib.sha256()
    h.update((auth or "").encode("utf-8"))
    h.update(b"|")
    if isinstance(messages, list) and len(messages) > 0:
        prefix = messages[:-2] if len(messages) > 2 else []
        h.update(json.dumps(prefix, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()[:16]


class SessionStore:
    def __init__(self, default_window_seconds: int = 600):
        self._lock = threading.Lock()
        self._data: dict[str, tuple[int, float]] = {}   # key -> (rule_idx, ts)
        self._default_window = default_window_seconds

    def get_previous_idx(self, key: str, window_seconds: int | None = None) -> int | None:
        """返回 window 秒内记录的上一档 rule index;超期返回 None。"""
        if not key:
            return None
        win = window_seconds if window_seconds is not None else self._default_window
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            idx, ts = entry
            if time.time() - ts > win:
                self._data.pop(key, None)
                return None
            return idx

    def record(self, key: str, idx: int) -> None:
        if not key or idx < 0:
            return
        with self._lock:
            self._data[key] = (idx, time.time())

    def size(self) -> int:
        with self._lock:
            return len(self._data)
