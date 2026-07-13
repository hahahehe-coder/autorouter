"""
观测:结构化日志(默认) + 可选 JSONL 落盘(训练用)。
绝不存原始 prompt,只存 preview[:N chars] + 决策摘要。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .config import ObservabilityCfg
from .policy import PolicyStep

logger = logging.getLogger("autorouter.observe")


class Observer:
    def __init__(self, cfg: ObservabilityCfg):
        self.decision_log = cfg.decision_log
        self.preview_chars = cfg.log_preview_chars
        self._jsonl_path: Path | None = None
        self._lock = Lock()
        if cfg.jsonl_path:
            p = Path(cfg.jsonl_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = p
            logger.info(f"decision JSONL enabled: {p}")

    def record(self, *, strategy: str, session_key: str, msg_preview: str,
               rule_idx: int, model: str, confidence: float, source: str,
               steps: list[PolicyStep]) -> None:
        """一次决策一条记录。"""
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy,
            "session": session_key[:8] if session_key else None,
            "preview": msg_preview[: self.preview_chars],
            "rule_idx": rule_idx,
            "model": model,
            "confidence": round(confidence, 3),
            "source": source,
            "policies": [
                {"name": s.name, "in": s.input_idx, "out": s.output_idx,
                 "fired": s.fired, "info": s.info}
                for s in steps
            ],
        }
        if self.decision_log:
            logger.info(
                "route.decision strategy=%s rule=%s model=%s conf=%.2f src=%s policies=%s",
                strategy, rule_idx, model, confidence, source,
                ",".join(f"{s.name}{'*' if s.fired else ''}" for s in steps),
            )
        if self._jsonl_path:
            line = json.dumps(rec, ensure_ascii=False)
            with self._lock:
                with self._jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
