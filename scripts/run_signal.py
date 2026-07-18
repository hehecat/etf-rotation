#!/usr/bin/env python3
"""生产信号入口: C01 (冻结).

用法:
  python3 scripts/run_signal.py
  python3 scripts/run_signal.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 保证包可导入
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.signal import run_signal  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="ETF轮动生产信号 (C01)")
    ap.add_argument("--dry-run", action="store_true", help="只出信号不改模拟仓位")
    ap.add_argument("--strategy", default="c01", help="策略配置名 (默认 c01)")
    ap.add_argument("--pool", default="pool", help="ETF池 (默认 pool=去重版; pool_full=原多票)")
    ap.add_argument("--shadow", default="c13_shadow", help="影子策略配置名")
    ap.add_argument("--bars", type=int, default=60, help="K线根数")
    args = ap.parse_args()
    run_signal(
        strategy_name=args.strategy,
        shadow_name=args.shadow,
        bar_count=args.bars,
        dry_run=args.dry_run,
        pool_name=args.pool,
    )


if __name__ == "__main__":
    main()
