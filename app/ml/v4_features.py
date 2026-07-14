"""V4 helper surface: assistant/continuation/reasoning features + BGE 3-channel.

Ported from OpenSquilla v4.2 phase3 runtime. Legacy V4FeatureExtractor omitted
(Phase 3 online assembly is in bundle.build_feature_bundle).
"""
from __future__ import annotations

import re

import joblib
import numpy as np
from sklearn.decomposition import PCA

# ---------------------------------------------------------------------------
# Assistant handcrafted (12 dims)
# ---------------------------------------------------------------------------

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
_RE_CODE_INLINE = re.compile(r"`[^`]{4,}`")
_RE_NUMBERED_LIST = re.compile(r"^\s*\d+[\.、]\s", re.M)
_RE_CONTINUATION = re.compile(
    r"(?:请继续|继续|接着|续写|展开一下|再说|more|continue|go on|carry on|next)",
    re.I,
)
_RE_REASONING = re.compile(
    r"(?:why|compare|trade[ -]?off|analy[sz]e|architecture|reasoning|design"
    r"|解释|原因|对比|分析|架构|设计|权衡)",
    re.I,
)


def _zh_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    zh = sum(1 for c in text if "一" <= c <= "鿿")
    return zh / max(len(text), 1)


def _normalize_log_usage(usage: dict | None, key: str, divisor: float = 10.0) -> float:
    value = (usage or {}).get(key, 0) or 0
    return float(np.log1p(max(value, 0)) / divisor)


def extract_assistant_handcrafted(prev_assistant_text: str | None,
                                   prev_assistant_usage: dict | None,
                                   current_user_text: str) -> np.ndarray:
    """12-dim float32 assistant signal vector."""
    if prev_assistant_text is None:
        return np.zeros(12, dtype=np.float32)
    t = prev_assistant_text
    u = prev_assistant_usage or {}
    return np.array([
        1.0,
        float(_RE_CLAR.search(t) is not None),
        float(_RE_REFUSAL.search(t) is not None),
        float(_RE_SELF_DOUBT.search(t) is not None),
        float("```" in t or _RE_CODE_INLINE.search(t) is not None),
        float(_RE_NUMBERED_LIST.search(t) is not None),
        np.log1p(u.get("output_tokens", 0) or 0) / 10.0,
        np.log1p(u.get("reasoning_tokens", 0) or 0) / 10.0,
        np.log1p(u.get("duration_ms", 0) or 0) / 10.0,
        min(len(t) / max(len(current_user_text), 1), 5.0) / 5.0,
        _zh_char_ratio(t),
        (u.get("cached_tokens", 0) or 0) / max(u.get("input_tokens", 1) or 1, 1),
    ], dtype=np.float32)


def extract_continuation_features(prev_assistant_usage: dict | None,
                                  current_user_text: str) -> np.ndarray:
    """2-dim float32 continuation vector."""
    text = (current_user_text or "").strip()
    is_short = len(text) <= 24
    has_cue = bool(text) and is_short and _RE_CONTINUATION.search(text) is not None
    return np.array([
        float(has_cue),
        _normalize_log_usage(prev_assistant_usage, "output_tokens"),
    ], dtype=np.float32)


def extract_reasoning_features(prev_assistant_usage: dict | None,
                               current_user_text: str) -> np.ndarray:
    """5-dim float32 reasoning vector."""
    text = (current_user_text or "").strip()
    qmarks = text.count("?") + text.count("？")
    return np.array([
        float(_RE_REASONING.search(text) is not None),
        min(qmarks / max(len(text), 1) * 20.0, 1.0),
        float(np.log1p(len(text)) / 10.0),
        _normalize_log_usage(prev_assistant_usage, "reasoning_tokens"),
        _normalize_log_usage(prev_assistant_usage, "duration_ms"),
    ], dtype=np.float32)


# ---------------------------------------------------------------------------
# History user text concatenation
# ---------------------------------------------------------------------------

_HISTORY_SEP = "\n[SEP]\n"


def make_history_user_text(prior_user_turns: list[str], max_turns: int = 4,
                           max_chars: int = 1500) -> str:
    """Concatenate up to max_turns prior user turns, oldest→newest, [SEP]-separated."""
    if not prior_user_turns:
        return ""
    selected = list(prior_user_turns[-max_turns:])
    text = _HISTORY_SEP.join(selected)
    while len(text) > max_chars and len(selected) > 1:
        selected = selected[1:]
        text = _HISTORY_SEP.join(selected)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


# ---------------------------------------------------------------------------
# BGE × 3 segments + shared PCA(64)
# ---------------------------------------------------------------------------

class BGEChannelExtractor:
    """Shared BGE encoder + shared PCA(64) for three text segments.

    Output shape: (192,) = concat of 3 × PCA(64).
    """

    def __init__(self, bge_model_name: str = "BAAI/bge-small-zh-v1.5",
                 pca_dim: int = 64, seed: int = 42,
                 backend: str = "sentence_transformers",
                 onnx_model_dir: str | None = None):
        self.bge_model_name = bge_model_name
        self.pca_dim = pca_dim
        self.seed = seed
        self.backend = backend
        self.onnx_model_dir = onnx_model_dir
        if backend == "onnx" and not onnx_model_dir:
            raise ValueError("backend='onnx' requires onnx_model_dir")
        self._bge = None
        self.pca: PCA | None = None
        self.fitted = False

    def _ensure_bge(self):
        if self._bge is None:
            if self.backend == "onnx":
                from .bge_onnx import OnnxBGE
                self._bge = OnnxBGE(self.onnx_model_dir)
            else:
                from sentence_transformers import SentenceTransformer
                self._bge = SentenceTransformer(self.bge_model_name)
        return self._bge

    def _encode_triplet(
        self,
        current_user: str | None,
        history_user: str | None,
        prev_assistant: str | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        if not self.fitted:
            raise RuntimeError("Call fit() before transform_one().")
        bge = self._ensure_bge()
        texts = [current_user or "", history_user or "", prev_assistant or ""]
        raw = bge.encode(texts, batch_size=3, show_progress_bar=False,
                         convert_to_numpy=True).astype(np.float32)   # (3, 512)
        reduced = self.pca.transform(raw)                            # (3, k)
        if reduced.shape[1] < self.pca_dim:
            pad = np.zeros((reduced.shape[0], self.pca_dim - reduced.shape[1]),
                           dtype=reduced.dtype)
            reduced = np.concatenate([reduced, pad], axis=1)
        return reduced.astype(np.float32), raw

    def transform_triplet(self, current_user: str | None, history_user: str | None,
                          prev_assistant: str | None) -> tuple[np.ndarray, np.ndarray]:
        reduced, raw = self._encode_triplet(
            current_user,
            history_user,
            prev_assistant,
        )
        return (
            np.concatenate(reduced, axis=0).astype(np.float32),
            raw.reshape(-1).astype(np.float32),
        )

    @classmethod
    def load(cls, path) -> "BGEChannelExtractor":
        state = joblib.load(path)
        ex = cls(
            state["bge_model_name"],
            state["pca_dim"],
            state["seed"],
            state.get("backend", "sentence_transformers"),
            state.get("onnx_model_dir"),
        )
        ex.pca = state["pca"]
        ex.fitted = state["fitted"]
        return ex
