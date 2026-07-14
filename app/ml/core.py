"""Lean InferenceCore — build 390-dim features → run lgbm_main+mlp heads → fuse.

Ported/simplified from OpenSquilla v4.2 phase3: postprocess dropped (AutoRouter
uses its own policy chain). predict() returns the fused 4-class probability dict.
"""
from __future__ import annotations

import numpy as np

from .artifacts import InferenceArtifacts
from .bundle import build_feature_bundle
from .ensemble import fuse_probabilities
from .heads import run_heads

ROUTE_CLASSES = ["R0", "R1", "R2", "R3"]


class InferenceCore:
    def __init__(
        self,
        *,
        config: dict,
        alpha: np.ndarray,
        temperature: float,
        main_model,
        aux_model,
        mlp_session,
        mlp_scaler,
        v3_extractor,
        bge_extractor,
    ):
        self.config = config
        self.alpha = np.asarray(alpha, dtype=np.float64)
        self.temperature = float(temperature)
        self.main_model = main_model
        self.aux_model = aux_model
        self.mlp_session = mlp_session
        self.mlp_scaler = mlp_scaler
        self.v3_extractor = v3_extractor
        self.bge_extractor = bge_extractor

    @classmethod
    def from_model_dir(
        cls,
        model_dir: str,
        config: dict,
        *,
        use_aux_head: bool,
    ) -> "InferenceCore":
        artifacts = InferenceArtifacts.load(model_dir)
        loaded = artifacts.load_runtime_objects(
            config=config,
            use_aux_head=use_aux_head,
        )
        return cls(
            config=config,
            alpha=np.asarray(artifacts.manifest["per_class_alpha"], dtype=np.float64),
            temperature=float(artifacts.manifest["temperature"]),
            main_model=loaded["main_model"],
            aux_model=loaded["aux_model"],
            mlp_session=loaded["mlp_session"],
            mlp_scaler=loaded["mlp_scaler"],
            v3_extractor=loaded["v3_extractor"],
            bge_extractor=loaded["bge_extractor"],
        )

    def predict(self, request) -> dict[str, float]:
        """Build features → run heads → fuse → return {R0..R3: probability}."""
        bundle = build_feature_bundle(
            request=request,
            v3_extractor=self.v3_extractor,
            bge_extractor=self.bge_extractor,
        )
        outputs = run_heads(
            bundle=bundle,
            main_model=self.main_model,
            aux_model=self.aux_model,
            mlp_session=self.mlp_session,
            mlp_scaler=self.mlp_scaler,
            temperature=self.temperature,
        )
        fused = fuse_probabilities(outputs.p_main_lgbm, outputs.p_mlp_calibrated, self.alpha)
        return {rc: float(fused[i]) for i, rc in enumerate(ROUTE_CLASSES)}
