#!/usr/bin/env bash
# scripts/deploy.sh — 一键打包并远程部署
#
# 用法:
#   ./scripts/deploy.sh                          # 用默认服务器配置
#   ./scripts/deploy.sh user@host 2222 /opt/autorouter
#
# 流程:本地打包 → scp → 远端解压(保留用户 config/) → docker compose up -d --build
#
# 服务器配置默认写在下方 DEPLOY_* 三行,直接改这里就行;
# 命令行参数临时覆盖这三个值。
#
# 首次连新主机 StrictHostKeyChecking=accept-new 自动接受新 host,
# 但会拒绝 host key 变化的服务器(防中间人)。

set -euo pipefail

DEPLOY_HOST="${1:-root@10.18.101.56}"
DEPLOY_PORT="${2:-10022}"
DEPLOY_REMOTE_DIR="${3:-/opt/autorouter}"

SSH_OPTS=(-o "StrictHostKeyChecking=accept-new" -o "UserKnownHostsFile=/dev/null" -p "$DEPLOY_PORT")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

TARBALL="/tmp/autorouter.tgz"

echo "==> [1/4] 打包 $PROJECT_ROOT -> $TARBALL"
tar -czf "$TARBALL" \
    --exclude='./.venv' \
    --exclude='./.git' \
    --exclude='./node_modules' \
    --exclude='./web/node_modules' \
    --exclude='./__pycache__' \
    --exclude='*/__pycache__' \
    --exclude='*/*/__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='./log' \
    --exclude='./.claude' \
    .
echo "    size: $(du -h "$TARBALL" | cut -f1)"

echo "==> [2/4] scp -> $DEPLOY_HOST:$DEPLOY_PORT"
scp "${SSH_OPTS[@]}" "$TARBALL" "$DEPLOY_HOST:/tmp/autorouter.tgz"

echo "==> [3/4] ssh -> extract + restart"
ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" bash -s -- "$DEPLOY_REMOTE_DIR" <<'REMOTE_EOF'
set -e
REMOTE_DIR="$1"
mkdir -p "$REMOTE_DIR"
cd "$REMOTE_DIR"

# 保留用户编辑过的 config/(connection.yaml 含真实渠道 key,
# 避免被 tarball 里的空模板覆盖)
if [ -f config/connection.yaml ]; then
    cp config/connection.yaml /tmp/_connection.yaml.bak
fi

tar -xzf /tmp/autorouter.tgz
rm -f /tmp/autorouter.tgz

if [ -f /tmp/_connection.yaml.bak ]; then
    cp /tmp/_connection.yaml.bak config/connection.yaml
    rm -f /tmp/_connection.yaml.bak
    echo "    config/connection.yaml restored"
fi

echo "    docker compose up -d --build ..."
docker compose up -d --build
docker image prune -f
REMOTE_EOF

echo "==> [4/4] 完成"
echo ""
echo "  实时日志:  ssh -p $DEPLOY_PORT $DEPLOY_HOST 'cd $DEPLOY_REMOTE_DIR && docker compose logs -f'"
echo "  健康检查:  curl http://$DEPLOY_HOST:3001/health"