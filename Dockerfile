# syntax=docker/dockerfile:1

# ---- stage 1: 构建 Svelte SPA → web/dist ----
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- stage 2: python 运行时 ----
FROM python:3.12-slim
# uv(和本地开发一致,复用 uv.lock)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev
COPY app/ ./app/
# 把 stage 1 的构建产物烘焙进镜像(运行时不需要 node)
COPY --from=web /web/dist ./web/dist

ENV CONFIG_DIR=/app/config \
    SPA_DIST=/app/web/dist \
    PYTHONUNBUFFERED=1

# host 网络模式下,进程按 connection.yaml 绑 127.0.0.1:3001。
# 端口隔离由 docker-compose 的 network_mode: host + 回环绑定保证。
EXPOSE 3001
CMD ["uv", "run", "python", "-m", "app.channel"]
