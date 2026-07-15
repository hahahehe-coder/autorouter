"""
SessionStore — 内存字典,key=hash(auth),存上次 tier+时间戳。
anti_downgrade 用来判断"同一会话"的上一档。惰性淘汰 TTL 条目。
"""
from __future__ import annotations

import hashlib
import threading
import time


def compute_session_key(auth: str, messages: list) -> str:
    """相同认证信息视为同一会话；无认证请求不启用 anti_downgrade。"""
    if not auth:
        return ""
    h = hashlib.sha256()
    h.update(auth.encode("utf-8"))
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
            now = time.time()
            expired = [k for k, (_, ts) in self._data.items()
                       if now - ts > self._default_window]
            for old_key in expired:
                self._data.pop(old_key, None)
            self._data[key] = (idx, now)

    def size(self) -> int:
        with self._lock:
            return len(self._data)
