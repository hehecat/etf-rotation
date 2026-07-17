#!/usr/bin/env python3
"""对比成交假设: 当日收盘 vs T+1开盘.

用法:
  python3 scripts/compare_fill.py
  python3 scripts/compare_fill.py --count 800
  python3 scripts/compare_fill.py --count 2500 --adjust qfq
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod
from etf_rotation import data as data_mod
from etf_rotation.backtest import bt, format_result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="c01")
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--adjust", default="none", choices=["none", "qfq", "hfq"])
    args = ap.parse_args()

    strat = cfgmod.load_strategy(args.strategy)
    pool = cfgmod.pool_as_dict()
    bench = strat.get("bench", "SH510300")
    codes = list(dict.fromkeys([bench] + list(pool.keys())))

    print("=" * 72)
    print(f"成交假设对比 · {strat.get('name')} · count={args.count} adjust={args.adjust}")
    print("=" * 72)
    print("📥 取数中...")
    raw = data_mod.fetch_many(codes, count=args.count, adjust=args.adjust, min_bars=100)
    all_data = {c: {**bars, "name": pool.get(c, c)} for c, bars in raw.items()}
    print(f"  有效: {len(all_data)} 只")
    if not all_data:
        return
    sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in all_data]))
    print(f"  交集: {sd[0]} ~ {sd[-1]} ({len(sd)} 天)\n")

    # 有多少带 open
    n_open = sum(1 for c, d in all_data.items() if d.get("open") and len(d["open"]) == len(d["close"]))
    print(f"  含开盘价: {n_open}/{len(all_data)}")

    base = cfgmod.strategy_for_backtest(strat)
    comm = float(strat.get("commission", 0.00005))

    results = {}
    for fill, label in [
        ("close", "A. 当日收盘成交 (旧假设)"),
        ("next_open", "B. T+1开盘成交 (可执行)"),
    ]:
        p = {**base, "fill": fill}
        r = bt(all_data, p, commission=comm)
        results[fill] = r
        print(format_result(r, label))
        if r:
            print(f"      区间 {r['d0']}~{r['d1']}  天数{r['days']}  终值{r['fv']:,.0f}  胜率{r['wr']:.0f}%")

    a, b = results.get("close"), results.get("next_open")
    if a and b:
        print("\n" + "=" * 72)
        print("差异 (B − A, 负=开盘更差)")
        print("=" * 72)
        print(f"  总收益:  {a['ret']*100:+.1f}% → {b['ret']*100:+.1f}%   Δ {(b['ret']-a['ret'])*100:+.1f}pp")
        print(f"  年化:    {a['ann']:+.1f}% → {b['ann']:+.1f}%   Δ {b['ann']-a['ann']:+.1f}pp")
        print(f"  最大回撤:{a['dd']*100:.1f}% → {b['dd']*100:.1f}%   Δ {(b['dd']-a['dd'])*100:+.1f}pp")
        print(f"  夏普:    {a['sp']:.2f} → {b['sp']:.2f}   Δ {b['sp']-a['sp']:+.2f}")
        print(f"  交易次数:{a['n']} → {b['n']}")
        if b.get("n_gaps"):
            print(f"  成交跳空(开盘/信号收盘−1) 均值: {b['avg_gap_pct']:+.2f}%  (样本{b['n_gaps']}次)")
            print("  说明: 均值为负 ≈ 买在高开/卖在低开 的平均损耗")
        print("\n结论提示:")
        print("  · 若 Δ收益明显为负: 旧回测偏乐观, 实盘应按 T+1 开盘预期")
        print("  · 邮件 15:35 信号 → 实盘宜次日开盘执行, 与 B 一致")
        print("  · C01 参数仍冻结; 本脚本只验证成交假设, 不改生产配置")


if __name__ == "__main__":
    main()
