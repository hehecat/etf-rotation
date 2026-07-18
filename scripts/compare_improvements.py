#!/usr/bin/env python3
"""本地改进对照: C01 原版 vs 实现对齐/改进项.

对比维度:
  A. 生产池 + close 成交 (旧乐观假设)
  B. 生产池 + next_open (可执行)
  C. 去重池 + next_open
  D. 改进配置(方向eff+绝对动量) + 去重池 + next_open

用法:
  python3 scripts/compare_improvements.py
  python3 scripts/compare_improvements.py --count 500 --adjust none
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


def load_data(pool_name: str, count: int, adjust: str, cache: dict) -> dict:
    if pool_name in cache:
        return cache[pool_name]
    pool_cfg = cfgmod.load_pool(pool_name)
    pool = cfgmod.pool_as_dict(pool_cfg)
    bench = pool_cfg.get("bench", "SH510300")
    codes = list(dict.fromkeys([bench] + list(pool.keys())))
    print(f"📥 取数 pool={pool_name} codes={len(codes)} ...")
    raw = data_mod.fetch_many(codes, count=count, adjust=adjust, min_bars=100)
    all_data = {c: {**bars, "name": pool.get(c, c)} for c, bars in raw.items()}
    print(f"   有效 {len(all_data)}")
    if all_data:
        sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in all_data]))
        print(f"   交集 {sd[0]}~{sd[-1]} ({len(sd)}天)")
    cache[pool_name] = all_data
    return all_data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--adjust", default="none", choices=["none", "qfq", "hfq"])
    args = ap.parse_args()

    print("=" * 78)
    print(f"本地改进对照 · count={args.count} adjust={args.adjust}")
    print("=" * 78)

    cache: dict = {}
    # 生产 pool.json 已=去重版; 原多票池备份为 pool_full
    cases = [
        ("A.多票池+收盘成交(旧)", "c01", "pool_full", "close"),
        ("B.多票池+T+1开盘", "c01", "pool_full", "next_open"),
        ("C.去重池+T+1开盘(生产池)", "c01", "pool", "next_open"),
        ("D.改进+去重+T+1", "c01_improved", "pool", "next_open"),
        ("E.改进+多票池+T+1", "c01_improved", "pool_full", "next_open"),
    ]

    results = []
    for label, strat_name, pool_name, fill in cases:
        strat = cfgmod.load_strategy(strat_name)
        data = load_data(pool_name, args.count, args.adjust, cache)
        if not data:
            print(f"  {label}: NO DATA")
            continue
        p = cfgmod.strategy_for_backtest(strat)
        p["fill"] = fill
        comm = float(strat.get("commission", 0.00005))
        r = bt(data, p, commission=comm)
        results.append((label, r))
        print(format_result(r, label))
        if r:
            extra = f" 跳空{r['avg_gap_pct']:+.2f}%" if r.get("n_gaps") else ""
            print(
                f"      终值{r['fv']:,.0f} 胜率{r['wr']:.0f}% 交易{r['n']}"
                f" signed_eff={p.get('signed_eff')}{extra}"
            )

    if len(results) >= 2 and results[0][1] and results[1][1]:
        a, b = results[0][1], results[1][1]
        print("\n" + "=" * 78)
        print("关键差分")
        print("=" * 78)
        print(
            f"  成交假设(B−A): 收益 {(b['ret']-a['ret'])*100:+.1f}pp  "
            f"年化 {b['ann']-a['ann']:+.1f}pp  夏普 {b['sp']-a['sp']:+.2f}"
        )
        if len(results) >= 4 and results[3][1]:
            d = results[3][1]
            print(
                f"  全改进(D−A):   收益 {(d['ret']-a['ret'])*100:+.1f}pp  "
                f"年化 {d['ann']-a['ann']:+.1f}pp  夏普 {d['sp']-a['sp']:+.2f}"
            )
            print(
                f"  全改进(D−B):   收益 {(d['ret']-b['ret'])*100:+.1f}pp  "
                f"(相对可执行基线)"
            )
        print("\n说明:")
        print("  · A 是旧回测乐观上界; B 更接近实盘可执行")
        print("  · 生产 C01 权重仍冻结; D/E 仅本地对照, 不自动上线")
        print("  · 交易日调仓已在 portfolio/回测对齐 (rb 语义一致)")


if __name__ == "__main__":
    main()
