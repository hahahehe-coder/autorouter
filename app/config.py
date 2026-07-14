"""
配置 — 多文件加载、合并、跨文件校验、热重载。

策略 kind(路由模式):
  - single      → 单一 rule 对象(单模型,不分类)。旧名 static 作别名。
  - rule        → 启发式 band 分类器(长度/代码)输出 idx 0-3 → rules[idx]
  - classifier  → ML 分类器输出 idx 0-3 → rules[idx];ML 不可用回退 rule。旧名 heuristic 作别名。
  rule/classifier 都用 rules 数组(下标=分类器输出,0=trivial..3=heavy,越往后越强)。

模型能力(supports_vision/context_window)在 models section 注册表里按模型名集中管理,
不在 rule 上重复;capability_gate 按名查注册表。

模型引用约束:rule.model / rules[i].model 必须出现在 models 注册表的 key 里
(validate() 硬校验,空或未注册都拒绝加载)。
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
SECTIONS = ("connection", "strategies", "policy", "observability", "ml", "models")

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
    # chitchat_only:R0 只留给真闲聊(招呼/自我介绍/天气/客套),其他从 R0 升 R1。
    # 避免"短但有点内容"被 thinking=off 的 R0 吃瘪。
    chitchat_only_enabled: bool = True
    # capability_gate:含图片/上下文超窗 → 往上找能用的档(需 rule 标 supports_vision/context_window)
    capability_gate_enabled: bool = True
    # large_context_floor:超大上下文强制高档(廉价模型装不下)
    large_context_floor_enabled: bool = True
    lc_t3_floor_tokens: int = 100000      # ≥此值 → 强制顶档
    lc_t2_floor_tokens: int = 50000       # ≥此值 → 强制次高档
    lc_t3_context_ratio: float = 0.8      # 或 material ≥ ratio*context_window → 顶档
    lc_context_window: int = 128000       # 假定的最大窗口(给 ratio 用)
    # confidence_gate 保留接口但暂无可调参数(等接 ML 后用)


# ============================================================
# strategy section
# ============================================================

@dataclass
class RuleCfg:
    """单条 rule。rule/classifier 策略按数组下标匹配 classifier 输出。
    字段全是路由后要覆盖到请求 body 的项 —— model 跟其他字段一视同仁。
    没设的可选字段(max_tokens/system/thinking)在覆盖阶段被跳过,保留用户原值。
    模型能力(supports_vision/context_window)在 models 注册表里,不在 rule 上。
    """
    model: str = ""
    max_tokens: int | None = None
    system: str = ""
    thinking: str = ""


@dataclass
class StrategyCfg:
    """单个策略。"""
    name: str
    kind: str = "single"        # single | rule | classifier
    # single: 单 rule 对象
    rule: RuleCfg | None = None
    # rule / classifier: rule 数组
    rules: list[RuleCfg] = field(default_factory=list)


@dataclass
class StrategiesCfg:
    items: dict[str, StrategyCfg] = field(default_factory=dict)


# ============================================================
# models section(模型注册表 — 能力元数据,capability_gate 按名查)
# ============================================================

@dataclass
class ModelCfg:
    """单个模型的能力元数据。None=未知(capability_gate 不动)。"""
    supports_vision: bool | None = None
    context_window: int | None = None


@dataclass
class ModelsCfg:
    items: dict[str, ModelCfg] = field(default_factory=dict)


# ============================================================
# observability
# ============================================================

@dataclass
class ObservabilityCfg:
    """日志配置。只配 log_dir;滚动/切割由 Python logging TimedRotatingFileHandler 负责(midnight 切文件)。"""
    log_dir: str = "./log"


# ============================================================
# ml(可选 — 加载 OpenSquilla 预训练路由 bundle)
# ============================================================

@dataclass
class MLCfg:
    """ML 路由配置。默认 enabled=True,但依赖/bundle 缺失时自动降级到启发式。"""
    enabled: bool = True
    bundle_path: str = ""
    confidence_threshold: float = 0.5
    confidence_fallback_idx: int = -1   # -1 = rules_count-1(最强档);否则字面 idx
    warmup_on_load: bool = True


# ============================================================
# 合并后全量
# ============================================================

@dataclass
class Config:
    connection: ConnectionCfg = field(default_factory=ConnectionCfg)
    strategies: StrategiesCfg = field(default_factory=StrategiesCfg)
    policy: PolicyCfg = field(default_factory=PolicyCfg)
    observability: ObservabilityCfg = field(default_factory=ObservabilityCfg)
    ml: MLCfg = field(default_factory=MLCfg)
    models: ModelsCfg = field(default_factory=ModelsCfg)


# ============================================================
# 校验
# ============================================================

def validate(cfg: Config) -> None:
    if not cfg.strategies.items:
        raise ValueError("strategies must define at least one strategy")
    reg = cfg.models.items
    for n, s in cfg.strategies.items.items():
        if s.kind not in ("single", "rule", "classifier"):
            raise ValueError(f"strategy '{n}': kind must be single/rule/classifier, got '{s.kind}'")
        if s.kind == "single":
            if s.rule is None or not s.rule.model:
                raise ValueError(f"strategy '{n}': single requires rule.model")
            if s.rule.model not in reg:
                raise ValueError(
                    f"strategy '{n}': rule.model '{s.rule.model}' not in models registry "
                    f"(去「模型」tab 注册)"
                )
        else:  # rule / classifier
            if not s.rules:
                raise ValueError(f"strategy '{n}': {s.kind} requires non-empty rules array")
            for i, r in enumerate(s.rules):
                if not r.model:
                    raise ValueError(f"strategy '{n}': rules[{i}].model is required")
                if r.model not in reg:
                    raise ValueError(
                        f"strategy '{n}': rules[{i}].model '{r.model}' not in models registry "
                        f"(去「模型」tab 注册)"
                    )


# ============================================================
# YAML → dataclass 解析
# ============================================================

def _parse_rule(d: dict | None) -> RuleCfg:
    d = d or {}
    if "inference" in d:
        raise ValueError(
            "rule uses deprecated 'inference' field — use flat max_tokens/system/thinking on the rule directly"
        )
    mt = d.get("max_tokens")
    return RuleCfg(
        model=d.get("model", ""),
        max_tokens=int(mt) if mt is not None else None,
        system=d.get("system") or "",
        thinking=d.get("thinking") or "",
    )


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
    co = d.get("chitchat_only") or {}
    cg = d.get("capability_gate") or {}
    lc = d.get("large_context_floor") or {}
    return PolicyCfg(
        anti_downgrade_enabled=bool(ad.get("enabled", True)),
        anti_downgrade_window_seconds=int(ad.get("window_seconds", 600)),
        complaint_upgrade_enabled=bool(cu.get("enabled", True)),
        complaint_max_chars=int(cu.get("max_chars", 160)),
        chitchat_only_enabled=bool(co.get("enabled", True)),
        capability_gate_enabled=bool(cg.get("enabled", True)),
        large_context_floor_enabled=bool(lc.get("enabled", True)),
        lc_t3_floor_tokens=int(lc.get("t3_floor_tokens", 100000)),
        lc_t2_floor_tokens=int(lc.get("t2_floor_tokens", 50000)),
        lc_t3_context_ratio=float(lc.get("t3_context_ratio", 0.8)),
        lc_context_window=int(lc.get("context_window", 128000)),
    )


_KIND_ALIASES = {"static": "single", "heuristic": "classifier"}


def _parse_strategies(d: dict | None) -> StrategiesCfg:
    d = d or {}
    items: dict[str, StrategyCfg] = {}
    for name, sd in d.items():
        sd = sd or {}
        raw_kind = sd.get("kind", "single")
        kind = _KIND_ALIASES.get(raw_kind, raw_kind)   # 旧名 static/heuristic → single/classifier
        if kind == "single":
            items[name] = StrategyCfg(
                name=name, kind="single",
                rule=_parse_rule(sd.get("rule")),
            )
        else:  # rule / classifier
            raw_rules = sd.get("rules") or []
            items[name] = StrategyCfg(
                name=name, kind=kind,
                rules=[_parse_rule(r) for r in raw_rules],
            )
    return StrategiesCfg(items=items)


def _parse_models(d: dict | None) -> ModelsCfg:
    """models.yaml 顶层就是 {模型名: {supports_vision, context_window}}(无 'models:' 包装)。
    顶层任何 dict 值的键都被当作模型名;非 dict 值(如顶层是 list/str)忽略。"""
    d = d or {}
    items: dict[str, ModelCfg] = {}
    for name, md in d.items():
        if not isinstance(md, dict):
            continue
        sv = md.get("supports_vision")
        cw = md.get("context_window")
        items[name] = ModelCfg(
            supports_vision=None if sv is None else bool(sv),
            context_window=int(cw) if cw is not None else None,
        )
    return ModelsCfg(items=items)


def _parse_observability(d: dict | None) -> ObservabilityCfg:
    d = d or {}
    return ObservabilityCfg(
        log_dir=str(d.get("log_dir", "./log")),
    )


def _parse_ml(d: dict | None) -> MLCfg:
    d = d or {}
    return MLCfg(
        enabled=bool(d.get("enabled", True)),
        bundle_path=str(d.get("bundle_path", "")),
        confidence_threshold=float(d.get("confidence_threshold", 0.5)),
        confidence_fallback_idx=int(d.get("confidence_fallback_idx", -1)),
        warmup_on_load=bool(d.get("warmup_on_load", True)),
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
        ml=_parse_ml(present["ml"]),
        models=_parse_models(present["models"]),
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