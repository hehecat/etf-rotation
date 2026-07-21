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

try:
    from etf_rotation.research_mainline import MONITOR_SHADOWS, SIGNAL_SHADOW

    _DEFAULT_SHADOW = SIGNAL_SHADOW
    _DEFAULT_EXTRA = ",".join(MONITOR_SHADOWS)
except Exception:
    _DEFAULT_SHADOW = "c01_q10_vt08_soft_oh38_xgn"
    _DEFAULT_EXTRA = (
        "c01_q10_vt08_soft_oh38,c01_q10_vt08_soft_oh38_xgn,"
        "c01_q10_vt09_oh35,c01_q10_vt11"
    )


def main():
    ap = argparse.ArgumentParser(description="ETF轮动生产信号 (C01)")
    ap.add_argument("--dry-run", action="store_true", help="不改生产模拟仓位")
    ap.add_argument("--strategy", default="c01", help="策略配置名 (默认 c01)")
    ap.add_argument("--pool", default="pool", help="ETF池 (默认 pool=去重版; pool_full=原多票)")
    ap.add_argument(
        "--shadow",
        default=_DEFAULT_SHADOW,
        help="主研究影子 (默认 xgn; 报告焦点)",
    )
    ap.add_argument(
        "--extra-shadows",
        default=_DEFAULT_EXTRA,
        help="主线其它影子逗号分隔 (默认 MONITOR_SHADOWS, 与主影子去重后批量成交)",
    )
    ap.add_argument(
        "--no-extra-shadows",
        action="store_true",
        help="仅更新 --shadow, 不跑主线其它影子",
    )
    ap.add_argument("--bars", type=int, default=120, help="K线根数 (regime 需≥60, 默认120)")
    ap.add_argument(
        "--shadow-exec",
        action="store_true",
        help="强制更新影子独立仓位 (即使 --dry-run 也写 shadow_states/)",
    )
    ap.add_argument(
        "--no-shadow-exec",
        action="store_true",
        help="禁止影子模拟成交/写仓",
    )
    args = ap.parse_args()
    # 默认: 非 dry-run 时更新影子; dry-run 不写影子, 除非 --shadow-exec
    if args.no_shadow_exec:
        sh_exec = False
    elif args.shadow_exec:
        sh_exec = True
    else:
        sh_exec = not args.dry_run
    extra = []
    if not args.no_extra_shadows:
        extra = [x.strip() for x in (args.extra_shadows or "").split(",") if x.strip()]
    run_signal(
        strategy_name=args.strategy,
        shadow_name=args.shadow,
        bar_count=args.bars,
        dry_run=args.dry_run,
        pool_name=args.pool,
        shadow_execute=sh_exec,
        extra_shadows=extra,
    )


if __name__ == "__main__":
    main()
