"""
配置 — 多文件加载、合并、跨文件校验、热重载。

新 schema(无 tier 概念):
  - strategies 下的每个策略 = 一组 rule
  - kind=static   → 单一 rule 对象 {model, inference?}
  - kind=heuristic → rules 数组 [{model, inference?}, ...]
    - 数组下标 = classifier 输出索引(0=trivial, 1=medium, 2=code, 3=heavy)
    - 越往后能力越强,policy 沿数组往后走做升级
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("autorouter.config")

# 每个配置 section 一个 yaml 文件
SECTIONS = ("connection", "strategies", "policy", "observability")

# 必填;其余缺失用默认值
REQUIRED_SECTIONS = frozenset({"strategies"})


# ============================================================
# connection section
# ============================================================

@dataclass
class ServerCfg:
    host: str = "127.0.0.1"
    port: int = 3001


@dataclass
class NewApiCfg:
    base_url: str = "http://127.0.0.1:3000"
    api_key: str = ""


@dataclass
class ConnectionCfg:
    server: ServerCfg = field(default_factory=ServerCfg)
    new_api: NewApiCfg = field(default_factory=NewApiCfg)


# ============================================================
# policy section(简化版 — 没有 capability_gate / large_context_floor)
# ============================================================

@dataclass
class PolicyCfg:
    anti_downgrade_enabled: bool = True
    anti_downgrade_window_seconds: int = 600
    complaint_upgrade_enabled: bool = True
    complaint_max_chars: int = 160
    # confidence_gate 保留接口但暂无可调参数(等接 ML 后用)


# ============================================================
# strategy section
# ============================================================

@dataclass
class RuleCfg:
    """单条 rule。heuristic 策略按数组下标匹配 classifier 输出。"""
    model: str = ""
    inference: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyCfg:
    """单个策略。"""
    name: str
    kind: str = "static"        # static | heuristic
    # static: 单 rule 对象
    rule: RuleCfg | None = None
    # heuristic: rule 数组
    rules: list[RuleCfg] = field(default_factory=list)


@dataclass
class StrategiesCfg:
    items: dict[str, StrategyCfg] = field(default_factory=dict)


# ============================================================
# observability
# ============================================================

@dataclass
class ObservabilityCfg:
    decision_log: bool = True
    jsonl_path: str | None = None
    log_preview_chars: int = 80


# ============================================================
# 合并后全量
# ============================================================

@dataclass
class Config:
    connection: ConnectionCfg = field(default_factory=ConnectionCfg)
    strategies: StrategiesCfg = field(default_factory=StrategiesCfg)
    policy: PolicyCfg = field(default_factory=PolicyCfg)
    observability: ObservabilityCfg = field(default_factory=ObservabilityCfg)


# ============================================================
# 校验
# ============================================================

def validate(cfg: Config) -> None:
    if not cfg.strategies.items:
        raise ValueError("strategies must define at least one strategy")
    for n, s in cfg.strategies.items.items():
        if s.kind not in ("static", "heuristic"):
            raise ValueError(f"strategy '{n}': kind must be static or heuristic, got '{s.kind}'")
        if s.kind == "static":
            if s.rule is None or not s.rule.model:
                raise ValueError(f"strategy '{n}': static requires rule.model")
        else:  # heuristic
            if not s.rules:
                raise ValueError(f"strategy '{n}': heuristic requires non-empty rules array")
            for i, r in enumerate(s.rules):
                if not r.model:
                    raise ValueError(f"strategy '{n}': rules[{i}].model is required")


# ============================================================
# YAML → dataclass 解析
# ============================================================

def _parse_rule(d: dict | None) -> RuleCfg:
    d = d or {}
    return RuleCfg(model=d.get("model", ""), inference=d.get("inference") or {})


def _parse_connection(d: dict | None) -> ConnectionCfg:
    d = d or {}
    s = d.get("server") or {}
    n = d.get("new_api") or {}
    return ConnectionCfg(
        server=ServerCfg(host=s.get("host", "127.0.0.1"), port=int(s.get("port", 3001))),
        new_api=NewApiCfg(base_url=n.get("base_url", "http://127.0.0.1:3000"), api_key=n.get("api_key", "")),
    )


def _parse_policy(d: dict | None) -> PolicyCfg:
    d = d or {}
    ad = d.get("anti_downgrade") or {}
    cu = d.get("complaint_upgrade") or {}
    return PolicyCfg(
        anti_downgrade_enabled=bool(ad.get("enabled", True)),
        anti_downgrade_window_seconds=int(ad.get("window_seconds", 600)),
        complaint_upgrade_enabled=bool(cu.get("enabled", True)),
        complaint_max_chars=int(cu.get("max_chars", 160)),
    )


def _parse_strategies(d: dict | None) -> StrategiesCfg:
    d = d or {}
    items: dict[str, StrategyCfg] = {}
    for name, sd in d.items():
        sd = sd or {}
        kind = sd.get("kind", "static")
        if kind == "static":
            items[name] = StrategyCfg(
                name=name, kind="static",
                rule=_parse_rule(sd.get("rule")),
            )
        else:
            raw_rules = sd.get("rules") or []
            items[name] = StrategyCfg(
                name=name, kind="heuristic",
                rules=[_parse_rule(r) for r in raw_rules],
            )
    return StrategiesCfg(items=items)


def _parse_observability(d: dict | None) -> ObservabilityCfg:
    d = d or {}
    return ObservabilityCfg(
        decision_log=bool(d.get("decision_log", True)),
        jsonl_path=d.get("jsonl_path"),
        log_preview_chars=int(d.get("log_preview_chars", 80)),
    )


# ============================================================
# 多文件加载入口
# ============================================================

def _yaml_load(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_section(name: str, config_dir: Path) -> dict | None:
    return _yaml_load(config_dir / f"{name}.yaml")


def load_all(config_dir: Path | str | None = None) -> Config:
    if config_dir is None:
        config_dir = Path(os.getenv("CONFIG_DIR", "config"))
    config_dir = Path(config_dir)

    for sec in REQUIRED_SECTIONS:
        if not (config_dir / f"{sec}.yaml").exists():
            raise FileNotFoundError(f"Required config file missing: {config_dir / (sec + '.yaml')}")

    present = {sec: load_section(sec, config_dir) for sec in SECTIONS}

    cfg = Config(
        connection=_parse_connection(present["connection"]),
        strategies=_parse_strategies(present["strategies"]) if present["strategies"] else StrategiesCfg(),
        policy=_parse_policy(present["policy"]),
        observability=_parse_observability(present["observability"]),
    )
    validate(cfg)
    return cfg


def save_section(name: str, data: dict, config_dir: Path | str | None = None) -> None:
    if config_dir is None:
        config_dir = Path(os.getenv("CONFIG_DIR", "config"))
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{name}.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)