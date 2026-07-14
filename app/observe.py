"""
路由决策 → 调用 Python logging 输出到滚动日志文件(由 channel.py 装 TimedRotatingFileHandler)。

注意:此处不再写独立 jsonl 文件 —— 决策作为结构化字段融到主日志流,前端日志查看器一站式浏览。
绝不存原始 prompt,只存 preview[:N chars]。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .policy import PolicyStep

logger = logging.getLogger("autorouter.observe")

# 截多少字符的 query preview 写到日志(只前 N 字,完整 prompt 不进日志)
_PREVIEW_CHARS = 80


@dataclass
class Observer:
    """路由决策 → logger.info。每条决策打 1 行。"""

    def record(self, *, strategy: str, session_key: str, msg_preview: str,
               rule_idx: int, model: str, confidence: float, source: str,
               steps: list[PolicyStep]) -> None:
        """一次决策一条 INFO 级别日志。"""
        policies = ",".join(f"{s.name}{'*' if s.fired else ''}" for s in steps)
        logger.info(
            "route.decision strategy=%s session=%s rule=%s model=%s conf=%.2f src=%s preview=%r policies=%s",
            strategy,
            (session_key or "")[:8],
            rule_idx, model, confidence, source,
            (msg_preview or "")[:_PREVIEW_CHARS],
            policies,
        )