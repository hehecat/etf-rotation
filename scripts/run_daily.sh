#!/usr/bin/env bash
# cron 入口 — 日更编排 (signal + monitor + email)
# 生产策略仍为 c01; 研究影子默认 vt08_soft_oh38
set -euo pipefail

# 优先环境变量 / 脚本自定位, 兼容旧 /home/abc 路径
ROOT="${ETF_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
if [[ ! -d "$ROOT/scripts" && -d "/home/abc/etf-rotation" ]]; then
  ROOT="/home/abc/etf-rotation"
fi

# 日志: ETF_DATA_DIR > 桌面 > repo/output/logs
if [[ -n "${ETF_DATA_DIR:-}" ]]; then
  LOG_DIR="${ETF_DATA_DIR}/logs"
elif [[ -d "${HOME}/桌面" ]]; then
  LOG_DIR="${HOME}/桌面/ETF轮动信号/logs"
else
  LOG_DIR="${ROOT}/output/logs"
fi
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/etf_$(date +%Y%m).log"

cd "$ROOT"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

{
  echo "[$(date -Iseconds)] pipeline start ROOT=$ROOT"
  # 默认: 信号+监控(告警)+邮件+pages; 不默认 warmup
  # PIPELINE_WARMUP=1 开启暖机; PIPELINE_DRY_RUN=1 预演
  EXTRA=()
  if [[ "${PIPELINE_WARMUP:-0}" == "1" ]]; then
    EXTRA+=(--warmup)
  fi
  if [[ "${PIPELINE_DRY_RUN:-0}" == "1" ]]; then
    EXTRA+=(--dry-run)
  fi
  python3 "$ROOT/scripts/run_pipeline.py" \
    --steps signal,monitor,compare,live,summary,status,today,email,pages \
    --strategy c01 \
    --shadow c01_q10_vt08_soft_oh38_xgn \
    --append-shadow-email \
    --require-trading-day \
    --monitor-fail-on-alert \
    --pages-out "$ROOT/output/site" \
    "${EXTRA[@]}"
  echo "[$(date -Iseconds)] pipeline done exit=$?"
} >> "$LOG_FILE" 2>&1
