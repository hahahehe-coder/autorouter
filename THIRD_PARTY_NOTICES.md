# Third-Party Notices

This project includes or depends on the following third-party software. We
acknowledge and thank their authors and contributors.

---

## OpenSquilla — SquillaRouter V4 Phase 3 ML Bundle

The ML routing layer (`app/ml/`, `app/ml_router.py`) is derived from
[OpenSquilla](https://github.com/opensquilla/opensquilla)'s
SquillaRouter V4 Phase 3 inference bundle.

- **Upstream project**: <https://github.com/opensquilla/opensquilla>
- **Bundle path inside upstream**: `src/opensquilla/squilla_router/models/v4.2_phase3_inference/`
- **Local copy**: `models/v4.2_phase3_inference/`
- **License**: MIT (see upstream repository)
- **Provenance**: `models/v4.2_phase3_inference/PROVENANCE.md`
- **Artifact manifest**: `models/v4.2_phase3_inference/artifact_manifest.json`

---

## FlagEmbedding — BGE Embeddings

The ML bundle uses BGE (BAAI General Embedding) for sentence embeddings.

- **Upstream project**: <https://github.com/FlagOpen/FlagEmbedding>
- **Hugging Face model**: <https://huggingface.co/BAAI/bge-small-zh-v1.5>
- **License**: MIT
- **Citation**:
  ```
  @article{bge_small,
    title={C-Pack: Packaged Resources To Advance General Chinese Embedding},
    author={Xiao, Shitao and Liu, Zheng and Zhang, Peitian and Muennighoff, Niklas},
    journal={arXiv preprint arXiv:2309.07597},
    year={2023}
  }
  ```

---

## LightGBM

Gradient-boosted decision tree heads used by the ML bundle.

- **Upstream**: <https://github.com/microsoft/LightGBM>
- **License**: MIT

---

## scikit-learn / joblib

Used for feature extraction (TF-IDF, SVD, BGE PCA artifact persistence).

- **Upstream**: <https://github.com/scikit-learn/scikit-learn>
- **License**: BSD-3-Clause

---

## ONNX Runtime

Used to execute the BGE ONNX embedding model.

- **Upstream**: <https://github.com/microsoft/onnxruntime>
- **License**: MIT

---

## FastAPI / Uvicorn / httpx

Web framework and async HTTP client used by AutoRouter itself.

- FastAPI: <https://github.com/tiangolo/fastapi> — MIT
- Uvicorn: <https://github.com/encode/uvicorn> — BSD-3-Clause
- httpx: <https://github.com/encode/httpx> — BSD-3-Clause

---

## Hugging Face Transformers (tokenizer only)

Tokenizer artifacts are derived from the BGE model's tokenizer.

- **Upstream**: <https://github.com/huggingface/transformers>
- **License**: Apache-2.0