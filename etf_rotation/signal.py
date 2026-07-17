"""生产信号主流程."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import config as cfgmod
from . import data as data_mod
from . import factors
from . import portfolio
from . import report
from .paths import (
    DATA_DIR,
    LATEST_JSON,
    LATEST_TXT,
    STATE_FILE,
    ensure_dirs,
)


def market_trend(bars: dict, dual_ma: bool = False) -> tuple[bool, float, float, float]:
    """返回 market_ok, px, ma20, chg20."""
    c = bars["close"]
    px = c[-1]
    ma20 = sum(c[-20:]) / 20
    ma10 = sum(c[-10:]) / 10 if len(c) >= 10 else ma20
    chg = (c[-1] / c[-21] - 1) * 100 if len(c) >= 21 else 0.0
    if dual_ma:
        ok = px > ma20 and px > ma10
    else:
        ok = px > ma20
    return ok, px, ma20, chg


def run_signal(
    strategy_name: str = "c01",
    shadow_name: str = "c13_shadow",
    bar_count: int = 60,
    dry_run: bool = False,
) -> dict[str, Any]:
    ensure_dirs()
    now = datetime.now()
    strat = cfgmod.load_strategy(strategy_name)
    shadow = cfgmod.load_strategy(shadow_name)
    pool = cfgmod.load_pool()
    etf_list = cfgmod.pool_as_list(pool)
    name_map = cfgmod.pool_as_dict(pool)
    bench = strat.get("bench") or pool.get("bench") or "SH510300"
    initial = float(strat.get("initial_capital", 100000))
    commission = float(strat.get("commission", 0.00005))

    print("📡 取数中...", end="", flush=True)
    codes = [bench] + [c for c, _ in etf_list]
    # 去重
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    market = data_mod.fetch_many(uniq, count=bar_count, min_bars=22)
    print(f" 完成 ({len(market)}只)")

    market_ok = True
    bench_px = ma20 = bench_chg = None
    if bench in market:
        market_ok, bench_px, ma20, bench_chg = market_trend(
            market[bench], dual_ma=bool(strat.get("dual_ma", False))
        )
    else:
        print("⚠️ 无基准数据")

    # 主策略池不含重复取 bench 时的 name
    pool_market = {c: market[c] for c, _ in etf_list if c in market}
    weights = strat.get("weights") or {"eff": 0.6, "mtf": 0.4}
    etf_data, rejected = factors.build_etf_table(
        pool_market,
        name_map,
        weights,
        overheat=float(strat.get("overheat", 0.3)),
        max_1d_abs=float(strat.get("max_1d_abs", 0.12)),
        max_20d_abs=float(strat.get("max_20d_abs", 0.45)),
    )
    if len(etf_data) < 5:
        print(f"❌ 有效ETF仅{len(etf_data)}只")
        return {"error": "insufficient_data", "n": len(etf_data)}

    ranked = sorted(etf_data.items(), key=lambda x: x[1]["score"], reverse=True)
    up_n = sum(1 for d in etf_data.values() if d["mom20"] > 0)
    breadth = up_n / len(etf_data)

    # 影子分
    sw = shadow.get("weights") or {"m20": 0.5, "m5": 0.3, "eff": 0.2}
    raw = {
        c: {"m20": d["mom20"], "m5": d["mom5"], "eff": d["eff"], "mtf": d["mtf"]}
        for c, d in etf_data.items()
    }
    shadow_scores, _ = factors.score_cross_section(raw, sw)
    for c, d in etf_data.items():
        d["shadow"] = shadow_scores.get(c, 0.0)
    shadow_ranked = sorted(etf_data.items(), key=lambda x: x[1]["shadow"], reverse=True)
    top_n = int(shadow.get("top_n", 2))
    shadow_picks = [
        (c, d) for c, d in shadow_ranked
        if not d["overheat"] and d["shadow"] > 0
    ][:top_n]

    if bench in market:
        sm_ok, _, _, _ = market_trend(market[bench], dual_ma=bool(shadow.get("dual_ma", True)))
    else:
        sm_ok = market_ok
    if not sm_ok:
        shadow_action = "空仓(双均线过滤)"
    elif shadow_picks:
        shadow_action = " + ".join(d["name"] for _, d in shadow_picks)
    else:
        shadow_action = "无合格标的"

    state = portfolio.load_state(STATE_FILE, initial, strat.get("name", "C01"))
    decision = portfolio.decide(
        holding=state.get("holding"),
        etf_data=etf_data,
        ranked=ranked,
        market_ok=market_ok,
        now=now,
        last_rebalance=state.get("last_rebalance"),
        cfg=strat,
    )

    executed: tuple | None = None
    if dry_run:
        # 不改仓位, 但仍渲染
        new_state = state
    else:
        new_state, executed = portfolio.execute(
            state, decision, etf_data, market_ok, now, strat, commission, initial
        )
        new_state["breadth"] = round(breadth, 3)
        new_state["shadow"] = {
            "config": shadow.get("name"),
            "market_ok": sm_ok,
            "picks": [
                {"code": c, "name": d["name"], "score": round(d["shadow"], 3)}
                for c, d in shadow_picks
            ],
            "action": shadow_action,
        }
        # 动作摘要
        holding = new_state.get("holding")
        if executed:
            if executed[0] == "买入":
                new_state["action_summary"] = f"🟢 买入 {executed[1]}"
            else:
                new_state["action_summary"] = (
                    f"🔴 卖出 {executed[1]} ({executed[3]}) 盈亏{executed[2]:+.1f}%"
                )
        elif holding:
            new_state["action_summary"] = f"🟡 持有 {holding['name']} (无需操作)"
        else:
            new_state["action_summary"] = "⚪ 空仓观望"
        new_state["decision_reasons"] = decision["reasons"]
        portfolio.save_state(STATE_FILE, new_state)

    # 持仓浮盈在报告里补全
    holding = new_state.get("holding")
    if holding and holding["code"] in etf_data:
        cp = etf_data[holding["code"]]["close"]
        # 更新市值
        hv = holding["shares"] * cp
        new_state["total_value"] = round(new_state["cash"] + hv, 2)
        new_state["return_pct"] = round(
            (new_state["total_value"] - initial) / initial * 100, 2
        )

    rep = report.render_signal_report(
        now=now,
        cfg=strat,
        market_ok=market_ok,
        bench_px=bench_px,
        ma20=ma20,
        bench_chg=bench_chg,
        breadth=breadth,
        n_valid=len(etf_data),
        n_rejected=len(rejected),
        rejected=rejected,
        ranked=ranked,
        shadow_ranked=shadow_ranked,
        shadow_action=shadow_action,
        shadow_market_ok=sm_ok,
        shadow_name=shadow.get("name", "shadow"),
        decision=decision,
        state=new_state,
        executed=executed,
        initial_capital=initial,
    )

    # 持仓细节补强 (现价/浮盈)
    if holding and holding["code"] in etf_data:
        d = etf_data[holding["code"]]
        hp = holding["buy_price"]
        cp = d["close"]
        pnl = (cp - hp) / hp * 100
        stop = float(strat.get("stop", -0.08))
        stop_px = hp * (1 + stop)
        room = (cp - stop_px) / cp * 100
        # 插入到报告末尾前不太方便; 控制台已够用, 在文本中追加一块
        extra = [
            "",
            f"  [持仓快照] 现价:{cp:.3f} 浮盈亏:{pnl:+.2f}% 止损:{stop_px:.3f}(距{room:.1f}%) 得分:{d['score']:+.2f}",
        ]
        for line in extra:
            rep.tee(line)

    stamp = now.strftime("%Y%m%d_%H%M")
    outfile = DATA_DIR / f"etf信号_{stamp}.txt"
    rep.save(outfile)
    rep.save(LATEST_TXT)

    payload = {
        "time": new_state.get("last_update") or now.strftime("%Y-%m-%d %H:%M"),
        "config": strat.get("name"),
        "frozen": strat.get("frozen", False),
        "action": getattr(rep, "action_summary", new_state.get("action_summary")),
        "reasons": decision["reasons"],
        "market_ok": market_ok,
        "breadth": breadth,
        "holding": new_state.get("holding"),
        "total_value": new_state.get("total_value"),
        "return_pct": new_state.get("return_pct"),
        "days_to_rebalance": decision.get("days_to_rb"),
        "top3": [
            {"code": c, "name": d["name"], "score": round(d["score"], 3)}
            for c, d in ranked[:3]
        ],
        "shadow": new_state.get("shadow"),
        "checks": [
            {"name": n, "ok": ok, "detail": d} for n, ok, d in decision.get("checks", [])
        ],
    }
    report.write_latest_json(LATEST_JSON, payload)

    print(f"\n✅ 已保存: {outfile}")
    print(f"📌 固定入口: {LATEST_TXT}")
    print(f"📌 JSON: {LATEST_JSON}")

    return {
        "outfile": str(outfile),
        "state": new_state,
        "decision": decision,
        "executed": executed,
        "payload": payload,
    }
