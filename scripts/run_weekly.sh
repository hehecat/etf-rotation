#!/usr/bin/env bash
# 周检入口: research_healthcheck + monitor 告警
set -euo pipefail

ROOT="${ETF_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
if [[ ! -d "$ROOT/scripts" && -d "/home/abc/etf-rotation" ]]; then
  ROOT="/home/abc/etf-rotation"
fi

if [[ -n "${ETF_DATA_DIR:-}" ]]; then
  LOG_DIR="${ETF_DATA_DIR}/logs"
elif [[ -d "${HOME}/桌面" ]]; then
  LOG_DIR="${HOME}/桌面/ETF轮动信号/logs"
else
  LOG_DIR="${ROOT}/output/logs"
fi
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/etf_weekly_$(date +%Y%m).log"

cd "$ROOT"
export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

{
  echo "[$(date -Iseconds)] weekly start ROOT=$ROOT"
  EXTRA=()
  if [[ "${WEEKLY_QUICK:-0}" == "1" ]]; then
    EXTRA+=(--quick)
  fi
  if [[ "${WEEKLY_PIPELINE_DRY:-0}" == "1" ]]; then
    EXTRA+=(--with-pipeline-dry)
  fi
  python3 "$ROOT/scripts/run_weekly.py" "${EXTRA[@]}"
  echo "[$(date -Iseconds)] weekly done exit=$?"
} >> "$LOG_FILE" 2>&1
