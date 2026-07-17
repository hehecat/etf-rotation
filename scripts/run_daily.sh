#!/usr/bin/env bash
# cron 入口 — 生产信号
set -euo pipefail
ROOT="/home/abc/etf-rotation"
LOG_DIR="/home/abc/桌面/ETF轮动信号/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/etf_$(date +%Y%m).log"
cd "$ROOT"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
# 确保 npx 可用
if ! command -v npx >/dev/null 2>&1; then
  echo "[$(date -Iseconds)] ERROR: npx not found" >> "$LOG_FILE"
  exit 1
fi
python3 "$ROOT/scripts/run_signal.py" >> "$LOG_FILE" 2>&1
