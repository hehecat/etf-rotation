#!/usr/bin/env python3
"""回测入口 — 验证冻结策略, 默认不改生产配置.

用法:
  python3 scripts/run_backtest.py
  python3 scripts/run_backtest.py --strategy c01 --count 500
  python3 scripts/run_backtest.py --strategy c01 --count 2500 --adjust qfq
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod  # noqa: E402
from etf_rotation import data as data_mod  # noqa: E402
from etf_rotation.backtest import bt, format_result  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="ETF轮动回测 (只读验证)")
    ap.add_argument("--strategy", default="c01")
    ap.add_argument("--count", type=int, default=500, help="K线根数, 长样本可 2500")
    ap.add_argument("--adjust", default="none", choices=["none", "qfq", "hfq"])
    ap.add_argument("--commission", type=float, default=None)
    args = ap.parse_args()

    strat = cfgmod.load_strategy(args.strategy)
    if strat.get("frozen"):
        print(f"📌 策略 {strat['name']} 已冻结 — 本脚本仅验证, 不改生产参数\n")

    pool = cfgmod.pool_as_dict()
    bench = strat.get("bench", "SH510300")
    codes = list(dict.fromkeys([bench] + list(pool.keys())))

    print(f"📥 取数 count={args.count} adjust={args.adjust} ...")
    raw = data_mod.fetch_many(codes, count=args.count, adjust=args.adjust, min_bars=100)
    all_data = {}
    for c, bars in raw.items():
        all_data[c] = {
            **bars,
            "name": pool.get(c, c),
        }
    print(f"  有效: {len(all_data)} 只")
    if all_data:
        sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in all_data]))
        print(f"  交集日期: {sd[0]} ~ {sd[-1]} ({len(sd)} 天)\n")

    p = cfgmod.strategy_for_backtest(strat)
    comm = args.commission if args.commission is not None else float(strat.get("commission", 0.00005))
    r = bt(all_data, p, commission=comm)
    print(format_result(r, strat.get("name", args.strategy)))
    if r:
        print(f"\n  区间: {r['d0']} ~ {r['d1']}  天数:{r['days']}")
        print(f"  终值: {r['fv']:,.0f}  胜率:{r['wr']:.0f}%")


if __name__ == "__main__":
    main()
