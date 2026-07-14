"""
启发式分类 — 输出 rule index (0-3)。

4 个 index,数组位置 = 模型能力顺序(弱→强):
  0 = trivial   (短文本,无代码)
  1 = medium    (中等文本,无代码;或长文本低置信度)
  2 = code      (有代码块)
  3 = heavy     (≥12000 字符 或 ≥3 个代码块)

边界:
  - "medium" 同时覆盖中等长度的纯文本(120-1200)和长文本 fallback(1200-12000)
  - confidence:trivial/medium/code/heavy = 0.55-0.60;long-text fallback = 0.40
"""
from __future__ import annotations

import re

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_FILE_PATH_RE = re.compile(
    r"(?:^|[\s\"'`(])([a-zA-Z_][\w.-]*/[\w./-]+\.[\w]+)", re.MULTILINE,
)

CONFIDENT = 0.60    # 高置信(heavy / code)
CONFIDENT_LOW = 0.55  # 中置信(trivial / medium)
BORDERLINE = 0.40    # 长文本 fallback → confidence_gate 会回退到最后一档


def classify(text: str) -> tuple[int, str, float]:
    """
    输入:最后一条 user 消息文本
    输出:(index 0-3, band 名字, confidence)
    """
    text = text or ""
    char_len = len(text)
    fenced_blocks = len(_CODE_BLOCK_RE.findall(text))
    has_code = fenced_blocks > 0 or len(_FILE_PATH_RE.findall(text)) > 0

    # heavy: 很长文本 或 多个代码块
    if char_len >= 12_000 or fenced_blocks >= 3:
        return 3, "heavy", CONFIDENT
    # code: 有代码块(任意长度)
    if has_code:
        return 2, "code", CONFIDENT
    # trivial: 短文本
    if char_len <= 240:
        return 0, "trivial", CONFIDENT_LOW
    # medium: 中等文本
    if char_len <= 1200:
        return 1, "medium", CONFIDENT_LOW
    # 长文本(1200-12000)无代码: fallback,低置信
    return 1, "medium", BORDERLINE


def context_signals(messages: list | None) -> tuple[int, bool]:
    """从 messages 算 (material_tokens 粗估, has_image)。
    给 large_context_floor / capability_gate 用 —— 无论 ML 还是启发式路径都调这个。"""
    full = ""
    for m in (messages or []):
        c = m.get("content", "") if isinstance(m, dict) else ""
        if isinstance(c, str):
            full += "\n" + c
    zh = sum(1 for ch in full if "一" <= ch <= "鿿")
    en = sum(1 for ch in full if ch.isascii() and ch.isalpha())
    material_tokens = int(zh / 1.5 + en / 0.75)
    has_image = False
    for m in (messages or []):
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    has_image = True
                    break
    return material_tokens, has_image


def classify_with_messages(text: str, messages: list | None) -> tuple[int, str, float, int, bool]:
    """
    扩展版:除文本外,还返回:
      - material_tokens:粗估整个 messages 的 token 数
      - has_image:消息里是否有图片
    """
    idx, band, conf = classify(text)
    material_tokens, has_image = context_signals(messages)
    return idx, band, conf, material_tokens, has_image