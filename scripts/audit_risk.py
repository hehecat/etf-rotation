#!/usr/bin/env python3
"""全天候风险验收 — 完整防御网格, 支持分批长时间跑.

重点: 控回撤, 不是追收益.
数据: 本地缓存的长历史股票/宽基代理池.

用法:
  # 先估规模
  python3 scripts/audit_risk.py --dry-count
  # 分批跑 (推荐)
  python3 scripts/audit_risk.py --batch 0 --batches 8
  python3 scripts/audit_risk.py --batch 1 --batches 8
  ...
  # 汇总
  python3 scripts/audit_risk.py --merge
  # 或一次全跑 (久)
  python3 scripts/audit_risk.py --all
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod
from etf_rotation import data as data_mod
from etf_rotation.backtest import bt

OUT_DIR = ROOT / "output" / "risk_audit"


def load_data(pool_name: str, count: int, adjust: str):
    pool_cfg = cfgmod.load_pool(pool_name)
    pool = cfgmod.pool_as_dict(pool_cfg)
    bench = pool_cfg.get("bench", "SH510300")
    codes = list(dict.fromkeys([bench] + list(pool.keys())))
    raw = data_mod.fetch_many(
        codes, count=count, adjust=adjust, min_bars=200, max_workers=6, use_disk=True
    )
    data = {}
    for c, bars in raw.items():
        closes = list(bars["close"])
        opens = list(bars.get("open") or closes)
        last = None
        bad = 0
        for i, px in enumerate(closes):
            if px is None or px <= 0:
                bad += 1
                if last is not None:
                    closes[i] = last
                    opens[i] = last
            else:
                last = px
        if c != bench and bad / max(len(closes), 1) > 0.05:
            continue
        data[c] = {**bars, "close": closes, "open": opens, "name": pool.get(c, c)}
    if bench not in data:
        kl = data_mod.fetch_klines(bench, count=count, adjust=adjust)
        bb = data_mod.normalize_bars(kl, min_bars=200)
        if bb:
            data[bench] = {**bb, "name": "沪深300ETF"}
    sd = sorted(set.intersection(*[set(data[c]["dates"]) for c in data]))
    return data, bench, sd


def regime_of_year(bench_ret: float) -> str:
    if bench_ret >= 0.15:
        return "bull"
    if bench_ret <= -0.10:
        return "bear"
    return "chop"


def year_bench_ret(data, bench, y):
    pairs = [
        (d, c)
        for d, c in zip(data[bench]["dates"], data[bench]["close"])
        if f"{y}-01-01" <= d <= f"{y}-12-31" and c > 0
    ]
    if len(pairs) < 40:
        return None
    return pairs[-1][1] / pairs[0][1] - 1


def build_candidates() -> list[tuple[str, dict]]:
    base = cfgmod.strategy_for_backtest(cfgmod.load_strategy("c01"))
    out: list[tuple[str, dict]] = []

    # 基线
    for nm in ["c01", "c01_park", "c01_aw_v3", "c01_aw_v4", "c01_aw_v4_1", "c01_aw_g15"]:
        out.append((nm, cfgmod.strategy_for_backtest(cfgmod.load_strategy(nm))))

    modes = [
        ("sat", False, False, False),
        ("park", False, True, True),
        ("soft", True, False, True),
        ("v4", True, True, True),
    ]
    weights = [
        ("c01w", 1, {"eff": 0.6, "mtf": 0.4}),
        ("pure", 1, {"eff": 1.0}),
        ("c01w2", 2, {"eff": 0.6, "mtf": 0.4}),
        ("pure2", 2, {"eff": 1.0}),
        ("g15", 2, {"eff": 0.5, "mtf": 0.3, "m20": 0.2}),
    ]

    # 完整防御网格: 仓位/宽度/模式/止损/双均
    for wn, tn, w in weights:
        for mode, soft, prefer, park in modes:
            for ps in [0.95, 0.80, 0.65, 0.50, 0.35]:
                for bm in [0.0, 0.20, 0.30, 0.40, 0.50]:
                    for stop in [-0.08, -0.06, -0.05]:
                        for dual in [False, True]:
                            p = deepcopy(base)
                            p.update(
                                {
                                    "w": w,
                                    "top_n": tn,
                                    "ps": ps,
                                    "bm": bm,
                                    "soft_trend": soft,
                                    "prefer_bench_if_stronger": prefer,
                                    "park_bench": park,
                                    "stop": stop,
                                    "dual_ma": dual,
                                    "hyst": 0.3 if tn == 2 else 0.2,
                                    "min_hold": 7 if tn == 2 else 5,
                                    "empty_free_entry": True,
                                    "fill": "next_open",
                                }
                            )
                            label = (
                                f"{wn}|{mode}|ps{ps}|bm{bm}|"
                                f"st{abs(int(round(stop*100)))}|dm{int(dual)}"
                            )
                            out.append((label, p))

    # 去重
    seen = set()
    uniq = []
    for name, p in out:
        key = (
            tuple(sorted((p.get("w") or {}).items())),
            p.get("top_n"),
            p.get("ps"),
            p.get("bm"),
            p.get("soft_trend"),
            p.get("prefer_bench_if_stronger"),
            p.get("park_bench"),
            p.get("stop"),
            p.get("dual_ma"),
            p.get("hyst"),
            p.get("min_hold"),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append((name, p))
    return uniq


def eval_one(name: str, p: dict, data: dict, sd: list[str], bench: str, comm: float):
    r = bt(data, p, commission=comm)
    if not r:
        return None
    # 不要把整条 equity 写进汇总文件
    r = {k: v for k, v in r.items() if k != "equity"}

    years = []
    for y in sorted({d[:4] for d in sd}):
        ry = bt(data, p, date_range=(f"{y}-01-01", f"{y}-12-31"), commission=comm)
        by = year_bench_ret(data, bench, y)
        if not ry or by is None or ry["days"] < 40:
            continue
        years.append(
            {
                "y": y,
                "ret": ry["ret"],
                "by": by,
                "ex": ry["ret"] - by,
                "dd": ry["dd"],
                "ann": ry["ann"],
                "sharpe": ry.get("sharpe", 0),
                "regime": regime_of_year(by),
            }
        )

    mid = len(years) // 2
    first, second = years[:mid], years[mid:]
    reg = {}
    for rg in ("bull", "bear", "chop"):
        sub = [x for x in years if x["regime"] == rg]
        reg[rg] = {
            "n": len(sub),
            "avg_ex": statistics.mean([x["ex"] for x in sub]) if sub else 0.0,
            "worst_dd": min([x["dd"] for x in sub], default=0.0),
            "avg_ret": statistics.mean([x["ret"] for x in sub]) if sub else 0.0,
        }

    return {
        "name": name,
        "params": {
            "w": p.get("w"),
            "top_n": p.get("top_n"),
            "ps": p.get("ps"),
            "bm": p.get("bm"),
            "soft_trend": p.get("soft_trend"),
            "prefer_bench_if_stronger": p.get("prefer_bench_if_stronger"),
            "park_bench": p.get("park_bench"),
            "stop": p.get("stop"),
            "dual_ma": p.get("dual_ma"),
            "hyst": p.get("hyst"),
            "min_hold": p.get("min_hold"),
            "rb": p.get("rb"),
        },
        "r": r,
        "years": years,
        "first_ann": statistics.mean([x["ann"] for x in first]) if first else 0.0,
        "second_ann": statistics.mean([x["ann"] for x in second]) if second else 0.0,
        "second_dd": min([x["dd"] for x in second], default=0.0),
        "reg": reg,
    }


def checklist(m: dict, thr: dict) -> dict:
    r = m["r"]
    reg = m["reg"]
    sh = r.get("sharpe", 0)
    cal = r.get("calmar", 0)
    dd = abs(r["dd"])
    expv = r.get("expectancy", 0)
    oos_ann = m["second_ann"]
    first_ann = m["first_ann"]
    oos_dd = abs(m["second_dd"])
    oos_ok = True
    if m["years"]:
        oos_ok = (oos_ann > first_ann - 15) and (oos_dd < max(thr["max_dd"] + 0.15, 0.55))
    if first_ann > 1e-6:
        fae = max(0.0, min(1.5, oos_ann / first_ann))
    else:
        fae = 1.0 if oos_ann >= 0 else 0.0
    bear_dd = abs(reg["bear"]["worst_dd"]) if reg["bear"]["n"] else 0
    multi_ok = bear_dd <= thr["max_dd"] + 0.08
    return {
        "夏普": (sh >= thr["sharpe"], f"{sh:.2f}"),
        "最大回撤": (dd <= thr["max_dd"], f"{dd*100:.1f}%"),
        "卡玛": (cal >= thr["calmar"], f"{cal:.2f}"),
        "期望值": (expv > 0, f"{expv*100:.2f}%"),
        "样本外": (oos_ok, f"后半{oos_ann:.1f}/前半{first_ann:.1f}, dd{oos_dd*100:.1f}%"),
        "前向效率": (fae >= thr["fae"], f"{fae*100:.0f}%"),
        "多状态": (multi_ok, f"熊市dd{bear_dd*100:.1f}%"),
        "含成本": (True, f"n={r['n']}"),
        "资金容量": (True, "研究级"),
        "_fae": fae,
    }


def risk_score(m: dict) -> float:
    r = m["r"]
    dd = abs(r["dd"])
    return (
        -dd * 220
        + r.get("sharpe", 0) * 45
        + r.get("calmar", 0) * 30
        + r.get("expectancy", 0) * 120
        + r["ann"] * 0.25
        - max(0, dd - 0.25) * 350
        - max(0, dd - 0.40) * 600
    )


def serialize_checks(checks: dict) -> dict:
    out = {}
    for k, v in checks.items():
        if k.startswith("_"):
            continue
        ok, msg = v
        out[k] = {"pass": bool(ok), "msg": msg}
    return out


def merge_batches(out_dir: Path, thr: dict, top: int):
    files = sorted(out_dir.glob("batch_*.json"))
    if not files:
        print("无 batch 结果可合并")
        return
    rows = []
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        rows.extend(obj.get("rows", []))
    if not rows:
        print("batch 为空")
        return
    # recompute pass with current thr
    for m in rows:
        # reconstruct minimal structure for checklist
        mm = {
            "r": m["r"],
            "reg": m["reg"],
            "years": m.get("years", []),
            "first_ann": m.get("first_ann", 0),
            "second_ann": m.get("second_ann", 0),
            "second_dd": m.get("second_dd", 0),
        }
        checks = checklist(mm, thr)
        m["checks"] = serialize_checks(checks)
        m["pass_n"] = sum(1 for k, v in m["checks"].items() if v["pass"])
        m["fae"] = checks.get("_fae", 0)
        m["risk_score"] = risk_score(mm)
        m["hard_pass"] = m["checks"]["最大回撤"]["pass"] and m["checks"]["期望值"]["pass"]

    rows.sort(key=lambda x: (x["pass_n"], x["hard_pass"], x["risk_score"]), reverse=True)
    summary = {
        "n": len(rows),
        "thr": thr,
        "top": rows[: max(top, 50)],
        "best_strict": None,
        "best_hard": None,
        "best_any": rows[0] if rows else None,
    }
    strict = [
        m
        for m in rows
        if abs(m["r"]["dd"]) <= thr["max_dd"]
        and m["r"].get("sharpe", 0) >= thr["sharpe"]
        and m["r"].get("calmar", 0) >= thr["calmar"]
        and m["r"].get("expectancy", 0) > 0
    ]
    hard = [m for m in rows if m.get("hard_pass")]
    if strict:
        summary["best_strict"] = strict[0]
    if hard:
        summary["best_hard"] = hard[0]

    out_path = out_dir / "merged_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 90)
    print(f"合并完成 n={len(rows)} -> {out_path}")
    print("=" * 90)
    for i, m in enumerate(rows[:top], 1):
        r = m["r"]
        print(
            f"#{i:02d} {m['name'][:54]:54s} pass={m['pass_n']}/9 "
            f"sh={r.get('sharpe',0):.2f} cal={r.get('calmar',0):.2f} "
            f"dd={r['dd']*100:5.1f}% ann={r['ann']:+6.1f}% exp={r.get('expectancy',0)*100:+.2f}%"
        )
        fails = [k for k, v in m["checks"].items() if not v["pass"]]
        print("     未过:", ", ".join(fails) if fails else "无")

    def show(tag, m):
        if not m:
            print(f"{tag}: NONE")
            return
        r = m["r"]
        print(
            f"{tag}: {m['name']} | dd={r['dd']*100:.1f}% sh={r.get('sharpe',0):.2f} "
            f"cal={r.get('calmar',0):.2f} ann={r['ann']:+.1f}% pass={m['pass_n']}/9"
        )
        fails = [k for k, v in m["checks"].items() if not v["pass"]]
        print("  未过:", ", ".join(fails) if fails else "清单全过")

    print("-" * 90)
    show("严格阈值最优", summary["best_strict"])
    show("硬约束(回撤+期望)最优", summary["best_hard"])
    show("风险排序第一", summary["best_any"])

    # 基线对照
    print("-" * 90)
    print("基线对照")
    for nm in ["c01", "c01_aw_v3", "c01_aw_v4", "c01_aw_v4_1"]:
        hit = next((x for x in rows if x["name"] == nm), None)
        if not hit:
            continue
        r = hit["r"]
        print(
            f"  {nm:12s} pass={hit['pass_n']}/9 dd={r['dd']*100:5.1f}% "
            f"sh={r.get('sharpe',0):.2f} cal={r.get('calmar',0):.2f} "
            f"ann={r['ann']:+6.1f}% exp={r.get('expectancy',0)*100:+.2f}%"
        )


def main():
    ap = argparse.ArgumentParser(description="完整风险网格验收(可分批)")
    ap.add_argument("--pool", default="pool_long_proxy")
    ap.add_argument("--count", type=int, default=3200)
    ap.add_argument("--adjust", default="none")
    ap.add_argument("--comm", type=float, default=0.0003)
    ap.add_argument("--sharpe", type=float, default=0.8)
    ap.add_argument("--max-dd", type=float, default=0.40)
    ap.add_argument("--calmar", type=float, default=1.0)
    ap.add_argument("--fae", type=float, default=0.5)
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--batches", type=int, default=8)
    ap.add_argument("--all", action="store_true", help="一次跑完全部")
    ap.add_argument("--merge", action="store_true", help="合并 batch 结果")
    ap.add_argument("--dry-count", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    thr = {
        "sharpe": args.sharpe,
        "max_dd": args.max_dd,
        "calmar": args.calmar,
        "fae": args.fae,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cands = build_candidates()
    if args.dry_count:
        print(f"候选总数: {len(cands)}")
        print(f"建议分批: --batches 8/12/16")
        return

    if args.merge:
        merge_batches(OUT_DIR, thr, args.top)
        return

    if args.all:
        batch_ids = list(range(args.batches))
    else:
        if args.batch < 0 or args.batch >= args.batches:
            raise SystemExit(f"--batch 需在 0..{args.batches-1}")
        batch_ids = [args.batch]

    data, bench, sd = load_data(args.pool, args.count, args.adjust)
    print(
        f"数据 n={len(data)} {sd[0]}~{sd[-1]} ({len(sd)}天) | 候选 {len(cands)} | "
        f"cache_files={data_mod.cache_info()['files']}"
    )

    for batch in batch_ids:
        part = [c for i, c in enumerate(cands) if i % args.batches == batch]
        print("=" * 90)
        print(f"BATCH {batch}/{args.batches} size={len(part)}")
        print("=" * 90)
        rows = []
        t0 = time.time()
        for i, (name, p) in enumerate(part, 1):
            m = eval_one(name, p, data, sd, bench, args.comm)
            if not m:
                continue
            checks = checklist(m, thr)
            rec = {
                "name": name,
                "params": m["params"],
                "r": m["r"],
                "years": m["years"],
                "first_ann": m["first_ann"],
                "second_ann": m["second_ann"],
                "second_dd": m["second_dd"],
                "reg": m["reg"],
                "checks": serialize_checks(checks),
                "pass_n": sum(1 for k, v in checks.items() if not k.startswith("_") and v[0]),
                "fae": checks.get("_fae", 0),
                "risk_score": risk_score(m),
                "hard_pass": checks["最大回撤"][0] and checks["期望值"][0],
            }
            rows.append(rec)
            if i % 10 == 0 or i == len(part):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(part) - i) / rate if rate > 0 else 0
                print(
                    f"  [{batch}] {i}/{len(part)} "
                    f"elapsed={elapsed/60:.1f}m eta={eta/60:.1f}m"
                )

        rows.sort(key=lambda x: (x["pass_n"], x["hard_pass"], x["risk_score"]), reverse=True)
        out = {
            "batch": batch,
            "batches": args.batches,
            "pool": args.pool,
            "count": args.count,
            "thr": thr,
            "n": len(rows),
            "rows": rows,
        }
        path = OUT_DIR / f"batch_{batch:02d}.json"
        path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        print(f"写入 {path} n={len(rows)}")
        # 预览本批 top5
        for i, m in enumerate(rows[:5], 1):
            r = m["r"]
            print(
                f"  batch-top{i} {m['name'][:48]:48s} pass={m['pass_n']}/9 "
                f"dd={r['dd']*100:5.1f}% sh={r.get('sharpe',0):.2f} ann={r['ann']:+.1f}%"
            )

    if args.all:
        merge_batches(OUT_DIR, thr, args.top)
    else:
        print("本批完成. 全部批次跑完后执行: python3 scripts/audit_risk.py --merge")


if __name__ == "__main__":
    main()
