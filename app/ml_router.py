"""
ML 路由 adapter —— 加载 OpenSquilla 预训练 bundle,用 app.ml 自带的推理代码。

设计要点:
- 顶层只 import stdlib + yaml;重依赖(lightgbm/onnxruntime/joblib/sklearn/tokenizers/numpy)
  在 configure() 内经 `from .ml.core import InferenceCore` 懒加载 → 默认无 ml extra 也能 import。
- 全程优雅降级:enabled=false / 依赖缺失 / bundle 缺失 / 加载或推理出错 → instance=None,
  调用方(_route_heuristic)自动回退到启发式分类。
- 推理代码在 app.ml/(从 OpenSquilla 移植),bundle 只作数据文件,不再 sys.path 引用 opensquilla。
- 取 InferenceCore.predict() 的融合 4 类概率(lgbm_main+mlp 融合),argmax → idx,
  后处理交给 AutoRouter 自己的 policy 链。
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("autorouter.ml_router")

_ROUTE_CLASSES = ["R0", "R1", "R2", "R3"]
_BAND_NAMES = ["trivial", "medium", "code", "heavy"]   # 与 heuristic 词汇一致


@dataclass
class _State:
    enabled: bool = False
    instance: object = None        # InferenceCore 实例
    req_cls: object = None         # InferenceRequest 类(缓存,避免每请求 import)
    status: str = "unconfigured"   # unconfigured|disabled|ready|deps_missing|bundle_missing|runtime_error
    reason: str = ""
    bundle_path: str = ""


_STATE = _State()


# ============================ messages → InferenceRequest 字段 ============================

def _flatten_content(content) -> str:
    """把 message content(str 或 [{type:text,text:...}] 多模态)拍平成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _split_turns(messages) -> tuple[str, list[str], str | None]:
    """从 messages 抽 (current_user_text, history_user_texts, prev_assistant_text)。"""
    msgs = messages or []
    last_user_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if isinstance(msgs[i], dict) and msgs[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx < 0:
        return "", [], None
    current = _flatten_content(msgs[last_user_idx].get("content", ""))
    history = [
        _flatten_content(msgs[i].get("content", ""))
        for i in range(last_user_idx)
        if isinstance(msgs[i], dict) and msgs[i].get("role") == "user"
    ]
    prev_asst: str | None = None
    for i in range(last_user_idx - 1, -1, -1):
        if isinstance(msgs[i], dict) and msgs[i].get("role") == "assistant":
            prev_asst = _flatten_content(msgs[i].get("content", ""))
            break
    return current, history, prev_asst


# ============================ 生命周期 ============================

def configure(ml_cfg) -> None:
    """根据 MLCfg 加载(或禁用)ML 路由。任何失败都不抛,只记 status/reason。"""
    _STATE.enabled = bool(ml_cfg.enabled)
    _STATE.bundle_path = ml_cfg.bundle_path
    _STATE.instance = None
    _STATE.req_cls = None

    if not _STATE.enabled:
        _STATE.status = "disabled"
        _STATE.reason = "ml.enabled=false"
        return

    bundle = Path(ml_cfg.bundle_path)
    if not bundle.is_dir():
        _STATE.status = "bundle_missing"
        _STATE.reason = f"bundle_path 无效: {ml_cfg.bundle_path}"
        return

    # config:bundle 的 router.runtime.yaml(可选;给 from_model_dir 用)
    config: dict = {}
    rt_yaml = bundle / "router.runtime.yaml"
    if rt_yaml.is_file():
        try:
            with rt_yaml.open("r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception:
            config = {}

    # bundle 的 sklearn pickle 是 1.8.0 训的,1.9.0 反序列化会刷 InconsistentVersionWarning —— 无害,压掉
    try:
        from sklearn.exceptions import InconsistentVersionWarning  # noqa: WPS433
    except ImportError:
        InconsistentVersionWarning = None

    try:
        from .ml.core import InferenceCore                  # noqa: WPS433 懒加载重依赖
        from .ml.types import InferenceRequest              # noqa: WPS433
    except ImportError as e:
        _STATE.status = "deps_missing"
        _STATE.reason = f"ml extra 未安装(uv sync --extra ml): {e}"
        logger.info(f"ML deps missing, will use heuristic: {e}")
        return

    try:
        with warnings.catch_warnings():
            if InconsistentVersionWarning is not None:
                warnings.simplefilter("ignore", InconsistentVersionWarning)
            instance = InferenceCore.from_model_dir(str(bundle), config, use_aux_head=False)
    except FileNotFoundError as e:
        _STATE.status = "bundle_missing"
        _STATE.reason = str(e)
        logger.warning(f"ML bundle incomplete: {e}")
        return
    except Exception as e:
        _STATE.status = "runtime_error"
        _STATE.reason = f"load failed: {e}"
        logger.warning(f"ML bundle load failed: {e}")
        return

    if ml_cfg.warmup_on_load:
        try:
            warm = InferenceRequest(
                current_user_text="hello",
                history_user_texts=[],
                prev_assistant_text=None,
                prev_assistant_usage=None,
                prev_route_decisions=[],
            )
            instance.predict(warm)   # 触发 BGE 懒加载,单线程,消除首请求延迟 + 并发竞态
        except Exception as e:
            _STATE.status = "runtime_error"
            _STATE.reason = f"warmup failed: {e}"
            logger.warning(f"ML warmup failed: {e}")
            return

    _STATE.instance = instance
    _STATE.req_cls = InferenceRequest
    _STATE.status = "ready"
    _STATE.reason = ""
    logger.info(f"ML router ready (bundle={ml_cfg.bundle_path})")


def is_available() -> bool:
    return _STATE.instance is not None


def status() -> dict:
    return {
        "enabled": _STATE.enabled,
        "available": _STATE.instance is not None,
        "status": _STATE.status,
        "reason": _STATE.reason,
        "bundle_path": _STATE.bundle_path,
        "route_classes": list(_ROUTE_CLASSES),
    }


# ============================ 分类入口(同步,调用方用 asyncio.to_thread 包裹) ============================

def classify(text: str, messages) -> tuple[int, str, float, int, bool] | None:
    """返回 (idx 0-3, band, confidence, material_tokens, has_image) 或 None(回退启发式)。"""
    if _STATE.instance is None or _STATE.req_cls is None:
        return None
    try:
        current, history, prev_asst = _split_turns(messages)
        if not current:
            current = text or ""
        req = _STATE.req_cls(
            current_user_text=current,
            history_user_texts=history,
            prev_assistant_text=prev_asst,
            prev_assistant_usage=None,
            prev_route_decisions=[],
        )
        probs = _STATE.instance.predict(req) or {}
        vals = [float(probs.get(rc, 0.0)) for rc in _ROUTE_CLASSES]
        idx = vals.index(max(vals))
        return idx, _BAND_NAMES[idx], vals[idx], 0, False
    except Exception as e:
        logger.warning(f"ML classify failed → 回退启发式: {e}")
        return None
