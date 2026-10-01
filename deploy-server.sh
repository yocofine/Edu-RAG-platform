#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "缺少 .env：请先执行 cp .env.example .env，并替换所有 CHANGE_ME。" >&2
  exit 1
fi

if grep -q "CHANGE_ME" .env; then
  echo ".env 中仍包含 CHANGE_ME，请先配置生产密钥和密码。" >&2
  exit 1
fi

# 本地模型依赖（torch / sentence-transformers）已从镜像移除，embedding 只能走远端 API。
embedding_backend="$(sed -n 's/^EMBEDDING_BACKEND=//p' .env | tail -n 1 | tr -d '\r')"
if [[ "${embedding_backend:-api}" != "api" ]]; then
  echo "EMBEDDING_BACKEND=${embedding_backend}：本地 embedding 依赖已移除，只能使用 api。" >&2
  exit 1
fi

rerank_backend="$(sed -n 's/^RERANK_BACKEND=//p' .env | tail -n 1 | tr -d '\r')"
if [[ "${rerank_backend:-api}" != "api" ]]; then
  echo "RERANK_BACKEND=${rerank_backend}：本地 CrossEncoder 依赖已移除，只能使用 api。" >&2
  exit 1
fi

mkdir -p logs reports models

docker compose pull mysql etcd minio milvus
docker compose up -d --build
docker compose ps

echo "部署完成。请检查：http://127.0.0.1:8080/health"
