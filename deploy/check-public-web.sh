#!/usr/bin/env bash
# 生产环境 config 片段：公网无鉴权访问
# 用法: 合并到 config.yaml 的 web 段，或手动修改

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="${ROOT}/config.yaml"

if [[ ! -f "$CFG" ]]; then
  echo "请先创建 config.yaml: cp config.example.yaml config.yaml"
  exit 1
fi

echo "请确认 config.yaml 中 web 段为:"
echo ""
echo "web:"
echo "  enabled: true"
echo "  host: 0.0.0.0"
echo "  port: 8089"
echo ""
echo "然后执行: python main.py"
echo "公网访问: http://<服务器IP>:8089"
