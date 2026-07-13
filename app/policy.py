"""
后处理链 — 按 rule index 工作,不再有 tier 概念。

简化版(去掉 capability_gate 和 large_context_floor,因为没有能力字段可读)。
执行顺序固定:
  confidence_gate → complaint_upgrade → anti_downgrade

每个策略是纯函数:(rule_idx_in, ctx) -> (rule_idx_out, fired, info)

ctx 字段:
  - rules_count: 当前策略的 rule 数(钳制用)
  - previous_idx: 上次会话命中的 rule 索引(anti_downgrade 用)
  - message_text: 短消息抱怨检测用
  - force: 跳过 anti_downgrade 的开关
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PolicyCtx:
    rules_count: int = 0
    previous_idx: int | None = None       # 上次会话命中的 rule index
    message_text: str = ""
    force: bool = False


@dataclass
class PolicyStep:
    name: str
    input_idx: int
    output_idx: int
    fired: bool
    info: str = ""


# ============================ 策略 ============================

def confidence_gate(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """
    当前没传 confidence 进 ctx(heuristic 还没接 ML)。
    保留接口,等 ML 上线后用真实概率。
    简化策略:任何 idx >= ctx.rules_count 都回退到最后一档。
    """
    if ctx.rules_count <= 0:
        return idx, False, "no rules"
    if idx >= ctx.rules_count:
        return ctx.rules_count - 1, True, f"idx {idx} OOB → fallback to last"
    return idx, False, ""


def complaint_upgrade(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """短消息中的抱怨词 → 升一档(往后走一步)。"""
    terms = ["nonsense", "wrong", "useless", "terrible", "garbage", "stupid",
             "胡扯", "胡说", "废话", "没用", "错", "蠢"]
    text = (ctx.message_text or "").strip().lower()
    if not text or len(text) > 160:
        return idx, False, ""
    if not any(t in text for t in terms):
        return idx, False, ""
    if ctx.rules_count <= 0 or idx >= ctx.rules_count - 1:
        return idx, False, "already at top"
    return idx + 1, True, "complaint → upgrade"


def anti_downgrade(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """会话窗口内禁降档(锁住高档,护上游 KV cache)。"""
    if ctx.previous_idx is None or ctx.force or ctx.rules_count <= 0:
        return idx, False, ""
    if ctx.previous_idx < 0 or ctx.previous_idx >= ctx.rules_count:
        return idx, False, ""
    if ctx.previous_idx > idx:
        return ctx.previous_idx, True, f"locked by previous={ctx.previous_idx}"
    return idx, False, ""


# ============================ pipeline ============================

POLICY_ORDER = (
    ("confidence_gate",    confidence_gate),
    ("complaint_upgrade",  complaint_upgrade),
    ("anti_downgrade",     anti_downgrade),
)


def run_pipeline(idx: int, ctx: PolicyCtx, enabled: dict) -> tuple[list[PolicyStep], int]:
    """按 POLICY_ORDER 依次执行,记录每步结果。返回 (steps, final_idx)。"""
    steps: list[PolicyStep] = []
    current = idx
    if ctx.rules_count > 0:
        current = max(0, min(current, ctx.rules_count - 1))
    for name, fn in POLICY_ORDER:
        if not enabled.get(name, True):
            steps.append(PolicyStep(name=name, input_idx=current, output_idx=current, fired=False, info="disabled"))
            continue
        out, fired, info = fn(current, ctx)
        steps.append(PolicyStep(name=name, input_idx=current, output_idx=out, fired=fired, info=info))
        current = out
    if ctx.rules_count > 0:
        current = max(0, min(current, ctx.rules_count - 1))
    return steps, current