"""
后处理链 — 按 rule index 工作,不再有 tier 概念。

简化版(去掉 capability_gate 和 large_context_floor,因为没有能力字段可读)。
执行顺序固定:
  confidence_gate → chitchat_only → complaint_upgrade → anti_downgrade
  → capability_gate → large_context_floor

每个策略是纯函数:(rule_idx_in, ctx) -> (rule_idx_out, fired, info)

ctx 字段:
  - rules_count: 当前策略的 rule 数(钳制用)
  - previous_idx: 上次会话命中的 rule 索引(anti_downgrade 用)
  - message_text: 短消息抱怨检测用
  - force: 跳过 anti_downgrade 的开关
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PolicyCtx:
    rules_count: int = 0
    previous_idx: int | None = None       # 上次会话命中的 rule index
    message_text: str = ""
    complaint_max_chars: int = 160
    force: bool = False
    # ML 置信度门(confidence_gate 用);启发式默认 confidence=1.0、threshold=0.0 → 不触发
    confidence: float = 1.0
    confidence_threshold: float = 0.0
    confidence_fallback_idx: int = -1     # -1 = rules_count-1
    # capability_gate / large_context_floor 用
    rules: list = field(default_factory=list)   # 该策略 rule 列表(读 .model 查注册表)
    material_tokens: int = 0
    has_image: bool = False
    # 模型能力注册表(name → ModelCfg),capability_gate 按名查 supports_vision/context_window
    model_caps: dict = field(default_factory=dict)
    # large_context_floor 阈值
    lc_t3_floor: int = 100000
    lc_t2_floor: int = 50000
    lc_t3_ratio: float = 0.8
    lc_context_window: int = 128000


@dataclass
class PolicyStep:
    name: str
    input_idx: int
    output_idx: int
    fired: bool
    info: str = ""


# ============================ 策略 ============================

def confidence_gate(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """OOB 钳制 + 低置信度回退。
    confidence_threshold=0.0(启发式默认)时第二段不触发,行为与旧版一致。"""
    if ctx.rules_count <= 0:
        return idx, False, "no rules"
    # OOB → 最后一档
    if idx >= ctx.rules_count:
        return ctx.rules_count - 1, True, f"idx {idx} OOB → fallback to last"
    # 低置信度 → 回退到 fallback 档(ML 模型不确定就给更强档)
    if ctx.confidence < ctx.confidence_threshold:
        fb = ctx.confidence_fallback_idx
        if fb < 0 or fb >= ctx.rules_count:
            fb = ctx.rules_count - 1
        return fb, True, f"low conf {ctx.confidence:.2f} < {ctx.confidence_threshold} → fallback {fb}"
    return idx, False, ""


def complaint_upgrade(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """短消息中的抱怨词 → 升一档(往后走一步)。"""
    terms = ["nonsense", "wrong", "useless", "terrible", "garbage", "stupid",
             "胡扯", "胡说", "废话", "没用", "错", "蠢"]
    text = (ctx.message_text or "").strip().lower()
    if not text or len(text) > ctx.complaint_max_chars:
        return idx, False, ""
    if not any(t in text for t in terms):
        return idx, False, ""
    if ctx.rules_count <= 0 or idx >= ctx.rules_count - 1:
        return idx, False, "already at top"
    return idx + 1, True, "complaint → upgrade"


# 纯闲聊短语(exact-match 集合,剥末尾标点后做整体匹配)
# 设计上"倒过来":默认 R0 都升 R1,只有文本 100% 等于一个真闲聊短语才留 R0。
# 这样"你好,帮我建模"、"hi, write me X"这种"招呼 + 任务"混合句被正确升档。
# 老的"包含 chitchat 词 + 没有 task 词"双条件设计有漏洞:任务词表不可能穷举,
# 例:"你好,帮我建模:30m t梁"中"建模"不在 _TASK_MARKERS 就误判成闲聊。
_CHITCHAT_PHRASES = {
    # 招呼
    "hi", "hello", "hey", "你好", "您好", "嗨", "哈喽",
    "how are you", "how do you do", "how's it going", "what's up",
    # 自我介绍类问题
    "你是谁", "你叫什么", "你叫什么名字", "介绍自己", "介绍一下自己", "自我介绍",
    "who are you", "what's your name", "introduce yourself",
    # 天气 / 寒暄
    "今天天气怎么样", "今天天气如何", "今天怎么样", "天气怎么样", "天气如何",
    "how's the weather",
    # 客套
    "thanks", "thank you", "ty", "thx", "谢谢", "多谢", "不客气",
    "thanks a lot", "thanks so much",
    # 确认 / 应答
    "ok", "okay", "好的", "嗯", "是的", "对", "收到",
    "yes", "no", "yeah", "yep", "nope", "sure",
}


def chitchat_only(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """R0 只留给"100% 纯闲聊,或闲聊短语 + 无分隔符短尾";其他从 R0 升 R1。

    判定:
    1. 文本剥末尾中英文标点后,等于纯闲聊短语 → R0
    2. 文本以闲聊短语开头,尾巴是 ≤3 个 alphanumeric(无空格/标点分隔符)
       → R0(吸收"你好0"、"你好啊"、"hello~"、"ok~"等typo/语气/轻微数字尾巴)
    3. 其他(含分隔符的混合句)→ 升 R1

    目的:R0 的 thinking=off 对"短但有点内容"的请求太弱,默认升到 R1(M3 + thinking 默认)
    让模型有思考余地。仅真闲聊走最便宜路径。
    """
    if idx != 0:
        return idx, False, ""
    text = (ctx.message_text or "").strip()
    if not text:
        return idx, False, ""
    if len(text) > 80:
        return 1, True, "len>80 → R1"
    norm = text.lower().rstrip("!.?,;:。!?～~")
    if not norm:
        return idx, False, ""
    if norm in _CHITCHAT_PHRASES:
        return idx, False, ""
    # prefix-match 兜底:容忍短尾巴(typo/语气/数字),但拒绝"你好,帮我..."这种分隔符混合
    for phrase in _CHITCHAT_PHRASES:
        if norm.startswith(phrase):
            tail = norm[len(phrase):]
            if not tail or (len(tail) <= 3 and tail.isalnum()):
                return idx, False, ""
            break   # 长尾 / 有分隔符 → 不当 chitchat,跳出
    return 1, True, "non-chitchat → R1"


def anti_downgrade(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """会话窗口内禁降档(锁住高档,护上游 KV cache)。"""
    if ctx.previous_idx is None or ctx.force or ctx.rules_count <= 0:
        return idx, False, ""
    if ctx.previous_idx < 0 or ctx.previous_idx >= ctx.rules_count:
        return idx, False, ""
    if ctx.previous_idx > idx:
        return ctx.previous_idx, True, f"locked by previous={ctx.previous_idx}"
    return idx, False, ""


def _cap(ctx: "PolicyCtx", model_name: str):
    """从模型注册表查某模型的能力(ModelCfg 或 None)。"""
    return ctx.model_caps.get(model_name) if ctx.model_caps else None


def capability_gate(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """硬能力约束:vision 缺失/上下文超窗 → 往上找能用的档。只在确定信号上动作(未知=None 不动)。
    能力按 rule.model 名从注册表查(不在 rule 上重复)。"""
    rules = ctx.rules
    if not rules or idx < 0 or idx >= len(rules):
        return idx, False, ""
    cur_cap = _cap(ctx, rules[idx].model)
    # vision_walk_up:有图且当前档模型确定不支持 vision → 找最近支持 vision 的更高档
    sv = getattr(cur_cap, "supports_vision", None) if cur_cap else None
    if ctx.has_image and sv is False:
        for j in range(idx + 1, len(rules)):
            c = _cap(ctx, rules[j].model)
            if getattr(c, "supports_vision", None) is True:
                return j, True, f"vision walk-up {idx}→{j}"
    # context_walk_up:material 超过当前档模型确定窗口 → 找最近能装下的;都没有就饱和顶档
    cw = getattr(cur_cap, "context_window", None) if cur_cap else None
    if ctx.material_tokens > 0 and cw is not None and ctx.material_tokens > cw:
        for j in range(idx + 1, len(rules)):
            c = _cap(ctx, rules[j].model)
            w = getattr(c, "context_window", None) if c else None
            if w is not None and ctx.material_tokens <= w:
                return j, True, f"context walk-up {idx}→{j} ({ctx.material_tokens}>{cw})"
        top = len(rules) - 1
        if top > idx:
            return top, True, f"context overflow → saturate top {idx}→{top}"
    return idx, False, ""


def large_context_floor(idx: int, ctx: PolicyCtx) -> tuple[int, bool, str]:
    """超大上下文强制高档(廉价模型装不下)。material_tokens 是粗估 token 数。"""
    if ctx.rules_count <= 0:
        return idx, False, ""
    top = ctx.rules_count - 1
    mt = ctx.material_tokens
    if mt >= ctx.lc_t3_floor or (ctx.lc_context_window and mt >= ctx.lc_context_window * ctx.lc_t3_ratio):
        if idx < top:
            return top, True, f"large ctx {mt} → top"
    elif mt >= ctx.lc_t2_floor:
        target = max(top - 1, 0)
        if idx < target:
            return target, True, f"large ctx {mt} → {target}"
    return idx, False, ""


# ============================ pipeline ============================

POLICY_ORDER = (
    ("confidence_gate",     confidence_gate),
    ("chitchat_only",       chitchat_only),
    ("complaint_upgrade",   complaint_upgrade),
    ("anti_downgrade",      anti_downgrade),
    ("capability_gate",     capability_gate),
    ("large_context_floor", large_context_floor),
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
