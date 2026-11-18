#!/usr/bin/env bash
# build_sandbox.sh — 构建 W2 沙箱基础镜像 local-agent/sandbox:0.1
# 依据：W2-plan.md §2.1、SANDBOX_RULES.md §2/§5/§6、ADR-0001
set -euo pipefail

IMAGE="${IMAGE:-local-agent/sandbox:0.1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { echo "[build-sandbox] $*"; }

command -v docker >/dev/null 2>&1 || { echo "docker 未安装" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker daemon 未运行" >&2; exit 1; }

log "构建镜像 $IMAGE（基线 Python 3.11 + Bash + 标准库）"
if ! docker build \
  --file "$REPO_ROOT/docker/sandbox.Dockerfile" \
  --tag "$IMAGE" \
  "$REPO_ROOT/docker"; then
  echo "[build-sandbox] 构建失败。常见原因（按序排查）：" >&2
  echo "  1. 无法访问 registry-1.docker.io:443（无网络/无 HTTPS 代理）——配置代理或换用可达的镜像仓库" >&2
  echo "  2. daemon 不可达——在宿主机 Docker CLI（非 WSL）执行本脚本" >&2
  echo "  3. 本地无 python:3.11-slim 且无法拉取——先 docker pull python:3.11-slim" >&2
  exit 1
fi

log "镜像体积:"
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -F "$IMAGE"

log "校验非 root 与无包管理:"
docker run --rm "$IMAGE" whoami
