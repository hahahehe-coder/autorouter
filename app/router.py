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

from .config import Config, StrategyCfg, RuleCfg
from .features import extract as extract_features, _last_user_text
from .heuristic import classify_with_messages
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
    inference: dict[str, Any] = field(default_factory=dict)
    policies: list[PolicyStep] = field(default_factory=list)


# ============================ helpers ============================

def _last_user_text_from_messages(messages: list) -> str:
    return _last_user_text({"messages": messages})


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
        inference=dict(rule.inference),
    )


def _route_heuristic(strategy: StrategyCfg, body: dict, messages: list,
                     cfg: Config, session_key: str,
                     prev_idx: int | None = None) -> RoutingDecision:
    text = _last_user_text_from_messages(messages)
    idx, band, conf, material_tokens, has_image = classify_with_messages(text, messages)

    enabled = {
        "confidence_gate":    True,
        "complaint_upgrade":  cfg.policy.complaint_upgrade_enabled,
        "anti_downgrade":     cfg.policy.anti_downgrade_enabled,
    }
    ctx = PolicyCtx(
        rules_count=len(strategy.rules),
        previous_idx=prev_idx,
        message_text=text,
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
        source="heuristic",
        band=band,
        session_key=session_key,
        inference=dict(rule.inference),
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
    if kind == "static":
        return _route_static(strat)
    if kind == "heuristic":
        return _route_heuristic(strat, body, messages, cfg, session_key, prev_idx=prev_idx)
    raise ValueError(f"unknown strategy.kind '{kind}' for '{strategy_name}'")