#!/usr/bin/env python3
"""三方池子对照: 原池 / 去重池 / 质量优选池 (均为 next_open).

用法:
  python3 scripts/compare_pools.py
  python3 scripts/compare_pools.py --count 500 --strategy c01
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
    print(f"📥 取数 pool={pool_name} n={len(codes)} ...")
    raw = data_mod.fetch_many(codes, count=count, adjust=adjust, min_bars=80)
    all_data = {c: {**bars, "name": pool.get(c, c)} for c, bars in raw.items()}
    missing = [c for c in codes if c not in all_data]
    print(f"   有效 {len(all_data)}  缺失 {len(missing)}")
    if missing[:12]:
        print(f"   缺失样例: {', '.join(missing[:12])}{'...' if len(missing)>12 else ''}")
    if all_data:
        sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in all_data]))
        if sd:
            print(f"   全交集 {sd[0]}~{sd[-1]} ({len(sd)}天) — 回测内部亦用全交集")
    cache[pool_name] = all_data
    return all_data


def common_range(cache: dict) -> tuple[str, str] | None:
    """各池全交集的再交集, 用于公平对齐区间."""
    ranges = []
    for data in cache.values():
        if not data:
            continue
        sd = sorted(set.intersection(*[set(d["dates"]) for d in data.values()]))
        if len(sd) >= 80:
            ranges.append((sd[0], sd[-1], set(sd)))
    if not ranges:
        return None
    inter = set.intersection(*[r[2] for r in ranges])
    if len(inter) < 80:
        return None
    sd = sorted(inter)
    return sd[0], sd[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="c01")
    ap.add_argument("--count", type=int, default=500)
    ap.add_argument("--adjust", default="none", choices=["none", "qfq", "hfq"])
    ap.add_argument("--fill", default="next_open", choices=["close", "next_open"])
    ap.add_argument(
        "--align",
        action="store_true",
        help="用各池共同可交易区间对齐后再比 (更公平)",
    )
    args = ap.parse_args()

    strat = cfgmod.load_strategy(args.strategy)
    base = cfgmod.strategy_for_backtest(strat)
    base["fill"] = args.fill
    comm = float(strat.get("commission", 0.00005))

    print("=" * 78)
    print(
        f"池子对照 · {strat.get('name')} · fill={args.fill} "
        f"count={args.count} adjust={args.adjust}"
    )
    print("=" * 78)

    cache: dict = {}
    cases = [
        ("A. 原多票池 pool_full", "pool_full"),
        ("B. 去重生产池 pool", "pool"),
        ("C. 质量优选 pool_quality", "pool_quality"),
    ]
    for _, pool_name in cases:
        load_data(pool_name, args.count, args.adjust, cache)

    date_range = common_range(cache) if args.align else None
    if date_range:
        print(f"\n📌 对齐区间: {date_range[0]} ~ {date_range[1]}\n")
    elif args.align:
        print("\n⚠️ 无法对齐共同区间, 退回各自全交集\n")

    results = []
    for label, pool_name in cases:
        data = cache.get(pool_name) or {}
        if len(data) < 10:
            print(f"  {label}: 数据不足\n")
            results.append((label, None, 0))
            continue
        r = bt(data, base, date_range=date_range, commission=comm)
        results.append((label, r, len(data)))
        print(format_result(r, label))
        if r:
            gap = f" 跳空{r['avg_gap_pct']:+.2f}%" if r.get("n_gaps") else ""
            print(
                f"      池有效{len(data)} 区间{r['d0']}~{r['d1']}({r['days']}天) "
                f"终值{r['fv']:,.0f} 胜率{r['wr']:.0f}% 交易{r['n']}{gap}\n"
            )

    ok = [(l, r, n) for l, r, n in results if r]
    if len(ok) >= 2:
        a = ok[0][1]
        print("=" * 78)
        print("相对 A (多票池) 的差分")
        print("=" * 78)
        for label, r, n in ok[1:]:
            print(
                f"  {label}: 收益 {(r['ret']-a['ret'])*100:+.1f}pp  "
                f"年化 {r['ann']-a['ann']:+.1f}pp  "
                f"回撤 {(r['dd']-a['dd'])*100:+.1f}pp  "
                f"夏普 {r['sp']-a['sp']:+.2f}"
            )
        print("\n说明:")
        print("  · 默认各自全交集; --align 用共同区间更公平")
        print("  · 生产默认 pool = 去重版; 多票备份 pool_full")
        print("  · 本脚本只读验证, 不改策略权重")


if __name__ == "__main__":
    main()
