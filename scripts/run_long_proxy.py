#!/usr/bin/env python3
"""长历史代理回测 — 用长寿股票/宽基近似验证全天候策略.

动机:
  多数行业 ETF 只有 2~5 年历史, 无法验证 10 年+ 持有假设.
  本脚本用跨行业龙头股 + 宽基 ETF 做代理宇宙, 在本地磁盘缓存上跑长样本.

重要限制:
  - 存活偏差: 现在仍在的龙头 ≠ 过去可投全集
  - 个股特有风险/涨跌停/停牌 与 ETF 不同
  - 结论只回答规则是否在长样本系统性失效, 不能直接当 ETF 实盘期望

用法:
  python3 scripts/run_long_proxy.py
  python3 scripts/run_long_proxy.py --count 3200 --strategies c01,c01_aw_v3
  python3 scripts/run_long_proxy.py --force-refresh
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod
from etf_rotation import data as data_mod
from etf_rotation.backtest import bt, format_result


def load_proxy_data(
    pool_name: str,
    count: int,
    adjust: str,
    force_refresh: bool,
    min_start: str | None,
    min_bars: int,
) -> tuple[dict, str]:
    pool_cfg = cfgmod.load_pool(pool_name)
    pool = cfgmod.pool_as_dict(pool_cfg)
    bench = pool_cfg.get("bench", "SH510300")
    codes = list(dict.fromkeys([bench] + list(pool.keys())))
    print(f"📥 取数(磁盘缓存) pool={pool_name} codes={len(codes)} count={count} adjust={adjust}")
    print(f"   cache={data_mod.cache_info()['dir']}")
    t0 = time.time()
    raw = data_mod.fetch_many(
        codes,
        count=count,
        adjust=adjust,
        min_bars=min_bars,
        max_workers=6,
        force_refresh=force_refresh,
        use_disk=True,
    )
    # 基准单独强取, 避免并行时漏掉导致无法对比
    if bench not in raw:
        bb = data_mod.fetch_bench(bench, count=count, min_bars=min_bars, force_refresh=force_refresh)
        # fetch_bench 默认 none/auto; 再按 adjust 补一次
        if not bb:
            kl = data_mod.fetch_klines(bench, count=count, adjust=adjust, force_refresh=force_refresh)
            bb = data_mod.normalize_bars(kl, min_bars=min_bars)
        if bb:
            raw[bench] = bb
            print(f"   bench force-ok {bench} n={len(bb['dates'])}")
        else:
            print(f"   ⚠️ bench missing {bench}")
    info = data_mod.cache_info()
    print(
        f"   有效 {len(raw)}/{len(codes)}  用时 {time.time()-t0:.1f}s  "
        f"cache_files={info['files']}"
    )
    all_data = {c: {**bars, "name": pool.get(c, c)} for c, bars in raw.items()}

    # 去掉非正价格点过多的标的
    cleaned = {}
    for c, d in all_data.items():
        bad = sum(1 for x in d["close"] if x is None or x <= 0)
        if bad > 0 and bad / max(len(d["close"]), 1) > 0.01:
            print(f"   drop {c}: bad_px={bad}/{len(d['close'])}")
            continue
        if bad:
            # 少量坏点: 用前值填充
            closes = list(d["close"])
            opens = list(d["open"])
            last = None
            for i, px in enumerate(closes):
                if px is None or px <= 0:
                    if last is not None:
                        closes[i] = last
                        opens[i] = last
                else:
                    last = px
            d = {**d, "close": closes, "open": opens}
        cleaned[c] = d
    all_data = cleaned

    if min_start:
        kept = {}
        drop = []
        for c, d in all_data.items():
            if c == bench:
                kept[c] = d
                continue
            d0 = d["dates"][0] if d.get("dates") else "9999"
            if d0 <= min_start:
                kept[c] = d
            else:
                drop.append((c, d0))
        if drop:
            drop_s = ", ".join(f"{c}({d0})" for c, d0 in drop[:8])
            print(
                f"   min_start={min_start} 剔除 {len(drop)} 只短窗: "
                f"{drop_s}{'...' if len(drop) > 8 else ''}"
            )
        all_data = kept

    if all_data:
        sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in all_data]))
        print(f"   交集 {sd[0]}~{sd[-1]} ({len(sd)}天) n={len(all_data)}")
    return all_data, bench


def bench_buyhold(data: dict, bench: str, warm: int = 65) -> dict | None:
    if bench not in data:
        return None
    bc = data[bench]
    if len(bc["close"]) <= warm + 10:
        return None
    c0 = bc["close"][warm]
    c1 = bc["close"][-1]
    if c0 <= 0 or c1 <= 0:
        return None
    days = len(bc["close"]) - warm
    ret = c1 / c0 - 1
    ann = ((c1 / c0) ** (250 / days) - 1) * 100 if days else 0
    pk = c0
    mdd = 0.0
    for x in bc["close"][warm:]:
        if x <= 0:
            continue
        pk = max(pk, x)
        mdd = min(mdd, x / pk - 1)
    return {
        "ret": ret,
        "ann": ann,
        "dd": mdd,
        "d0": bc["dates"][warm],
        "d1": bc["dates"][-1],
        "days": days,
    }


def yearly_vs_bench(data: dict, p: dict, bench: str) -> list[tuple]:
    if bench not in data:
        # 回测参数里的 bench 若未进池, 用数据里任意宽基近似
        for alt in ("SH510300", "SH510050", "SZ159915"):
            if alt in data:
                bench = alt
                break
        else:
            return []
    sd = sorted(set.intersection(*[set(data[c]["dates"]) for c in data]))
    rows = []
    for y in sorted({d[:4] for d in sd}):
        ry = bt(data, p, date_range=(f"{y}-01-01", f"{y}-12-31"))
        if not ry or ry["days"] < 40:
            continue
        pairs = [
            (d, c)
            for d, c in zip(data[bench]["dates"], data[bench]["close"])
            if f"{y}-01-01" <= d <= f"{y}-12-31" and c > 0
        ]
        if len(pairs) < 40:
            continue
        by = pairs[-1][1] / pairs[0][1] - 1
        rows.append((y, ry["ret"], by, ry["ret"] - by, ry["dd"], ry["sp"]))
    return rows


def eval_strategy(label: str, strat_name: str, data: dict, fill: str | None) -> dict | None:
    strat = cfgmod.load_strategy(strat_name)
    p = cfgmod.strategy_for_backtest(strat)
    if fill:
        p["fill"] = fill
    # 股票代理佣金更保守
    comm = max(float(strat.get("commission", 0.0005)), 0.0003)
    r = bt(data, p, commission=comm)
    if not r:
        print(f"  {label}: NO DATA")
        return None
    years = yearly_vs_bench(data, p, p.get("bench", "SH510300"))
    beat = sum(1 for row in years if row[3] > 0)
    total = len(years)
    min_ex = min((row[3] for row in years), default=0.0)
    score = beat * 100 + min_ex * 100 + r["sp"] * 5 + r["ret"] * 10 - abs(r["dd"]) * 20
    print(format_result(r, label))
    print(
        f"      beat {beat}/{total} min_ex={min_ex*100:+.1f}% score={score:.1f} "
        f"comm={comm} top_n={p.get('top_n')} soft={p.get('soft_trend')} bm={p.get('bm')}"
    )
    if years:
        ytxt = " | ".join(
            f"{y}:{sr*100:+.1f}/{br*100:+.1f}({ex*100:+.1f})"
            for y, sr, br, ex, dd, sp in years
        )
        print(f"      年: {ytxt}")
    return {
        "label": label,
        "name": strat.get("name", strat_name),
        "r": r,
        "beat": beat,
        "total": total,
        "min_ex": min_ex,
        "score": score,
        "years": years,
        "p": p,
    }


def main():
    ap = argparse.ArgumentParser(description="长历史股票代理回测 (磁盘缓存)")
    ap.add_argument("--pool", default="pool_long_proxy")
    ap.add_argument("--count", type=int, default=3200, help="K线根数, 约 12 年+")
    ap.add_argument(
        "--adjust",
        default="none",
        choices=["none", "qfq", "hfq"],
        help="复权; 股票长历史建议 none (部分源 qfq 会出现负价格)",
    )
    ap.add_argument(
        "--min-start",
        default="2014-01-01",
        help="窗口起点不晚于此日的代理才保留, 用于拉长交集",
    )
    ap.add_argument("--min-bars", type=int, default=200)
    ap.add_argument("--fill", default="next_open", choices=["close", "next_open"])
    ap.add_argument(
        "--strategies",
        default="c01,c01_park,c01_aw_v3,c01_aw_v4,c01_aw_v4_1,c01_aw_g15",
        help="逗号分隔策略名",
    )
    ap.add_argument("--force-refresh", action="store_true")
    args = ap.parse_args()

    print("=" * 86)
    print("长历史代理回测 · 仅研究稳健性, 非 ETF 实盘期望")
    print("=" * 86)

    data, bench = load_proxy_data(
        args.pool,
        args.count,
        args.adjust,
        args.force_refresh,
        args.min_start or None,
        args.min_bars,
    )
    if len(data) < 8:
        print("有效标的过少, 停止")
        return

    bh = bench_buyhold(data, bench)
    if bh:
        print(
            f"\n📌 基准买入持有 {bench}: 收益{bh['ret']*100:+.1f}% 年化{bh['ann']:+.1f}% "
            f"回撤{bh['dd']*100:.1f}%  {bh['d0']}~{bh['d1']} ({bh['days']}天)"
        )

    rows = []
    for name in [s.strip() for s in args.strategies.split(",") if s.strip()]:
        try:
            m = eval_strategy(name, name, data, args.fill)
        except FileNotFoundError as e:
            print(f"  skip {name}: {e}")
            continue
        if m:
            rows.append(m)

    if not rows:
        return
    rows.sort(key=lambda x: x["score"], reverse=True)
    print("\n" + "=" * 86)
    print("长样本排名 (全天候分 = beat年 + 最差超额 + 夏普/收益/回撤)")
    print("=" * 86)
    for i, m in enumerate(rows, 1):
        r = m["r"]
        print(
            f"  #{i} {m['label']:16s} score={m['score']:6.1f} beat={m['beat']}/{m['total']} "
            f"min_ex={m['min_ex']*100:+5.1f}% ret={r['ret']*100:+6.1f}% "
            f"dd={r['dd']*100:5.1f}% sp={r['sp']:.2f}"
        )
    best = rows[0]
    print("\n结论线索:")
    print(f"  · 长样本相对更稳: {best['label']} ({best['name']})")
    print("  · 若长样本 beat 年显著 < 50% 或 min_ex 极差, 说明规则不适合 10 年持有叙事")
    print("  · 股票代理有存活偏差; 最终仍要以可交易 ETF 池做执行层")
    print(f"  · K线缓存: {data_mod.cache_info()}")


if __name__ == "__main__":
    main()
