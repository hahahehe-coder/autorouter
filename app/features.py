"""
51 维手写特征 — 从 OpenSquilla 抄,纯 stdlib(正则 + 关键词计数)。

返回 dict[str, float] — 字段名就是维度编号,便于排查。
不做 np.array 包装因为这里只用连续值,dict 也够 inspect。
"""
from __future__ import annotations

import re

# ============================ 正则 ============================

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_JSON_RE = re.compile(r"\{[\s\S]*?[\"'][\w]+[\"']\s*:")
_YAML_RE = re.compile(r"^[\w_]+:\s+\S", re.MULTILINE)
_CSV_RE = re.compile(r"^[^,\n]+,[^,\n]+,[^,\n]+", re.MULTILINE)
_TABLE_RE = re.compile(r"\|.*\|.*\|")
_FILE_PATH_RE = re.compile(
    r"(?:^|[\s\"'`(])([a-zA-Z_][\w.-]*/[\w./-]+\.[\w]+)", re.MULTILINE,
)
_URL_RE = re.compile(r"https?://\S+")
_LOG_RE = re.compile(
    r"(\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}.*\n){3,}"
    r"|(^\[?(INFO|WARN|ERROR|DEBUG)\]?\s.*\n){3,}",
    re.MULTILINE,
)
_SHELL_RE = re.compile(r"^\$\s+\w|^>\s+\w|```(?:bash|sh|shell)", re.MULTILINE)
_TRACEBACK_RE = re.compile(r"Traceback \(most recent|stderr:|\.py\", line \d+")
_BULLET_RE = re.compile(r"^[\s]*[-*]\s", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^[\s]*\d+[.)]\s", re.MULTILINE)
_HAS_IMAGE_RE = re.compile(r"\"type\"\s*:\s*\"image_url\"", re.IGNORECASE)


# ============================ 关键词表(13 类) ============================

_DEBUG_KW = ["error", "bug", "exception", "traceback", "failed", "root cause",
             "报错", "根因", "修复", "stack trace", "debug"]
_RESEARCH_KW = ["调研", "research", "对比", "compare", "survey", "分析报告"]
_ARCH_KW = ["architecture", "架构", "重构", "refactor", "monorepo", "codebase"]
_COMPARE_KW = ["对比", "compare", "audit", "审计", "review", "评估"]
_PLANNING_KW = ["plan", "规划", "roadmap", "设计方案", "workflow", "pipeline", "步骤"]
_STRICT_FMT_KW = ["JSON", "YAML", "CSV", "schema", "只返回", "不要解释", "按格式"]
_HIGH_RISK_KW = ["deploy", "rollback", "migration", "delete", "production",
                 "生产", "部署", "删除", "客户", "法务"]
_PRODUCTION_KW = ["production", "生产", "prod", "线上", "正式环境"]
_CUSTOMER_KW = ["customer", "客户", "用户邮件", "client"]
_DELETE_KW = ["delete", "remove", "drop", "truncate", "删除", "清空", "覆盖", "overwrite"]
_FORMAL_KW = ["formal", "正式", "official", "公文", "合同", "法律"]
_CONSTRAINT_KW = ["必须", "不能", "不要", "只能", "must", "shall", "required"]
_TEACHING_KW = ["how does", "explain", "what is", "why does", "教我", "解释", "为什么"]
_IMPLEMENT_KW = ["implement", "write function", "写个", "实现", "用法", "帮我写"]


def _last_user_text(body: dict) -> str:
    """从 messages 抽最后一条 user 文本(支持 str 或 content[] 多模态)。"""
    for m in reversed(body.get("messages", []) or []):
        if m.get("role") != "user":
            continue
        c = m.get("content", "")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return ""


# ============================ helpers ============================

def _char_type_ratios(text: str) -> tuple[float, float, float]:
    if not text:
        return 0.0, 0.0, 0.0
    n = len(text)
    zh = sum(1 for c in text if "一" <= c <= "鿿")
    en = sum(1 for c in text if c.isascii() and c.isalpha())
    code = sum(1 for c in text if c in "{}[]();=<>|&!@#$%^*~`\\")
    return zh / n, en / n, code / n


def _keyword_count(text: str, keywords: list[str]) -> int:
    t = text.lower()
    return sum(1 for kw in keywords if kw.lower() in t)


def _approx_tokens(text: str) -> int:
    """粗估 token 数:中文字符 / 1.5,英文 word / 0.75,够 large_context 阈值用。"""
    if not text:
        return 0
    zh = sum(1 for c in text if "一" <= c <= "鿿")
    en = sum(1 for c in text if c.isascii() and c.isalpha() and c.isalpha())
    return int(zh / 1.5 + en / 0.75)


def _has_image_in_messages(messages) -> bool:
    """扫 messages,看 content[] 里有没有 image_url。"""
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


# ============================ 提特征主入口 ============================

def extract(text: str, messages: list | None = None) -> dict[str, float]:
    """
    从纯文本 (最后一条 user 消息) + 完整 messages 提特征。
    返回 51 维手写特征 + 上下文信号(has_image/material_tokens)。
    """
    text = text or ""
    f: dict[str, float] = {}

    # 0-3 Basic
    words = text.split()
    lines = text.split("\n")
    f["len_chars"] = float(len(text))
    f["len_words"] = float(len(words))
    f["len_lines"] = float(len(lines))
    f["avg_line_len"] = f["len_chars"] / max(f["len_lines"], 1)

    # 4-7 Language
    zh, en, code = _char_type_ratios(text)
    f["zh_ratio"] = zh
    f["en_ratio"] = en
    f["code_ratio"] = code
    f["zh_en_mix"] = 1.0 if (zh > 0.1 and en > 0.1) else 0.0

    # 8-14 Structure
    code_blocks = _CODE_BLOCK_RE.findall(text)
    f["has_code_block"] = 1.0 if code_blocks else 0.0
    f["code_block_count"] = float(len(code_blocks))
    f["code_block_chars"] = float(sum(len(b) for b in code_blocks))
    f["has_json"] = 1.0 if _JSON_RE.search(text) else 0.0
    f["has_yaml"] = 1.0 if _YAML_RE.search(text) else 0.0
    f["has_csv"] = 1.0 if _CSV_RE.search(text) else 0.0
    f["has_table"] = 1.0 if _TABLE_RE.search(text) else 0.0

    # 15-18 Punctuation
    f["qmark"] = float(text.count("?") + text.count("？"))
    f["emark"] = float(text.count("!") + text.count("！"))
    f["bullet_count"] = float(len(_BULLET_RE.findall(text)))
    f["numbered_count"] = float(len(_NUMBERED_RE.findall(text)))

    # 22-27 Keyword signals
    f["kw_debug"] = float(_keyword_count(text, _DEBUG_KW))
    f["kw_research"] = float(_keyword_count(text, _RESEARCH_KW))
    f["kw_arch"] = float(_keyword_count(text, _ARCH_KW))
    f["kw_compare"] = float(_keyword_count(text, _COMPARE_KW))
    f["kw_planning"] = float(_keyword_count(text, _PLANNING_KW))
    f["kw_strict_fmt"] = float(_keyword_count(text, _STRICT_FMT_KW))

    # 28-32 Risk
    f["kw_high_risk"] = float(_keyword_count(text, _HIGH_RISK_KW))
    f["kw_production"] = float(_keyword_count(text, _PRODUCTION_KW))
    f["kw_customer"] = float(_keyword_count(text, _CUSTOMER_KW))
    f["kw_delete"] = float(_keyword_count(text, _DELETE_KW))
    f["kw_formal"] = float(_keyword_count(text, _FORMAL_KW))

    # 33-37 File/tool
    f["has_file_path"] = 1.0 if _FILE_PATH_RE.search(text) else 0.0
    f["has_url"] = 1.0 if _URL_RE.search(text) else 0.0
    f["has_log"] = 1.0 if _LOG_RE.search(text) else 0.0
    f["has_shell"] = 1.0 if _SHELL_RE.search(text) else 0.0
    f["has_traceback"] = 1.0 if _TRACEBACK_RE.search(text) else 0.0

    # 38-40 Intensity
    f["kw_constraint"] = float(_keyword_count(text, _CONSTRAINT_KW))
    quoted = re.findall(r"[\"'`](.*?)[\"'`]", text)
    f["quote_ratio"] = sum(len(q) for q in quoted) / max(len(text), 1)
    words_lower = [w.lower() for w in words]
    f["lex_diversity"] = len(set(words_lower)) / max(len(words_lower), 1)

    # 41-50 R1-specific
    f["kw_teaching"] = float(_keyword_count(text, _TEACHING_KW))
    f["kw_implement"] = float(_keyword_count(text, _IMPLEMENT_KW))
    file_refs = _FILE_PATH_RE.findall(text)
    n_files = len(set(file_refs))
    f["files_zero"] = 1.0 if n_files == 0 else 0.0
    f["files_one_two"] = 1.0 if 1 <= n_files <= 2 else 0.0
    f["files_three_plus"] = 1.0 if n_files >= 3 else 0.0
    has_debug_kw = _keyword_count(text, _DEBUG_KW) > 0
    f["code_without_debug"] = 1.0 if (code_blocks and not has_debug_kw) else 0.0
    text_len = len(text)
    f["len_short"] = 1.0 if text_len < 200 else 0.0
    f["len_medium"] = 1.0 if 200 <= text_len <= 1000 else 0.0
    f["len_long"] = 1.0 if text_len > 1000 else 0.0
    total_kw = (f["kw_debug"] + f["kw_research"] + f["kw_arch"] + f["kw_compare"]
                + f["kw_planning"] + f["kw_strict_fmt"] + f["kw_high_risk"])
    f["kw_sparse"] = 1.0 if total_kw < 2 else 0.0

    # context signals(not part of the 51,但路由需要)
    f["has_image"] = 1.0 if _has_image_in_messages(messages) else 0.0
    # material_tokens:整个 messages 拼接粗估
    full_text = text
    if messages:
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, str):
                full_text += "\n" + c
    f["material_tokens"] = float(_approx_tokens(full_text))

    return f
