"""
路由层 — 每策略按 rule index 工作。

  kind=static    → 直接用 strategy.rule
  kind=heuristic → classifier 输出 0-3 → strategy.rules[idx](OOB 回退到末位)

策略链输出 (tier/mode/tier_model_name → unified):
  - tier 字段保留为字符串(显示用),值是 "rule-{idx}"
  - model 是真实下游模型名
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import ml_router
from .config import Config, StrategyCfg, RuleCfg
from .features import extract as extract_features, _last_user_text
from .heuristic import classify_with_messages, context_signals
from .policy import POLICY_ORDER, PolicyCtx, PolicyStep, run_pipeline

logger = logging.getLogger("autorouter.router")


@dataclass
class RoutingDecision:
    strategy: str
    rule_idx: int                # -1 for static/passthrough
    rule_count: int
    model: str
    confidence: float
    source: str                  # static / heuristic / passthrough
    band: str = ""
    session_key: str = ""
    # 这个档位要覆盖到请求 body 的字段集合(model 必含,其它可选)
    fields: dict[str, Any] = field(default_factory=dict)
    policies: list[PolicyStep] = field(default_factory=list)


# ============================ helpers ============================

def _last_user_text_from_messages(messages: list) -> str:
    return _last_user_text({"messages": messages})


def _rule_fields(rule) -> dict:
    """从 RuleCfg 抽出'实际要覆盖'的字段:空值/None 跳过。
    model 必填;thinking 空串=原样透传(不进 fields),'off'=不思考(强制关闭)。"""
    f = {"model": rule.model}
    if rule.max_tokens is not None:
        f["max_tokens"] = rule.max_tokens
    if rule.system:
        f["system"] = rule.system
    if rule.thinking:
        f["thinking"] = rule.thinking
    return f


def _pick_rule(strategy: StrategyCfg, idx: int) -> RuleCfg | None:
    """heuristic 时按 idx 拿 rule;OOB 回退到最后一档。"""
    if not strategy.rules:
        return None
    if idx < 0 or idx >= len(strategy.rules):
        idx = len(strategy.rules) - 1
    return strategy.rules[idx]


# ============================ per-strategy router kinds ============================

def _route_static(strategy: StrategyCfg) -> RoutingDecision:
    rule = strategy.rule
    if rule is None or not rule.model:
        raise ValueError(f"strategy '{strategy.name}': static requires rule.model")
    return RoutingDecision(
        strategy=strategy.name,
        rule_idx=0,
        rule_count=1,
        model=rule.model,
        confidence=1.0,
        source="static",
        fields=_rule_fields(rule),
    )


def _route_bands(strategy: StrategyCfg, body: dict, messages: list,
                 cfg: Config, session_key: str,
                 prev_idx: int | None = None, *,
                 use_ml: bool = False) -> RoutingDecision:
    """rule / classifier 策略:分类成 idx 0-3 → rules[idx] + policy 链。
    use_ml=False(rule) → 启发式 band;use_ml=True(classifier) → ML,不可用回退启发式。"""
    text = _last_user_text_from_messages(messages)

    source = "heuristic"
    if use_ml:
        # ML 可用先走 ML;推理出错 ml_router.classify 返回 None → 自动回退启发式
        ml_result = ml_router.classify(text, messages) if ml_router.is_available() else None
        if ml_result is not None:
            idx, band, conf, _, _ = ml_result
            source = "ml"
        else:
            idx, band, conf, _, _ = classify_with_messages(text, messages)
    else:
        idx, band, conf, _, _ = classify_with_messages(text, messages)

    # 上下文信号(material_tokens/has_image)无论 ML 还是启发式都算一次,给 lc/capability 用
    material_tokens, has_image = context_signals(messages)

    enabled = {
        "confidence_gate":     True,
        "chitchat_only":       cfg.policy.chitchat_only_enabled,
        "complaint_upgrade":   cfg.policy.complaint_upgrade_enabled,
        "anti_downgrade":      cfg.policy.anti_downgrade_enabled,
        "capability_gate":     cfg.policy.capability_gate_enabled,
        "large_context_floor": cfg.policy.large_context_floor_enabled,
    }
    ctx = PolicyCtx(
        rules_count=len(strategy.rules),
        previous_idx=prev_idx,
        message_text=text,
        confidence=conf,
        confidence_threshold=cfg.ml.confidence_threshold,
        confidence_fallback_idx=cfg.ml.confidence_fallback_idx,
        rules=strategy.rules,
        material_tokens=material_tokens,
        has_image=has_image,
        model_caps=cfg.models.items,
        lc_t3_floor=cfg.policy.lc_t3_floor_tokens,
        lc_t2_floor=cfg.policy.lc_t2_floor_tokens,
        lc_t3_ratio=cfg.policy.lc_t3_context_ratio,
        lc_context_window=cfg.policy.lc_context_window,
    )
    steps, final_idx = run_pipeline(idx, ctx, enabled)

    rule = _pick_rule(strategy, final_idx)
    if rule is None or not rule.model:
        raise ValueError(f"strategy '{strategy.name}': rules empty or rule.model missing")

    return RoutingDecision(
        strategy=strategy.name,
        rule_idx=final_idx,
        rule_count=len(strategy.rules),
        model=rule.model,
        confidence=conf,
        source=source,
        band=band,
        session_key=session_key,
        fields=_rule_fields(rule),
        policies=steps,
    )


# ============================ 顶层 dispatch ============================

def route(strategy_name: str, body: dict, cfg: Config, *,
          messages: list, session_key: str = "",
          prev_idx: int | None = None) -> RoutingDecision:
    """顶层路由入口。channel.py 只调这个。"""
    strat = cfg.strategies.items.get(strategy_name)
    if strat is None:
        # 策略表里没有 → 原样回灌
        return RoutingDecision(
            strategy=strategy_name,
            rule_idx=-1,
            rule_count=0,
            model=body.get("model", strategy_name),
            confidence=0.0,
            source="passthrough",
        )

    kind = strat.kind
    if kind == "single":
        return _route_static(strat)
    if kind in ("rule", "classifier"):
        return _route_bands(strat, body, messages, cfg, session_key,
                            prev_idx=prev_idx, use_ml=(kind == "classifier"))
    raise ValueError(f"unknown strategy.kind '{kind}' for '{strategy_name}'")