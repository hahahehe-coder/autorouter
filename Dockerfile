# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

# uv from official multi-arch image (no curl|sh needed at build time)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# 国内 PyPI 镜像,境外部署可改为空
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依赖文件单独 COPY,利用 Docker 构建缓存
COPY pyproject.toml uv.lock ./

# --extra ml 装 lightgbm/onnxruntime/sklearn/tokenizers/numpy/joblib
RUN uv sync --frozen --extra ml --no-dev

# 源码 + 配置 + SPA bundle + ML 数据文件
COPY app/ ./app/
COPY config/ ./config/
COPY web/dist/ ./web/dist/
COPY models/ ./models/

# 非 root 运行
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 3001

# 健康检查(用 urllib 不用 curl,slim 镜像没装 curl)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3001/health', timeout=2).status==200 else 1)"

CMD ["uv", "run", "uvicorn", "app.channel:app", \
     "--host", "0.0.0.0", \
     "--port", "3001", \
     "--workers", "2"]