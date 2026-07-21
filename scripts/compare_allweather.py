#!/usr/bin/env python3
"""全天候候选对照: C01 vs top2 分散 vs pure_eff/G15.

验收口径 (结构牛/全天候优先):
  1) 分年超额: beat_years / 总年数
  2) 最差年超额 min_excess
  3) 全样本收益/回撤/夏普 (T+1 开盘)
  4) 不改生产 C01 冻结参数

用法:
  python3 scripts/compare_allweather.py
  python3 scripts/compare_allweather.py --count 640 --core-start 2024-01-02
"""
from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod
from etf_rotation import data as data_mod
from etf_rotation.backtest import bt, format_result


def load_data(pool_name: str, count: int, adjust: str) -> dict:
    pool_cfg = cfgmod.load_pool(pool_name)
    pool = cfgmod.pool_as_dict(pool_cfg)
    bench = pool_cfg.get("bench", "SH510300")
    codes = list(dict.fromkeys([bench] + list(pool.keys())))
    print(f"📥 取数 pool={pool_name} codes={len(codes)} count={count} ...")
    raw: dict = {}
    # 分块取数, 避免一次并发过大卡死
    for i in range(0, len(codes), 10):
        chunk = codes[i : i + 10]
        part = data_mod.fetch_many(chunk, count=count, adjust=adjust, min_bars=80, max_workers=5)
        raw.update(part)
        print(f"   chunk {i // 10 + 1}: {len(part)}/{len(chunk)}")
    all_data = {c: {**bars, "name": pool.get(c, c)} for c, bars in raw.items()}
    print(f"   有效 {len(all_data)}")
    if all_data:
        sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in all_data]))
        print(f"   全池交集 {sd[0]}~{sd[-1]} ({len(sd)}天)")
    return all_data


def core_slice(all_data: dict, core_start: str, bench: str) -> dict:
    core = {
        c: d
        for c, d in all_data.items()
        if d["dates"][0] <= core_start or c == bench
    }
    if not core:
        return all_data
    sd = sorted(set.intersection(*[set(core[c]["dates"]) for c in core]))
    print(f"   核心池 {len(core)} 只, 交集 {sd[0]}~{sd[-1]} ({len(sd)}天)")
    return core


def bench_ret(data: dict, bench: str, d0: str, d1: str) -> float | None:
    bc = data.get(bench)
    if not bc:
        return None
    pairs = [(d, c) for d, c in zip(bc["dates"], bc["close"]) if d0 <= d <= d1]
    if len(pairs) < 30:
        return None
    return pairs[-1][1] / pairs[0][1] - 1


def yearly_excess(data: dict, p: dict, bench: str) -> tuple[list[tuple], int, int, float]:
    sd = sorted(set.intersection(*[set(data[c]["dates"]) for c in data]))
    ys = sorted({d[:4] for d in sd})
    rows = []
    beat = total = 0
    min_ex = 999.0
    for y in ys:
        ry = bt(data, p, date_range=(f"{y}-01-01", f"{y}-12-31"))
        if not ry or ry["days"] < 40:
            continue
        by = bench_ret(data, bench, f"{y}-01-01", f"{y}-12-31")
        if by is None:
            continue
        ex = ry["ret"] - by
        rows.append((y, ry["ret"], by, ex))
        total += 1
        if ex > 0:
            beat += 1
        min_ex = min(min_ex, ex)
    if total == 0:
        min_ex = 0.0
    return rows, beat, total, min_ex


def score_row(m: dict) -> float:
    """全天候分: 先分年不输, 再最差超额, 再夏普/收益/回撤."""
    return (
        m["beat"] * 100
        + m["min_ex"] * 100
        + m["sp"] * 5
        + m["ret"] * 10
        - abs(m["dd"]) * 20
    )


def eval_case(label: str, strat_name: str, data: dict, fill: str | None = None) -> dict | None:
    strat = cfgmod.load_strategy(strat_name)
    p = cfgmod.strategy_for_backtest(strat)
    if fill:
        p["fill"] = fill
    r = bt(data, p, commission=float(strat.get("commission", 0.00005)))
    if not r:
        print(f"  {label}: NO DATA")
        return None
    bench = p.get("bench", "SH510300")
    years, beat, total, min_ex = yearly_excess(data, p, bench)
    m = {
        "label": label,
        "name": strat.get("name", strat_name),
        "ret": r["ret"],
        "ann": r["ann"],
        "dd": r["dd"],
        "sp": r["sp"],
        "n": r["n"],
        "wr": r["wr"],
        "beat": beat,
        "total": total,
        "min_ex": min_ex,
        "years": years,
        "r": r,
        "p": p,
    }
    m["score"] = score_row(m)
    print(format_result(r, label))
    ytxt = " | ".join(
        f"{y}:{sr*100:+.1f}/{br*100:+.1f}({ex*100:+.1f})" for y, sr, br, ex in years
    )
    print(
        f"      beat {beat}/{total}  min_ex={min_ex*100:+.1f}%  "
        f"score={m['score']:.1f}  top_n={p.get('top_n')} w={p.get('w')}"
    )
    if ytxt:
        print(f"      年: {ytxt}")
    return m


def main():
    ap = argparse.ArgumentParser(description="全天候候选对照 (不改生产 C01)")
    ap.add_argument("--count", type=int, default=640)
    ap.add_argument("--adjust", default="none", choices=["none", "qfq", "hfq"])
    ap.add_argument("--pool", default="pool")
    ap.add_argument("--core-start", default="2024-01-02", help="核心长样本: 仅保留此日及之前上市的ETF")
    ap.add_argument("--fill", default="next_open", choices=["close", "next_open"])
    args = ap.parse_args()

    print("=" * 86)
    print(f"全天候对照 · count={args.count} adjust={args.adjust} fill={args.fill}")
    print("目标: 结构牛/宽基年少输300, 主题年保留超额; C01 仍冻结")
    print("=" * 86)

    all_data = load_data(args.pool, args.count, args.adjust)
    if not all_data:
        print("无数据")
        return
    bench = cfgmod.load_pool(args.pool).get("bench", "SH510300")
    core = core_slice(all_data, args.core_start, bench)

    cases = [
        ("A.C01冻结生产", "c01"),
        ("B.C01_park底仓300", "c01_park"),
        ("C.C01_improved", "c01_improved"),
        ("D.C01_AW pure_eff×2", "c01_aw"),
        ("E.C01_AW_G15×2", "c01_aw_g15"),
        ("F.C01_AW_v2软趋势", "c01_aw_v2"),
        ("G.C01_AW_slopeR2", "c01_aw_slope"),
        ("H.C01_AW_mix", "c01_aw_mix"),
        ("I.C01_AW_multi", "c01_aw_multi"),
        ("J.C01_AW_v3宽度软趋势", "c01_aw_v3"),
        ("K.C01_AW_v4底仓卫星", "c01_aw_v4"),
    ]

    print("\n--- 核心长样本 (更考验结构年) ---")
    core_rows = []
    for label, name in cases:
        m = eval_case(label, name, core, fill=args.fill)
        if m:
            core_rows.append(m)

    print("\n--- 全池短样本 (当前生产池交集) ---")
    all_rows = []
    for label, name in cases:
        m = eval_case(label, name, all_data, fill=args.fill)
        if m:
            all_rows.append(m)

    def rank_print(title: str, rows: list[dict]):
        if not rows:
            return
        print("\n" + "=" * 86)
        print(title)
        print("=" * 86)
        rows = sorted(rows, key=lambda x: x["score"], reverse=True)
        for i, m in enumerate(rows, 1):
            print(
                f"  #{i} {m['label']:22s} score={m['score']:6.1f}  "
                f"beat={m['beat']}/{m['total']} min_ex={m['min_ex']*100:+5.1f}%  "
                f"ret={m['ret']*100:+6.1f}% dd={m['dd']*100:5.1f}% sp={m['sp']:.2f}"
            )
        best = rows[0]
        print("\n推荐研究主线:", best["label"], f"({best['name']})")
        print("  · 生产 C01 保持冻结; 本脚本结果仅研究/影子对照")
        print("  · pure_eff top2: 分年超额更稳, 回撤更浅")
        print("  · soft_trend: 趋势关停靠300, 减少结构牛现金拖累")
        print("  · slope_r2: 社区斜率×R²动量, 与 pure_eff 对照")
        print("  · multi: 黄金/纳指进入可交易宇宙 (池内需有码)")

    rank_print("核心样本排名 (全天候分)", core_rows)
    rank_print("全池短样本排名 (参考)", all_rows)


if __name__ == "__main__":
    main()
