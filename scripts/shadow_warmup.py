#!/usr/bin/env python3
"""用长代理/ETF 回测权益曲线暖机影子 state 的 port_rets / nav_history.

目的: 让信号侧 vol_target 能切到 vol_src=portfolio (无需等实盘日更攒样本).
只写 shadow_states/, 永不碰生产 模拟仓位.json.

用法:
  python3 scripts/shadow_warmup.py --shadows c01_q10_vt08_soft_oh38,c01_q10_vt11
  python3 scripts/shadow_warmup.py --pool pool_long_proxy --count 3200 --tail 120
  python3 scripts/shadow_warmup.py --reset --shadows c01_q10_vt08_soft_oh38
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod  # noqa: E402
from etf_rotation.backtest import bt  # noqa: E402
from etf_rotation.paths import STATE_FILE, ensure_dirs, shadow_state_file  # noqa: E402
from etf_rotation.portfolio import (  # noqa: E402
    apply_live_metrics,
    compute_target_exposure,
    default_state,
    load_state,
    save_state,
)
from etf_rotation.research_mainline import (  # noqa: E402
    MONITOR_SHADOWS,
    extra_codes_for_strategies,
)
from etf_rotation.search_data import load_pool_data  # noqa: E402

DEFAULT_SHADOWS = list(MONITOR_SHADOWS)


def warmup_one(
    name: str,
    data: dict,
    *,
    comm: float,
    tail: int,
    reset: bool,
    max_len: int = 260,
) -> dict:
    strat = cfgmod.load_strategy(name)
    p = cfgmod.strategy_for_backtest(strat)
    p["ps"] = p.get("ps", 0.95)
    r = bt(data, p, commission=comm)
    if not r or not r.get("equity"):
        return {"name": name, "ok": False, "error": "no equity"}

    eq = r["equity"]
    if tail > 0 and len(eq) > tail:
        eq = eq[-tail:]

    path = shadow_state_file(name)
    initial = float(strat.get("initial_capital", 100000))
    if reset or not path.exists():
        st = default_state(initial, strat.get("name", name))
    else:
        st = load_state(path, initial, strat.get("name", name))

    # 用 equity 重建 nav_history + port_rets (截断 tail)
    nav_hist = [{"date": row["d"], "nav": float(row["v"])} for row in eq]
    rets: list[float] = []
    for i in range(1, len(nav_hist)):
        v0 = nav_hist[i - 1]["nav"]
        v1 = nav_hist[i]["nav"]
        if v0 > 0:
            rets.append(v1 / v0 - 1.0)

    st["nav_history"] = nav_hist[-max_len:]
    st["port_rets"] = rets[-max_len:]
    last_nav = round(float(eq[-1]["v"]), 2)
    st["total_value"] = last_nav
    st["return_pct"] = round((float(eq[-1]["v"]) - initial) / initial * 100, 2)
    # 暖机清空持仓 → 视为全现金, cash 必须对齐净值, 否则信号日会用 10 万覆盖曲线
    st["cash"] = last_nav
    st["holding"] = None
    st["holdings"] = []
    st["config"] = strat.get("name", name)
    st["warmup"] = {
        "source": "backtest_equity",
        "d0": eq[0]["d"],
        "d1": eq[-1]["d"],
        "n_nav": len(nav_hist),
        "n_rets": len(rets),
        "comm": comm,
        "bt_sharpe": r.get("sharpe"),
        "bt_dd": r.get("dd"),
        "bt_ann": r.get("ann"),
    }
    # live 段锚点: 暖机末净值; 之后信号日更从此计 live_return
    st["live_anchor_nav"] = last_nav
    st["live_start_date"] = eq[-1]["d"]
    st["note"] = "warmed by shadow_warmup.py; holdings cleared for live signal re-entry"
    apply_live_metrics(st, date_str=eq[-1]["d"])
    save_state(path, st)

    # 探测暴露 src
    exp = compute_target_exposure(
        strat,
        market_ok=True,
        bench_bars=None,
        port_rets=list(st.get("port_rets") or []),
    )
    return {
        "name": name,
        "ok": True,
        "path": str(path),
        "n_rets": len(st["port_rets"]),
        "n_nav": len(st["nav_history"]),
        "d0": eq[0]["d"],
        "d1": eq[-1]["d"],
        "bt_sharpe": r.get("sharpe"),
        "bt_dd": r.get("dd"),
        "vol_src": (exp.get("parts") or {}).get("vol_src"),
        "target_exposure_bull": exp.get("target_exposure"),
        "prod_untouched": True,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="影子 port_rets 暖机")
    ap.add_argument("--shadows", default=",".join(DEFAULT_SHADOWS))
    ap.add_argument("--pool", default="pool_long_proxy")
    ap.add_argument("--count", type=int, default=3200)
    ap.add_argument("--adjust", default="none")
    ap.add_argument("--comm", type=float, default=0.0003)
    ap.add_argument("--tail", type=int, default=120, help="只保留最近 N 日权益 (0=全长)")
    ap.add_argument("--reset", action="store_true", help="忽略已有影子 state 重建")
    ap.add_argument("--out", default="output/risk_audit/shadow_warmup.json")
    args = ap.parse_args()

    ensure_dirs()
    prod_before = STATE_FILE.read_bytes() if STATE_FILE.exists() else b""

    names = [x.strip() for x in args.shadows.split(",") if x.strip()]
    print("=" * 72)
    print("影子暖机 · backtest equity → port_rets")
    print("=" * 72)
    print(f"pool={args.pool} tail={args.tail} comm={args.comm} reset={args.reset}")
    print(f"shadows={names}")

    extras = extra_codes_for_strategies(names)
    data, bench, sd = load_pool_data(
        args.pool, args.count, args.adjust, extra_codes=extras
    )
    print(f"data n={len(data)} {sd[0]}~{sd[-1]} bench={bench} extras={extras}")

    rows = []
    for name in names:
        try:
            row = warmup_one(
                name, data, comm=args.comm, tail=args.tail, reset=args.reset
            )
        except Exception as e:
            row = {"name": name, "ok": False, "error": str(e)}
        rows.append(row)
        if row.get("ok"):
            print(
                f"  OK {name:32s} rets={row['n_rets']:3d} "
                f"{row['d0']}~{row['d1']} vol_src={row.get('vol_src')} "
                f"exp@bull={row.get('target_exposure_bull')} "
                f"bt_sh={row.get('bt_sharpe'):.2f}"
            )
        else:
            print(f"  FAIL {name}: {row.get('error')}")

    prod_after = STATE_FILE.read_bytes() if STATE_FILE.exists() else b""
    untouched = prod_before == prod_after
    print(f"prod STATE untouched: {untouched}")
    if not untouched:
        raise SystemExit("生产 state 被改动, 中止")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pool": args.pool,
        "tail": args.tail,
        "comm": args.comm,
        "prod_untouched": untouched,
        "rows": rows,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE {out}")


if __name__ == "__main__":
    main()
