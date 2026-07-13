# OpenSquilla 智能路由实现方法（参考文档）

> **目的**：本文档描述 OpenSquilla（位于 `d:/code/opensquilla`）的省 token 智能路由实现方法，作为实现类似路由系统的参考。所有路径都标注好了，可以直接 Read 对应文件看细节。

---

## 0. 路由系统总览

OpenSquilla 的路由系统由 4 层组成，每层都是独立的：

1. **L1 启发式路由**（纯 stdlib，零依赖）— 见第 5 节
2. **L2 4 档路由架构**（C0/C1/C2/C3）— 见第 1 节
3. **L3 后处理策略链**（6 个有序策略）— 见第 3 节
4. **L4 ML 分类器**（LightGBM + BGE + 手写特征）— 见第 2 节

**关键设计**：L1 是 fallback，L4 是主路径。两者**输出格式一致**，可以无缝切换。

---

## 1. 4 档路由架构

### 1.1 档位定义

参考 [`d:/code/opensquilla/src/opensquilla/router_tiers.py:9-19`](d:/code/opensquilla/src/opensquilla/router_tiers.py#L9-L19)：

```python
TEXT_TIERS = ("c0", "c1", "c2", "c3")
DEFAULT_TEXT_TIER = "c1"
HIGHEST_TEXT_TIER = "c3"
```

**为什么是 4 档**：
- 3 档太粗：默认档什么都接，简单问题也烧推理档
- 5 档边际收益低：4 档已经覆盖 80% 场景
- OpenSquilla 跑了 25 个任务 benchmark 才定 4 档

### 1.2 TierConfig 数据结构

参考 [`router_tiers.py:95-129`](d:/code/opensquilla/src/opensquilla/router_tiers.py#L95-L129)：

```python
@dataclass(frozen=True)
class TierConfig:
    provider: str = ""
    model: str = ""
    description: str = ""
    thinking_level: str | None = None  # "low" / "medium" / "high"
    supports_image: bool = False
    image_only: bool = False
```

### 1.3 档位 YAML 配置

参考 [`router.runtime.yaml`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/router.runtime.yaml)：

```yaml
route_classes: [R0, R1, R2, R3]

tier_mapping:
  R0: S    # C0 - 廉价档（deepseek-flash）
  R1: M    # C1 - 默认档（deepseek-pro）
  R2: L    # C2 - 推理档（GLM-5.2）
  R3: XL   # C3 - 最强档（Opus-4）

tier_registry:
  S:  [deepseek/deepseek-v4-flash]
  M:  [deepseek/deepseek-v4-pro]
  L:  [z-ai/glm-5.2]
  XL: [anthropic/claude-opus-4.8]

tier_explanations:
  S:
    intent: "Fast direct answers for trivial turns, acknowledgements, simple rewrites"
  M:
    intent: "General-purpose default for routine product, coding, writing tasks"
  L:
    intent: "Reasoning tier for debugging, multi-step analysis"
  XL:
    intent: "Highest tier for architecture, high-risk work, hard recovery"
```

**注意**：`tier_registry` 同一档可以列多个候选模型做 fallback，但**同档内 fallback 也会抖 cache**（详见第 4 节）。

---

## 2. ML 路由（V4 Phase 3）

### 2.1 8 通道 390 维特征向量

参考 [`models/v4.2_phase3_inference/runtime_src/src/router/inference/features.py:14-68`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/inference/features.py#L14-L68)：

```
[0:51]      HC (handcrafted)                  51 维
[51:151]    TFIDF + TruncatedSVD             100 维
[151:161]   ctx (context metadata)            10 维
[161:177]   hist (history route decisions)    16 维
[177:369]   BGE × 3 channels × PCA(64)      192 维  (user_curr + user_hist + asst)
[369:381]   asst_hc (assistant handcrafted)   12 维
[381:383]   cont_hc (continuation cues)        2 维
[383:388]   reasoning_hc                       5 维
            -------------------------------------------
            TOTAL                              390 维
```

### 2.2 51 维手写特征（最重要，可独立使用）

参考 [`runtime_src/src/router/features.py:220-310`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/features.py#L220-L310)：

**纯正则 + 关键词，不需要训练**，这是 OpenSquilla 路由最值钱的部分之一：

| 维度范围 | 内容 |
| --- | --- |
| 0-3 | 文本长度（字符、词、行、平均行长） |
| 4-7 | 语言（中文比、英文比、代码符号比、中英混合 flag） |
| 8-14 | 结构（代码块标记、JSON/YAML/CSV/表格 标记） |
| 15-18 | 标点（问号、感叹号、列表项数） |
| 22-27 | 关键词信号（debug、调研、架构、对比、规划、严格格式） |
| 28-32 | 风险信号（生产、删除、客户、法务） |
| 33-37 | 文件/工具（文件路径、URL、日志、shell、traceback） |
| 38-40 | 强度（约束词、引号占比、词汇多样性） |
| 41-50 | R1 专项信号（教学意图、实现意图、文件引用数 bucket、长度 bucket） |

**13 类中英双语关键词表**（[`features.py:46-65`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/features.py#L46-L65)）：

```python
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
```

**完整代码抄录**（参考 [`features.py:220-310`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/features.py#L220-L310)）：

```python
import re
import numpy as np

# 正则
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

# 关键词表（13 类，详见上面）
_DEBUG_KW = [...]
_RESEARCH_KW = [...]
# ... 其他 11 类

def _char_type_ratios(text: str) -> tuple[float, float, float]:
    if not text:
        return 0.0, 0.0, 0.0
    n = len(text)
    zh = sum(1 for c in text if "一" <= c <= "鿿")
    en = sum(1 for c in text if c.isascii() and c.isalpha())
    code = sum(1 for c in text if c in "{}[]();=<>|&!@#$%^*~`\\")
    return zh / n, en / n, code / n

def _keyword_count(text: str, keywords: list[str]) -> int:
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)

HANDCRAFTED_DIMS = 51

def extract_handcrafted(text: str) -> np.ndarray:
    """返回 51 维手写特征向量"""
    feats = np.zeros(HANDCRAFTED_DIMS, dtype=np.float32)
    
    # Basic (0-3)
    feats[0] = len(text)
    words = text.split()
    feats[1] = len(words)
    lines = text.split("\n")
    feats[2] = len(lines)
    feats[3] = feats[0] / max(feats[2], 1)
    
    # Language (4-7)
    zh, en, code = _char_type_ratios(text)
    feats[4] = zh
    feats[5] = en
    feats[6] = code
    feats[7] = 1.0 if (zh > 0.1 and en > 0.1) else 0.0
    
    # Structure (8-14)
    code_blocks = _CODE_BLOCK_RE.findall(text)
    feats[8] = 1.0 if code_blocks else 0.0
    feats[9] = len(code_blocks)
    feats[10] = sum(len(b) for b in code_blocks)
    feats[11] = 1.0 if _JSON_RE.search(text) else 0.0
    feats[12] = 1.0 if _YAML_RE.search(text) else 0.0
    feats[13] = 1.0 if _CSV_RE.search(text) else 0.0
    feats[14] = 1.0 if _TABLE_RE.search(text) else 0.0
    
    # Punctuation (15-18)
    feats[15] = text.count("?") + text.count("？")
    feats[16] = text.count("!") + text.count("！")
    feats[17] = len(_BULLET_RE.findall(text))
    feats[18] = len(_NUMBERED_RE.findall(text))
    
    # Keyword signals (22-27)
    feats[22] = _keyword_count(text, _DEBUG_KW)
    feats[23] = _keyword_count(text, _RESEARCH_KW)
    feats[24] = _keyword_count(text, _ARCH_KW)
    feats[25] = _keyword_count(text, _COMPARE_KW)
    feats[26] = _keyword_count(text, _PLANNING_KW)
    feats[27] = _keyword_count(text, _STRICT_FMT_KW)
    
    # Risk (28-32)
    feats[28] = _keyword_count(text, _HIGH_RISK_KW)
    feats[29] = _keyword_count(text, _PRODUCTION_KW)
    feats[30] = _keyword_count(text, _CUSTOMER_KW)
    feats[31] = _keyword_count(text, _DELETE_KW)
    feats[32] = _keyword_count(text, _FORMAL_KW)
    
    # File/tool (33-37)
    feats[33] = 1.0 if _FILE_PATH_RE.search(text) else 0.0
    feats[34] = 1.0 if _URL_RE.search(text) else 0.0
    feats[35] = 1.0 if _LOG_RE.search(text) else 0.0
    feats[36] = 1.0 if _SHELL_RE.search(text) else 0.0
    feats[37] = 1.0 if _TRACEBACK_RE.search(text) else 0.0
    
    # Intensity (38-40)
    feats[38] = _keyword_count(text, _CONSTRAINT_KW)
    quoted = re.findall(r"[\"'`](.*?)[\"'`]", text)
    feats[39] = sum(len(q) for q in quoted) / max(len(text), 1)
    words_lower = [w.lower() for w in words]
    feats[40] = len(set(words_lower)) / max(len(words_lower), 1)
    
    # R1-specific signals (41-50)
    feats[41] = _keyword_count(text, _TEACHING_KW)
    feats[42] = _keyword_count(text, _IMPLEMENT_KW)
    file_refs = _FILE_PATH_RE.findall(text)
    n_files = len(set(file_refs))
    feats[43] = 1.0 if n_files == 0 else 0.0
    feats[44] = 1.0 if 1 <= n_files <= 2 else 0.0
    feats[45] = 1.0 if n_files >= 3 else 0.0
    has_debug = _keyword_count(text, _DEBUG_KW) > 0
    feats[46] = 1.0 if (code_blocks and not has_debug) else 0.0
    text_len = len(text)
    feats[47] = 1.0 if text_len < 200 else 0.0
    feats[48] = 1.0 if 200 <= text_len <= 1000 else 0.0
    feats[49] = 1.0 if text_len > 1000 else 0.0
    total_kw = (feats[22] + feats[23] + feats[24] + feats[25]
                + feats[26] + feats[27] + feats[28])
    feats[50] = 1.0 if total_kw < 2 else 0.0
    
    return feats
```

### 2.3 12 维 assistant 特征（上一轮状态信号）

参考 [`runtime_src/src/router/v4_features.py:75-111`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/v4_features.py#L75-L111)：

**核心创新**：把上一轮模型回答的状态作为这一轮路由的输入。

```python
_RE_CLAR = re.compile(
    r"(?:能否|请\s*提供|需要(?:更多|具体).{0,8}信息"
    r"|could you (?:clarify|provide)|please (?:specify|provide)|clarify which)",
    re.I,
)
_RE_REFUSAL = re.compile(
    r"(?:I cannot|I can't help|对不起.{0,5}无法|抱歉.{0,5}不能"
    r"|作为(?:AI|大语言模型))",
    re.I,
)
_RE_SELF_DOUBT = re.compile(
    r"(?:我不(?:确定|清楚)|可能(?:不太|不一定)"
    r"|not sure|might not be|I'm not entirely)",
    re.I,
)

def extract_assistant_handcrafted(prev_assistant_text, prev_assistant_usage, current_user_text):
    """返回 12 维 assistant 信号向量"""
    if prev_assistant_text is None:
        return np.zeros(12, dtype=np.float32)
    t = prev_assistant_text
    u = prev_assistant_usage or {}
    return np.array([
        1.0,                                                      # 0: has_prev_asst
        float(_RE_CLAR.search(t) is not None),                    # 1: has_clarification
        float(_RE_REFUSAL.search(t) is not None),                 # 2: has_refusal
        float(_RE_SELF_DOUBT.search(t) is not None),              # 3: self_doubt
        float("```" in t or re.search(r"`[^`]{4,}`", t)),         # 4: has_code_block
        float(re.search(r"^\s*\d+[\.、]\s", t, re.M) is not None), # 5: has_steps_list
        np.log1p(u.get("output_tokens", 0) or 0) / 10.0,          # 6: log_output_tokens
        np.log1p(u.get("reasoning_tokens", 0) or 0) / 10.0,       # 7: log_reasoning_tokens
        np.log1p(u.get("duration_ms", 0) or 0) / 10.0,            # 8: log_duration_ms
        min(len(t) / max(len(current_user_text), 1), 5.0) / 5.0,  # 9: ans_user_ratio
        sum(1 for c in t if "一" <= c <= "鿿") / max(len(t), 1),  # 10: zh_ratio
        (u.get("cached_tokens", 0) or 0) / max(u.get("input_tokens", 1) or 1, 1),  # 11: cache_ratio
    ], dtype=np.float32)
```

### 2.4 192 维 BGE 嵌入（可选）

参考 [`v4_features.py:185-316`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/v4_features.py#L185-L316)：

```python
class BGEChannelExtractor:
    """BGE encoder + 共享 PCA(64) for three text segments.
    
    三段文本：[current_user, history_user, prev_assistant]
    每段单独 BGE encode → PCA(64) → concat = 192 维
    """
```

**三段文本拼接策略**（[`v4_features.py:160-178`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/v4_features.py#L160-L178)）：

```python
def make_history_user_text(prior_user_turns, max_turns=4, max_chars=1500):
    """拼接最多 max_turns 轮历史用户文本"""
    if not prior_user_turns:
        return ""
    selected = list(prior_user_turns[-max_turns:])
    text = "\n[SEP]\n".join(selected)
    # 超长时从前面截断（丢最早的）
    while len(text) > max_chars and len(selected) > 1:
        selected = selected[1:]
        text = "\n[SEP]\n".join(selected)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text
```

### 2.5 TFIDF 通道（100 维）

参考 [`features.py:338-355`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/features.py#L338-L355)：

```python
TfidfVectorizer(
    analyzer="char_wb",      # ← 字符 n-gram，不是词 n-gram
    ngram_range=(2, 4),      # 2-4 字符组合
    max_features=10000,
    sublinear_tf=True,
)
TruncatedSVD(n_components=100, random_state=42)
```

**关键 trick**：`analyzer="char_wb"` 对中文/日文等无空格语言更鲁棒。

### 2.6 模型训练

参考 [`v4.2_phase3_inference/runtime_src/src/router/predictor.py:264-332`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/predictor.py#L264-L332)：

```python
import lightgbm as lgb

# 训练数据准备
X = np.array([extract_handcrafted(q) for q in queries])  # 51 维纯规则
# 或完整 390 维
X_full = np.array([extract_390_dim(q) for q in queries])

y = np.array(labels)  # 0=R0, 1=R1, 2=R2, 3=R3

train_data = lgb.Dataset(X, label=y)
params = {
    'objective': 'multiclass',
    'num_class': 4,
    'learning_rate': 0.05,
    'num_leaves': 31,
    'feature_fraction': 0.9,
    'metric': 'multi_logloss',
}
model = lgb.train(params, train_data, num_boost_round=200)

# 推理
probs = model.predict(features.reshape(1, -1))[0]  # [P(R0), P(R1), P(R2), P(R3)]
```

### 2.7 推理时的 Bundle 加载

参考 [`v4_phase3.py:62-131`](d:/code/opensquilla/src/opensquilla/squilla_router/v4_phase3.py#L62-L131)：

```python
import joblib
import lightgbm as lgb
import onnxruntime as ort
from tokenizers import Tokenizer

BUNDLE = "models/v4.2_phase3_inference"

# 加载所有 fit 好的组件
lgbm = lgb.Booster(model_file=f"{BUNDLE}/lgbm_main.bin")
tfidf = joblib.load(f"{BUNDLE}/features/tfidf.pkl")
svd = joblib.load(f"{BUNDLE}/features/svd.pkl")
bge_pca = joblib.load(f"{BUNDLE}/features/bge_pca.joblib")
bge_sess = ort.InferenceSession(f"{BUNDLE}/bge_onnx/model.onnx")
tokenizer = Tokenizer.from_file(f"{BUNDLE}/bge_onnx/tokenizer.json")

# Bundle 总大小 ~70 MB，常驻内存 ~300 MB
```

---

## 3. 后处理策略链（6 个有序策略）

参考 [`d:/code/opensquilla/src/opensquilla/engine/routing/policy.py`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py)：

**关键原则**：策略**有固定顺序**，每个策略只能 hold 或升档或降档，**绝不取消前一档的效果**。

### 3.1 6 个策略一览

| # | 策略 | 作用 | 只能 |
| ---:| --- | --- | --- |
| 1 | confidence_gate | 低置信度 → 回落到默认档 | 降档 |
| 2 | complaint_upgrade | 短抱怨词 → 升档 | 升档 |
| 3 | anti_downgrade | 时间窗内 → 锁住高档 | 升档 |
| 4 | capability_gate | vision/上下文 → 升档 | 升档 |
| 5 | large_context_floor | 超大上下文 → 强制高档 | 升档 |
| 6 | budget_gate | 超预算 → 降档 | 降档 |

### 3.2 策略 1：confidence_gate

参考 [`policy.py:213-248`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L213-L248)：

```python
def confidence_gate(tier, *, confidence, threshold=0.5, 
                    high_tier_margin=0.05, default_tier="c1",
                    tiers=None, valid_tiers=None):
    """分类器置信度低时回落默认档"""
    base_threshold = threshold
    if default_tier is None:
        return tier
    
    tier_rank = _tier_index(tier, valid_tiers)
    default_rank = _tier_index(default_tier, valid_tiers)
    cutoff = threshold - high_tier_margin if tier_rank > default_rank else threshold
    
    if confidence < cutoff and tier_rank >= 0 and default_rank >= 0 and tier != default_tier:
        return default_tier
    return tier
```

**默认值**：`threshold=0.5`，高档 `cutoff=0.45`（因为高档容错稍低）

### 3.3 策略 2：complaint_upgrade

参考 [`policy.py:260-292`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L260-L292)：

```python
def detect_complaint(message: str, max_chars: int = 160) -> list[str]:
    """检测短消息中的抱怨词"""
    text = message.strip()
    if max_chars and len(text) > max_chars:
        return []
    lowered = text.lower()
    # 关键：只在 ≤160 字符的短消息中检测
    return [term for term in COMPLAINT_TERMS if term in lowered]

def complaint_upgrade(tier, *, message, steps=1, max_chars=160, 
                      pre_confidence_tier=None, previous_tier=None):
    """短抱怨词升档"""
    terms = detect_complaint(message, max_chars=max_chars)
    if not terms:
        return tier
    # 从 max(当前, pre_confidence, previous) 起步 +steps 档
    start_tier = tier
    if pre_confidence_tier in valid_tiers and _tier_index(pre_confidence_tier) > _tier_index(start_tier):
        start_tier = pre_confidence_tier
    if previous_tier in valid_tiers and _tier_index(previous_tier) > _tier_index(start_tier):
        start_tier = previous_tier
    return _upgrade_tier(start_tier, valid_tiers, steps)
```

**设计要点**：
- 只在 ≤160 字符检测（短消息才可能是抱怨）
- 升级起点是 max(当前, pre_confidence, previous)，不让 confidence_gate 后的回落掩盖抱怨
- 默认升 1 档

### 3.4 策略 3：anti_downgrade（核心！防 cache 抖动）

参考 [`policy.py:301-316`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L301-L316)：

```python
def anti_downgrade(tier, *, previous_tier, valid_tiers, 
                   kv_cache_anti_downgrade_enabled=True):
    """时间窗内禁止降档，保护 KV cache"""
    if (kv_cache_anti_downgrade_enabled
        and previous_tier in valid_tiers
        and _tier_index(previous_tier) > _tier_index(tier)):
        return previous_tier  # 锁住高档
    return tier
```

**默认窗口 600 秒**（[`policy.py:1021-1022`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L1021-L1022)）：

```python
window = float(getattr(router_cfg, "kv_cache_anti_downgrade_window_seconds", 600))
```

**设计哲学**：宁愿这轮多花点钱用更贵的模型（cache hit 便宜），也不愿为这"看似简单"的 query 切换模型丢掉 cache。

### 3.5 策略 4：capability_gate

参考 [`policy.py:346-427`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L346-L427)：

```python
def capability_gate(tier, *, valid_tiers, tier_capabilities, 
                    turn_has_image, material_tokens):
    """vision 缺失或上下文超出 → 向上走"""
    if not tier_capabilities:
        return tier
    
    # Rule 1: vision_walk_up
    if turn_has_image and _caps(current).supports_vision is False:
        for candidate in ordered[idx + 1:]:
            if _caps(candidate).supports_vision is True:
                return candidate  # 升到第一个支持 vision 的档
    
    # Rule 2: context_walk_up
    window = _caps(current).context_window
    if material_tokens > 0 and window is not None and material_tokens > window:
        for candidate in ordered[idx + 1:]:
            candidate_window = _caps(candidate).context_window
            if candidate_window is not None and material_tokens <= candidate_window:
                return candidate
        return ordered[-1]  # 都不够用 → 顶档
    return tier
```

**关键**：只在 catalog 有**明确信号**时才动作（`supports_vision is False` 而不是 `is None`），未知时不动。

### 3.6 策略 5：large_context_floor

参考 [`policy.py:534-587`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L534-L587)：

```python
LARGE_CONTEXT_T3_FLOOR_TOKENS = 100000  # ≥100k 强制 c3
LARGE_CONTEXT_T3_CONTEXT_RATIO = 0.8     # 或 ≥ 80% 窗口
LARGE_CONTEXT_T2_FLOOR_TOKENS = 50000   # ≥50k 强制 c2

def large_context_floor(decision, *, material_tokens, 
                        context_window_tokens, valid_tiers):
    """超大上下文强制高档（廉价模型装不下）"""
    if (material_tokens >= LARGE_CONTEXT_T3_FLOOR_TOKENS
        or material_tokens >= int(context_window_tokens * LARGE_CONTEXT_T3_CONTEXT_RATIO)):
        return "c3"
    if material_tokens >= LARGE_CONTEXT_T2_FLOOR_TOKENS:
        return "c2"
    return decision.tier
```

### 3.7 策略 6：budget_gate

参考 [`policy.py:628-757`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L628-L757)：

```python
def budget_gate(tier, *, budget):
    """超预算自动降档到 cap_tier"""
    if budget is None:
        return tier, "off"
    if budget.spend_usd is None:
        return tier, "suspended"  # ← 关键：未知花费时不行动
    projected = budget.spend_usd + (budget.estimate_usd or 0.0)
    if projected <= budget.limit_usd:
        return tier, "under_limit"
    if budget.action == "cap" and target < tier:
        return target, "cap"  # 降到 cap_tier
    return tier, "warn"  # 只警告，不降档
```

**关键原则**：
- **只能 hold 或降档，不能升档**（防止预算门盖过前面的能力门）
- `spend_usd is None` → `suspended`，**绝不在未知数据上行动**
- 无可用降档目标 → `warn` 而不是 raise

### 3.8 Pipeline 顺序

参考 [`policy.py:941-994`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L941-L994)：

```python
class RoutingPolicyEngine:
    def run(self, inputs):
        decision = inputs.decision
        
        # 1. 后处理链（顺序固定）
        decision = self._finalize(decision, inputs, extra)
        #    内部顺序：confidence_gate → complaint_upgrade 
        #           → anti_downgrade → capability_gate → bind
        
        # 2. 大上下文兜底
        decision = large_context_floor(...)
        
        # 3. 预算门（永远最后）
        decision = apply_budget_gate(...)
        
        return PolicyResult(decision, ...)
```

**为什么这个顺序**：
- confidence_gate 必须先（决定后续是否升档的起点）
- complaint_upgrade 必须在 anti_downgrade 前（抱怨立即升档，不能被锁住）
- capability_gate 在 bind 前（决定 final_tier）
- large_context_floor 在 budget_gate 前（能力优先于预算）
- budget_gate 永远最后（绝不盖过前面的能力决策）

---

## 4. Cache 抖动防护

### 4.1 CacheBreakMonitor

参考 [`d:/code/opensquilla/src/opensquilla/engine/cache_break_monitor.py`](d:/code/opensquilla/src/opensquilla/engine/cache_break_monitor.py)：

```python
import hashlib
import json
from dataclasses import dataclass

class CacheBreakMonitor:
    """追踪 cache 抖动并归因到具体的 prompt state 变化"""
    
    def __init__(self, *, min_drop_tokens=2000, min_drop_ratio=0.05):
        self._baselines = {}  # session_key -> (snapshot, cache_read_tokens)
        self.min_drop_tokens = min_drop_tokens
        self.min_drop_ratio = min_drop_ratio
    
    def record_prompt_state(self, *, session_key, messages, tools, 
                           config, model):
        """每次 LLM 调用前调用，hash 所有 cache-relevant 输入"""
        snapshot = {
            "system_hash": self._hash(config.system or ""),
            "tools_hash": self._hash(tools or []),
            "messages_prefix_hash": self._hash(messages),  # 去掉最新 2 条
            "cache_control_hash": self._hash({
                "cache_breakpoints": config.cache_breakpoints,
                "cache_mode": config.cache_mode,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "thinking": config.thinking,
            }),
            "model": model,
        }
        return snapshot
    
    def check_response_for_cache_break(self, session_key, snapshot, 
                                       cache_read_tokens):
        """每次 LLM 调用后调用，返回是否 cache break"""
        previous = self._baselines.get(session_key)
        self._baselines[session_key] = (snapshot, cache_read_tokens)
        
        if previous is None:
            return {"break_detected": False, "reason": "baseline_initialized"}
        
        prev_snap, prev_tokens = previous
        drop = max(0, prev_tokens - cache_read_tokens)
        drop_ratio = drop / prev_tokens if prev_tokens else 0
        
        changed = [k for k in snapshot if snapshot[k] != prev_snap[k]]
        
        break_detected = (
            bool(changed)
            and drop >= self.min_drop_tokens
            and drop_ratio >= self.min_drop_ratio
        )
        
        return {
            "break_detected": break_detected,
            "changed_fields": changed,
            "drop_tokens": drop,
            "drop_ratio": drop_ratio,
        }
    
    def notify_compaction(self, session_key):
        """compaction 后标记 baseline reset，避免误报"""
        # 下次响应作为新 baseline，不算 break
        ...
    
    @staticmethod
    def _hash(value):
        if isinstance(value, str):
            payload = value
        else:
            payload = json.dumps(value, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

**默认值**：`min_drop_tokens=2000, min_drop_ratio=0.05`

**阈值定法**：太小会噪声误报；太大漏报。2000 token ≈ 8k 字符，超过说明模型窗口浪费明显。

### 4.2 提示词拆分（cache 命中率优化）

参考 [`d:/code/opensquilla/src/opensquilla/engine/steps/prompt_cache.py:55-82`](d:/code/opensquilla/src/opensquilla/engine/steps/prompt_cache.py#L55-L82)：

**关键设计**：把 prompt 拆成 2-3 段，**只 cache 稳定段**：

```python
def build_cached_prompt(system: str, history_summary: str, current_request: str):
    """
    base 段：身份、规则、工具描述 - 100% cache hit 永远不变
    dynamic 段：历史摘要、记忆 - 高频 cache hit
    new 段：当前请求 - 每轮变，不缓存
    """
    return {
        "cache_base": system,            # ← 稳定缓存
        "cache_dynamic": history_summary, # ← 半稳定
        "cache_new": current_request,    # ← 不缓存
    }
```

**Anthropic cache_control 用法**：

```python
messages = [
    {
        "role": "system",
        "content": [{
            "type": "text",
            "text": prompt_dict["cache_base"],
            "cache_control": {"type": "ephemeral"}  # ← 关键标记
        }]
    },
    # 中间插入历史摘要（也可加 cache_control）
    {"role": "user", "content": prompt_dict["cache_new"]},
]
```

### 4.3 Cache 抖动场景与对策

| 场景 | 是否抖 | 对策 |
| --- | --- | --- |
| 全程同档同模型 | 不抖 | 无 |
| 偶尔升档 | 是 | capability_gate 前置检查 |
| 频繁切档 | **严重抖** | anti_downgrade 锁住 |
| 同档不同模型 fallback | 是 | 自定义 model_lock |
| 工具列表变化 | 是 | 固定工具集，cache 化 |
| compaction 后 | **必然抖** | notify_compaction 重置 baseline |

### 4.4 缓存命中率参考值

| 命中率 | 状态 | 行动 |
| ---: | --- | --- |
| > 80% | 健康 | 无 |
| 50-80% | 警告 | 加 anti_downgrade |
| < 50% | 严重 | 检查是否频繁切档/换模型 |

---

## 5. Fallback 路由（独立策略）

**关键认知**：Fallback 不是 ML 失败后的"补丁"，而是**一个完整的独立路由策略**，可以单独部署在 ML 不可用的环境（如嵌入式、CLI 工具、离线场景）。

参考 [`d:/code/opensquilla/src/opensquilla/engine/routing/heuristic.py`](d:/code/opensquilla/src/opensquilla/engine/routing/heuristic.py)：

### 5.1 特征提取（5 维）

```python
HEAVY_MIN_CHARS = 12_000
HEAVY_MIN_FENCED_BLOCKS = 3
CODE_OR_MATERIAL_MIN_CHARS = 2_500
SHORT_PLAIN_MAX_CHARS = 240
MEDIUM_PLAIN_MAX_CHARS = 1_200

def extract_features(message, routing_history=None, attachment_count=0):
    """返回 5 维确定性特征"""
    fenced_blocks = message.count("```") // 2
    return {
        "char_len": len(message),
        "has_code_fence": "```" in message,
        "code_fence_blocks": fenced_blocks,
        "attachment_count": int(attachment_count or 0),
        "history_depth": len(routing_history or []),
    }
```

### 5.2 分类规则

```python
def classify_features(features):
    """5 个分级带（first match wins）"""
    char_len = int(features.get("char_len", 0))
    has_fence = bool(features.get("has_code_fence", False))
    fenced_blocks = int(features.get("code_fence_blocks", 0))
    attachment_count = int(features.get("attachment_count", 0))
    
    # Band 1: heavy → c3
    if char_len >= HEAVY_MIN_CHARS or fenced_blocks >= HEAVY_MIN_FENCED_BLOCKS:
        return "heavy", "c3", 0.60
    # Band 2: code_or_material → c2
    if has_fence or char_len >= CODE_OR_MATERIAL_MIN_CHARS or attachment_count > 0:
        return "code_or_material", "c2", 0.60
    # Band 3: short_plain → c0
    if char_len <= SHORT_PLAIN_MAX_CHARS:
        return "short_plain", "c0", 0.55
    # Band 4: medium_plain → c1
    if char_len <= MEDIUM_PLAIN_MAX_CHARS:
        return "medium_plain", "c1", 0.55
    # Band 5: borderline_plain → c1 (故意低于 gate 阈值)
    return "borderline_plain", "c1", 0.40
```

### 5.3 置信度设计哲学

参考 [`heuristic.py:36-49`](d:/code/opensquilla/src/opensquilla/engine/routing/heuristic.py#L36-L49)：

```python
CONFIDENT_HIGH_TIER_CONFIDENCE = 0.60  # c2/c3 - 高于默认 gate 阈值
CONFIDENT_LOW_TIER_CONFIDENCE = 0.55   # c0/c1 - 高于默认 gate 阈值
BORDERLINE_CONFIDENCE = 0.40           # 故意低于 gate 阈值
```

**为什么不是越高越好**：
- 0.55-0.60：让 confidence_gate 不把它们打回默认档
- < 0.7：让 telemetry 诚实标记为"低信号猜测"
- 0.40 边界档：故意低于 gate，让模糊中等长度文本回落到默认档

### 5.4 完整 Fallback 策略类

```python
class HeuristicRouterStrategy:
    """纯 stdlib 的 fallback 路由 - 零依赖"""
    source = "heuristic"
    requires_history = True
    
    def __init__(self, error=None):
        self.error = error
    
    async def classify(self, message, valid_tiers, routing_history=None,
                       attachment_count=0):
        features = extract_features(message, routing_history, attachment_count)
        band, tier, confidence = classify_features(features)
        tier = _nearest_valid_tier(tier, valid_tiers)
        extra = {
            "route_class": TIER_TO_ROUTE_CLASS.get(tier, "R1"),
            "top1_label": TIER_TO_ROUTE_CLASS.get(tier, "R1"),
            "thinking_mode": _TIER_THINKING_MODE.get(tier, "T1"),
            "prompt_policy": "P1",  # 永远 P1 - 不用 P0 压缩
            "model_version": "heuristic-v1",
            "heuristic_band": band,
            "heuristic_features": features,
        }
        return tier, confidence, self.source, extra

def _nearest_valid_tier(tier, valid_tiers):
    """选最接近的 valid tier，宁可升档不降档"""
    if not valid_tiers:
        return "c1"
    if tier in valid_tiers:
        return tier
    start = TEXT_TIERS.index(tier) if tier in TEXT_TIERS else 1
    for candidate in TEXT_TIERS[start:]:
        if candidate in valid_tiers:
            return candidate
    for candidate in reversed(TEXT_TIERS[:start]):
        if candidate in valid_tiers:
            return candidate
    return valid_tiers[0]
```

### 5.5 三层 Fallback 链

```python
class RouterWithFallback:
    """主路径：ML → fallback 1：启发式 → fallback 2：默认档"""
    
    def __init__(self):
        self.ml_model = None
        self._try_load_ml()
    
    def _try_load_ml(self):
        try:
            import lightgbm as lgb
            self.ml_model = lgb.Booster(model_file="router.bin")
        except Exception as e:
            # ML 不可用，自动降级
            self.ml_model = None
    
    async def route(self, message, session_id="default"):
        # Layer 1: 尝试 ML
        if self.ml_model:
            try:
                return await self._ml_route(message, session_id)
            except Exception:
                pass  # ML 推理失败，降级
        
        # Layer 2: 启发式 fallback
        return await self._heuristic_route(message, session_id)
    
    async def _ml_route(self, message, session_id):
        features = extract_handcrafted(message)
        probs = self.ml_model.predict(features.reshape(1, -1))[0]
        base_tier = TIER_ORDER[np.argmax(probs)]
        # ML 也走后处理策略链（特别是 anti_downgrade）
        tier = self._apply_post_processing(base_tier, message, session_id)
        return RoutingDecision(
            tier=tier, confidence=float(np.max(probs)),
            source="ml", model=TIER_CONFIG[tier].model,
        )
    
    async def _heuristic_route(self, message, session_id):
        strategy = HeuristicRouterStrategy()
        tier, conf, source, extra = await strategy.classify(
            message, valid_tiers=TIER_ORDER
        )
        tier = self._apply_post_processing(tier, message, session_id)
        return RoutingDecision(
            tier=tier, confidence=conf, source=source,
            model=TIER_CONFIG[tier].model, extra=extra,
        )
```

**关键**：**ML 和启发式共享同一个后处理策略链**。两者输出的 tier 经过相同的 anti_downgrade / capability_gate 等处理。

### 5.6 Fallback 触发条件

参考 [`v4_phase3.py:90-102`](d:/code/opensquilla/src/opensquilla/squilla_router/v4_phase3.py#L90-L102)：

```python
try:
    self._init_runtime(use_aux_head=use_aux_head)
except Exception as exc:
    log.error("v4_phase3.init_failed", error=str(exc))
    if require_router_runtime:
        raise  # 配置要求 ML 必须可用
    # 否则默默降级到启发式
```

**触发 fallback 的场景**：
1. ML 依赖未装（onnxruntime / lightgbm 缺失）
2. Bundle 文件损坏或 SHA-256 不匹配
3. 模型推理时抛异常
4. 配置 `require_router_runtime=false`（默认）

**强制 ML 模式**：设置 `require_router_runtime=true` 时 ML 失败直接报错而不是降级。

---

## 6. 思考模式 & 提示策略联动

参考 [`d:/code/opensquilla/src/opensquilla/squilla_router/controller.py`](d:/code/opensquilla/src/opensquilla/squilla_router/controller.py)：

### 6.1 4 个思考档

```python
_THINKING_MODE_LEVEL = {
    "T0": None,     # 不思考
    "T1": "low",    # 浅思考
    "T2": "medium", # 中等思考
    "T3": "high",   # 深度思考
}

_TIER_TO_THINKING_MODE = {"c0": "T0", "c1": "T1", "c2": "T2", "c3": "T3"}
```

### 6.2 3 个提示策略

```python
_PROMPT_HINTS = {
    "P0": {
        "hint_zh": "直接作答，缩短思考长度，避免无关展开。",
        "hint_en": "Answer directly, keep thinking short, avoid irrelevant expansion.",
    },
    "P1": {"hint_zh": "", "hint_en": ""},  # 默认，无 hint
    "P2": {
        "hint_zh": "充分分析，覆盖关键约束，避免遗漏。",
        "hint_en": "Analyze thoroughly, cover key constraints, avoid omissions.",
    },
}
```

### 6.3 档位与策略的搭配

```python
def normalize_decisions(thinking_mode, prompt_policy):
    """禁止 THINK_DEEP + P0（自相矛盾）"""
    if thinking_mode in ("T2", "T3") and prompt_policy == "P0":
        return thinking_mode, "P1"  # 升级到 P1
    return thinking_mode, prompt_policy
```

| 档位 | 思考 | 提示策略 |
| --- | --- | --- |
| C0 | T0（无） | P0（短答） |
| C1 | T1（低） | P1（默认） |
| C2 | T2（中） | P1（默认） |
| C3 | T3（高） | P2（完整） |

**核心思想**：用**动态 system prompt**——简单任务不发全套，只塞一句"短答别展开"，省几百 token。

---

## 7. 观测埋点（路由决策可追溯）

### 7.1 决策日志

参考 [`d:/code/opensquilla/src/opensquilla/observability/decision_log.py`](d:/code/opensquilla/src/opensquilla/observability/decision_log.py)：

```python
def log_routing_decision(*, session_id, msg_preview, decision):
    """每次路由都记录（不存原始 prompt，存特征摘要）"""
    log.info(
        "route.decision",
        session_id=session_id,
        msg_preview=msg_preview[:80],  # ← 不要存完整 prompt（隐私）
        tier=decision.tier,
        model=decision.model,
        confidence=decision.confidence,
        source=decision.source,
        extra=decision.extra,
    )

def log_cache_break(*, session_id, break_info):
    if break_info["break_detected"]:
        log.warning(
            "cache.break",
            session_id=session_id,
            changed_fields=break_info["changed_fields"],
            drop_tokens=break_info["drop_tokens"],
            drop_ratio=break_info["drop_ratio"],
        )
```

### 7.2 缓存命中率埋点

```python
def compute_cache_hit_rate(usage_stats) -> float:
    cached = usage_stats.get("cache_read_input_tokens", 0)
    total = usage_stats.get("input_tokens", 0)
    return cached / total if total > 0 else 0.0

CACHE_HIT_RATE_LOW_THRESHOLD = 0.5
```

### 7.3 训练数据采集（用于再训练）

```python
training_sample = {
    "features_390_b64": encode_features(features),  # 编码后的特征向量
    "routed_tier": decision.tier,
    "route_class": decision.extra["route_class"],
    "final_route_class": decision.extra.get("final_route_class"),
    "confidence": decision.confidence,
    "margin": decision.extra.get("margin", 0.0),
    "complaint_detected": decision.extra.get("complaint_detected", False),
    "user_feedback": None,  # 后续接反馈机制
}
# 定期 dump 到 JSONL 用于离线训练
```

**关键约束**（[`self_learning/__init__.py:7-10`](d:/code/opensquilla/src/opensquilla/squilla_router/self_learning/__init__.py#L7-L10)）：

> Raw prompt text is never stored. The training payload is the float16 features_390 vector the model already produced at inference time.

---

## 8. Bundle 资产清单

参考 [`d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/)：

| 文件 | 大小 | 角色 |
| ---: | ---: | --- |
| `lgbm_main.bin` | 39.7 MB | 主 LightGBM 分类器 |
| `lgbm_aux.bin` | 3.5 MB | 辅助分类头 |
| `mlp/model.onnx` | 2.4 MB | 备选 MLP 头 |
| `features/tfidf.pkl` | ~3 MB | 字符 n-gram TF-IDF（已 fit） |
| `features/svd.pkl` | ~2 MB | 100 维 SVD 投影（已 fit） |
| `features/bge_pca.joblib` | < 100 KB | BGE PCA(64) 投影（已 fit） |
| `bge_onnx/model.onnx` | 23.9 MB | BGE-small-zh INT8 |
| `bge_onnx/tokenizer.json` | 439 KB | 分词器 |
| `router.runtime.yaml` | < 10 KB | 路由配置 |

**总计 ~70 MB**，常驻内存 ~300 MB。

### 8.1 Bundle 的合法使用方式

参考 [`models/v4.2_phase3_inference/PROVENANCE.md`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/PROVENANCE.md)：

**可以**：
- ✅ 直接加载作为自家 router（如果档位映射和模型生态类似）
- ✅ 当 teacher 给自家数据打弱标签
- ✅ 拆 bundle 的特征提取器复用

**不要**：
- ❌ 在跟 OpenSquilla 数据分布差异大的场景下直接当主路由
- ❌ 用他们训练数据训的模型去预测他们没见过的领域（欠拟合）
- ❌ 改 bundle 的内部权重文件（SHA-256 校验会失败）

### 8.2 重新训练数据来源

3 个实用方案：

**A. Bundle 当 teacher（最快，零成本）**
```python
# 把 bundle 跑一遍自家 query 库，输出 R0/R1/R2/R3 弱标签
labels = [label_with_bundle(q) for q in my_queries]
# 然后重训 LightGBM
my_model = train_lightgbm(my_queries, labels)
my_model.save_model("router.bin")
```

**B. LLM judge（成本中等）**
```python
# 用 gpt-4o-mini 当 judge，标 1000 条约 $0.50
JUDGE_PROMPT = """评估这个 query 的难度，0=闲聊 1=简单 2=中等 3=困难。
{query}
只返回 0/1/2/3。"""
```

**C. 弱标签起点（零成本起步）**
```python
# 用启发式 fallback 的分类结果当弱标签
def weak_label(text):
    if len(text) >= 12000 or text.count("```") >= 6: return 3
    if "```" in text or len(text) >= 2500: return 2
    if len(text) <= 240: return 0
    return 1
```

---

## 9. 参考文件快速索引

| 你要参考什么 | 看 OpenSquilla 哪个文件 |
|---|---|
| 4 档定义 + TierConfig | [`router_tiers.py`](d:/code/opensquilla/src/opensquilla/router_tiers.py) |
| 51 维手写特征完整代码 | [`features.py:220-310`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/features.py#L220-L310) |
| 13 类中英关键词表 | [`features.py:46-65`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/features.py#L46-L65) |
| 12 维 assistant 特征 | [`v4_features.py:75-111`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/v4_features.py#L75-L111) |
| 7 维 continuation/reasoning 特征 | [`v4_features.py:114-150`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/v4_features.py#L114-150) |
| 192 维 BGE × 3 通道 | [`v4_features.py:185-316`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/v4_features.py#L185-L316) |
| 100 维 TFIDF/SVD | [`features.py:338-355`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/features.py#L338-L355) |
| 390 维特征装配 | [`inference/features.py:14-68`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/runtime_src/src/router/inference/features.py#L14-L68) |
| 6 个后处理策略 | [`policy.py`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py) |
| TierLock (anti_downgrade) | [`policy.py:301-316`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L301-L316) |
| ComplaintUpgrade | [`policy.py:260-292`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L260-L292) |
| CapabilityGate | [`policy.py:346-427`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L346-L427) |
| LargeContextFloor | [`policy.py:534-587`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L534-L587) |
| BudgetGate | [`policy.py:628-757`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L628-L757) |
| Pipeline 编排 | [`policy.py:941-994`](d:/code/opensquilla/src/opensquilla/engine/routing/policy.py#L941-L994) |
| CacheBreakMonitor | [`cache_break_monitor.py`](d:/code/opensquilla/src/opensquilla/engine/cache_break_monitor.py) |
| Prompt cache 拆分 | [`prompt_cache.py`](d:/code/opensquilla/src/opensquilla/engine/steps/prompt_cache.py) |
| 启发式 fallback（完整策略） | [`heuristic.py`](d:/code/opensquilla/src/opensquilla/engine/routing/heuristic.py) |
| 路由总入口（V4Phase3Strategy） | [`v4_phase3.py`](d:/code/opensquilla/src/opensquilla/squilla_router/v4_phase3.py) |
| 思考模式 & 提示策略 | [`controller.py`](d:/code/opensquilla/src/opensquilla/squilla_router/controller.py) |
| 路由配置 YAML | [`router.runtime.yaml`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/router.runtime.yaml) |
| Bundle 结构 | [`v4.2_phase3_inference/`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/) |
| Bundle provenance | [`PROVENANCE.md`](d:/code/opensquilla/src/opensquilla/squilla_router/models/v4.2_phase3_inference/PROVENANCE.md) |
| 自学习闭环（暂不搬） | [`self_learning/`](d:/code/opensquilla/src/opensquilla/squilla_router/self_learning/) |
| 路由产品文档 | [`docs/features/squilla-router.md`](d:/code/opensquilla/docs/features/squilla-router.md) |
| Cache 产品文档 | [`docs/features/compaction-and-cache.md`](d:/code/opensquilla/docs/features/compaction-and-cache.md) |
| 工具压缩文档 | [`docs/features/tool-compression.md`](d:/code/opensquilla/docs/features/tool-compression.md) |

---

## 10. 实施优先级建议

按"投入产出比"排序：

| 优先级 | 方法 | 见效 | 效果 |
|---:|---|---|---|
| P0 | 51 维手写特征 + 启发式 fallback | 1 天 | 成本砍 30-50% |
| P0 | 6 个后处理策略（特别是 anti_downgrade） | 1 天 | 防 cache 抖动 |
| P1 | Bundle 当 teacher 打标签 + 训练 LightGBM | 1 周 | 成本砍 60-80% |
| P1 | CacheBreakMonitor + 提示词拆分 | 3 天 | 缓存命中率 80%+ |
| P2 | 12 维 assistant 特征 | 1 周 | 路由精度再提升 |
| P2 | 192 维 BGE 通道 | 1 周 | 多语/复杂 query |
| P3 | 自学习闭环 | 1 月+ | 持续优化 |

---

## 11. 不要踩的坑

1. **不要直接搬 `lgbm_main.bin`**：bundle 只当 teacher，重训一个用自家数据
2. **不要一上来就上 BGE embedder**：先 51 维手写特征跑通再加
3. **anti_downgrade 窗口保持 600 秒**：OpenSquilla benchmark 出的最优值
4. **confidence_threshold 保持 0.5**：调高会废掉 fallback，调低会过度信任 ML
5. **训练数据必须用真实 query**：合成数据会带 GPT 偏向，导致过拟合
6. **路由决策不要 cache**：动态路由的 cache 反而会导致决策错乱
7. **同档 fallback 模型不要混用**：同档不同 model 也会抖 cache

---

## 12. 一句话总结

**抄 OpenSquilla 的特征工程 + 后处理策略 + cache 监控，自己训 LightGBM，用 bundle 当 teacher 打标签。** 完整复制 4 层路由架构（L1 启发式 / L2 4 档 / L3 后处理 / L4 ML），其中 fallback 是**独立的完整策略**而不是 ML 的补丁。