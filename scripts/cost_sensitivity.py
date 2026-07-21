#!/usr/bin/env python3
"""成本敏感性: 对研究主线扫 commission, 防「零成本假优质」.

用法:
  python3 scripts/cost_sensitivity.py
  python3 scripts/cost_sensitivity.py --strategies c01_q10_vt08_soft_oh38,c01_q10_vt11
  python3 scripts/cost_sensitivity.py --pool pool_long_proxy --count 3200
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.backtest import bt  # noqa: E402
from etf_rotation import config as cfgmod  # noqa: E402
from etf_rotation.search_data import load_pool_data  # noqa: E402

DEFAULT_STRATS = [
    "c01",
    "c01_q10",
    "c01_q10_vt11",
    "c01_q10_vt09_oh35",
    "c01_q10_vt08_soft_oh38",
    "c01_q10_vt08_soft_oh38_xgn",
    "c01_q10_vt09_soft_oh40",
]
# 含 0 / 研究默认 3bp / 偏高 10bp / 悲观 20bp
DEFAULT_COMMS = [0.0, 0.0003, 0.001, 0.002]


def main() -> None:
    ap = argparse.ArgumentParser(description="成本敏感性扫描")
    ap.add_argument("--strategies", default=",".join(DEFAULT_STRATS))
    ap.add_argument("--comms", default=",".join(str(x) for x in DEFAULT_COMMS))
    ap.add_argument("--pool", default="pool_long_proxy")
    ap.add_argument("--count", type=int, default=3200)
    ap.add_argument("--adjust", default="none")
    ap.add_argument("--out", default="output/risk_audit/cost_sensitivity.json")
    args = ap.parse_args()

    strats = [s.strip() for s in args.strategies.split(",") if s.strip()]
    comms = [float(x) for x in args.comms.split(",") if x.strip()]

    print("=" * 72)
    print("成本敏感性 · 长代理")
    print("=" * 72)
    try:
        from etf_rotation.research_mainline import extra_codes_for_strategies

        extras = extra_codes_for_strategies(strats)
    except Exception:
        extras = []
    data, bench, sd = load_pool_data(
        args.pool, args.count, args.adjust, extra_codes=extras
    )
    print(f"pool={args.pool} n={len(data)} {sd[0]}~{sd[-1]} bench={bench} extras={extras}")
    print(f"comms={comms}")
    print()

    report: dict = {"pool": args.pool, "count": args.count, "comms": comms, "strategies": {}}
    header = f"{'strategy':32s} " + " ".join(f"{'c'+str(int(c*10000))+'bp':>12s}" for c in comms)
    print(header)
    print("-" * len(header))

    for name in strats:
        try:
            p0 = cfgmod.strategy_for_backtest(cfgmod.load_strategy(name))
        except Exception as e:
            print(f"{name:32s} SKIP {e}")
            continue
        p0["ps"] = p0.get("ps", 0.95)
        row = {}
        cells = []
        base_sh = None
        for comm in comms:
            r = bt(data, p0, commission=comm)
            if not r:
                cells.append(f"{'n/a':>12s}")
                row[str(comm)] = None
                continue
            cell = {
                "ann": r["ann"],
                "dd": r["dd"],
                "sharpe": r["sharpe"],
                "calmar": r["calmar"],
                "n": r["n"],
            }
            row[str(comm)] = cell
            if base_sh is None:
                base_sh = r["sharpe"]
            cells.append(f"{r['sharpe']:5.2f}/{r['dd']*100:5.1f}%")
        # 衰减: 最高成本相对 3bp 的夏普比
        ref = row.get("0.0003") or row.get(str(comms[0]))
        hi = row.get(str(comms[-1]))
        decay = None
        if ref and hi and ref.get("sharpe"):
            decay = hi["sharpe"] / ref["sharpe"] if ref["sharpe"] else None
        report["strategies"][name] = {"by_comm": row, "sharpe_decay_hi_vs_3bp": decay}
        flag = ""
        if decay is not None and decay < 0.7:
            flag = " ⚠衰减"
        print(f"{name:32s} " + " ".join(f"{c:>12s}" for c in cells) + flag)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print()
    print(f"WROTE {out}")
    print("列格式: sharpe / maxDD%; 衰减=最高成本夏普 / 3bp夏普")


if __name__ == "__main__":
    main()
