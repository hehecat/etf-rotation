"""生产信号主流程."""
from __future__ import annotations
import json
import sys
from pathlib import Path


from datetime import datetime
from typing import Any

from . import config as cfgmod
from . import data as data_mod
from . import factors
from . import portfolio
from . import report
from .calendar_util import merge_calendar
from .paths import (
    DATA_DIR,
    LATEST_JSON,
    LATEST_TXT,
    STATE_FILE,
    ensure_dirs,
    shadow_state_file,
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


def _mark_nav(state: dict, etf_data: dict, initial: float) -> float:
    """现金 + 全部持仓盯市净值."""
    cash = float(state.get("cash", initial))
    holds = state.get("holdings") or ([state["holding"]] if state.get("holding") else [])
    hv = 0.0
    for h in holds:
        if not h:
            continue
        code = h.get("code")
        if code and code in etf_data:
            hv += float(h.get("shares") or 0) * float(etf_data[code]["close"])
        elif h.get("buy_price") is not None:
            hv += float(h.get("shares") or 0) * float(h.get("buy_price") or 0)
    nav = cash + hv
    state["total_value"] = round(nav, 2)
    state["return_pct"] = round((nav - initial) / initial * 100, 2)
    return nav


def _process_research_shadow(
    *,
    shadow_name: str,
    shadow: dict,
    strat: dict,
    market: dict,
    etf_list: list,
    name_map: dict,
    bench: str,
    calendar: list[str] | None,
    now: datetime,
    asof_day: str,
    default_commission: float,
    shadow_execute: bool,
    etf_data_fallback: dict,
    market_slice_fn,
) -> dict[str, Any]:
    """单研究影子: decide → 可选 execute → 盯市净值 → 可选写盘.

    永不写生产 STATE_FILE.
    """
    sw = shadow.get("weights") or strat.get("weights") or {"eff": 0.6, "mtf": 0.4}
    shadow_market = market_slice_fn(shadow)
    sh_data, _sh_rej = factors.build_etf_table(
        shadow_market,
        name_map,
        sw,
        overheat=float(shadow.get("overheat", strat.get("overheat", 0.3))),
        max_1d_abs=float(shadow.get("max_1d_abs", strat.get("max_1d_abs", 0.12))),
        max_20d_abs=float(shadow.get("max_20d_abs", strat.get("max_20d_abs", 0.45))),
        signed_eff=bool(shadow.get("signed_eff", False)),
        require_abs_mom=bool(
            shadow.get("require_abs_mom", False) or shadow.get("abs_m", False)
        ),
    )
    if bench in market and bench not in sh_data:
        if bench in etf_data_fallback:
            sh_data[bench] = etf_data_fallback[bench]
        else:
            b = market[bench]
            sh_data[bench] = {
                "name": name_map.get(bench, "沪深300ETF"),
                "close": b["close"][-1] if b.get("close") else 0,
                "score": -999,
                "mom20": 0.0,
            }
    shadow_ranked = sorted(sh_data.items(), key=lambda x: x[1]["score"], reverse=True)
    if bench in market:
        sm_ok, _, _, _ = market_trend(
            market[bench], dual_ma=bool(shadow.get("dual_ma", False))
        )
    else:
        sm_ok = False

    sh_initial = float(shadow.get("initial_capital", 100000))
    sh_state_path = shadow_state_file(shadow_name)
    sh_state = portfolio.load_state(
        sh_state_path, sh_initial, shadow.get("name", shadow_name)
    )
    # 空仓且暖机净值远高于 cash → 对齐全现金 (保留曲线)
    if not sh_state.get("holding") and not (sh_state.get("holdings") or []):
        hist = sh_state.get("nav_history") or []
        if hist:
            try:
                last_nav = float(hist[-1].get("nav") or 0)
            except Exception:
                last_nav = 0.0
            cash0 = float(sh_state.get("cash") or sh_initial)
            if last_nav > cash0 * 1.01:
                sh_state["cash"] = last_nav

    shadow_decision = portfolio.decide(
        holding=sh_state.get("holding"),
        holdings=sh_state.get("holdings"),
        etf_data=sh_data if sh_data else etf_data_fallback,
        ranked=shadow_ranked,
        market_ok=sm_ok,
        now=now,
        last_rebalance=sh_state.get("last_rebalance"),
        cfg=shadow,
        calendar=calendar,
        bench_bars=market.get(bench),
        port_rets=list(sh_state.get("port_rets") or []),
    )
    te = float(shadow_decision.get("target_exposure") or 0)
    if shadow_decision.get("multi") and shadow_decision.get("targets"):
        names = "+".join(t["name"] for t in shadow_decision["targets"])
        shadow_action = f"{shadow_decision['action']} [{names}] @暴露{te*100:.0f}%"
    elif shadow_decision["action"] in ("BUY", "HOLD") and shadow_decision.get("target"):
        nm = shadow_decision.get("name") or shadow_decision["target"]
        shadow_action = f"{shadow_decision['action']} {nm} @暴露{te*100:.0f}%"
    elif shadow_decision["action"] == "SELL":
        shadow_action = f"SELL ({'; '.join(shadow_decision.get('reasons') or [])})"
    elif not sm_ok:
        shadow_action = f"空仓(趋势关) 暴露{te*100:.0f}%"
    else:
        shadow_action = (
            f"{shadow_decision['action']} 暴露{te*100:.0f}% · "
            + ("; ".join(shadow_decision.get("reasons") or ["观望"]))
        )

    sh_comm = float(shadow.get("commission", default_commission))
    sh_executed = None
    if shadow_execute:
        sh_state, sh_executed = portfolio.execute(
            sh_state,
            shadow_decision,
            sh_data if sh_data else etf_data_fallback,
            sm_ok,
            now,
            shadow,
            sh_comm,
            sh_initial,
        )

    price_src = sh_data if sh_data else etf_data_fallback
    sh_nav = _mark_nav(sh_state, price_src, sh_initial)
    dstr = asof_day
    portfolio.update_nav_history(sh_state, date_str=dstr, nav=sh_nav)
    # 确保 live 锚点存在 (暖机后首次日更)
    if sh_state.get("live_anchor_nav") is None and sh_state.get("warmup"):
        w = sh_state["warmup"]
        sh_state["live_anchor_nav"] = float(
            sh_state.get("cash") or sh_state.get("total_value") or sh_initial
        )
        if not sh_state.get("live_start_date"):
            sh_state["live_start_date"] = w.get("d1") or dstr
    portfolio.apply_live_metrics(sh_state, date_str=dstr)
    if shadow_execute:
        portfolio.save_state(sh_state_path, sh_state)

    if shadow_decision.get("multi") and shadow_decision.get("targets"):
        shadow_picks: list[tuple[str, dict]] = []
        for t in shadow_decision["targets"]:
            c = t["code"]
            if c in price_src:
                shadow_picks.append((c, price_src[c]))
            else:
                shadow_picks.append(
                    (
                        c,
                        {
                            "name": t.get("name", c),
                            "score": t.get("score", 0),
                            "shadow": t.get("score", 0),
                        },
                    )
                )
    elif shadow_decision.get("target") and shadow_decision["target"] in price_src:
        tc = shadow_decision["target"]
        shadow_picks = [(tc, price_src[tc])]
    else:
        shadow_picks = [
            (c, d)
            for c, d in shadow_ranked
            if d.get("eligible") and d.get("score", 0) > 0
        ][: int(shadow.get("top_n", 1))]

    holds = sh_state.get("holdings") or (
        [sh_state["holding"]] if sh_state.get("holding") else []
    )
    hold_names = ",".join(
        h.get("name", h.get("code", "?")) for h in holds if h
    ) or "空仓"

    return {
        "name": shadow_name,
        "config": shadow.get("name"),
        "research": True,
        "market_ok": sm_ok,
        "action": shadow_action,
        "decision": shadow_decision,
        "ranked": shadow_ranked,
        "picks": shadow_picks,
        "state_path": str(sh_state_path),
        "executed": sh_executed,
        "state": {
            "holding": sh_state.get("holding"),
            "holdings": holds,
            "holdings_str": hold_names,
            "n_holdings": len(holds),
            "cash": sh_state.get("cash"),
            "total_value": sh_state.get("total_value"),
            "return_pct": sh_state.get("return_pct"),
            "n_port_rets": len(sh_state.get("port_rets") or []),
            "live": sh_state.get("live"),
            "executed": None
            if not sh_executed
            else {
                "side": sh_executed[0],
                "name": sh_executed[1],
                "pnl": sh_executed[2],
                "reason": sh_executed[3] if len(sh_executed) > 3 else None,
            },
        },
    }



def run_signal(
    strategy_name: str = "c01",
    shadow_name: str = "c13_shadow",
    bar_count: int = 60,
    dry_run: bool = False,
    pool_name: str = "pool",
    shadow_execute: bool = True,
    extra_shadows: list[str] | None = None,
) -> dict[str, Any]:
    ensure_dirs()
    now = datetime.now()
    strat = cfgmod.load_strategy(strategy_name)
    shadow = cfgmod.load_strategy(shadow_name)
    pool = cfgmod.load_pool(pool_name)
    etf_list = cfgmod.pool_as_list(pool)
    name_map = cfgmod.pool_as_dict(pool)
    bench = strat.get("bench") or pool.get("bench") or "SH510300"
    initial = float(strat.get("initial_capital", 100000))
    commission = float(strat.get("commission", 0.00005))

    # 主线其它影子 (日更批量成交, 不含主 shadow_name)
    extra_names: list[str] = []
    for n in extra_shadows or []:
        n = (n or "").strip()
        if n and n != shadow_name and n not in extra_names:
            extra_names.append(n)

    print("📡 取数中...", end="", flush=True)
    codes = [c for c, _ in etf_list]
    # 生产/主影子 + 主线其它影子的 extra_universe、park_assets
    cfg_sources = [strat, shadow]
    for n in extra_names:
        try:
            cfg_sources.append(cfgmod.load_strategy(n))
        except Exception:
            pass
    for src in cfg_sources:
        for key in ("extra_universe", "park_assets"):
            for c in src.get(key) or []:
                if c:
                    codes.append(str(c))
    # 去重 (bench 单独强取)
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    market = data_mod.fetch_many(uniq, count=bar_count, min_bars=22)
    # 基准多源强取, 避免并行时漏掉导致错误开仓
    bench_bars = data_mod.fetch_bench(bench, count=bar_count, min_bars=22)
    if bench_bars:
        market[bench] = bench_bars
    print(f" 完成 (池{len(market)}只, 基准{'OK' if bench_bars else 'FAIL'})")

    # 无基准 → 一律空仓 (fail-closed), 禁止默认 market_ok=True
    market_ok = False
    bench_px = ma20 = bench_chg = None
    if bench in market:
        market_ok, bench_px, ma20, bench_chg = market_trend(
            market[bench], dual_ma=bool(strat.get("dual_ma", False))
        )
    else:
        print("⚠️ 无基准数据 → 强制空仓(不新开仓)")

    # 名称映射: 池内 + 常见跨资产
    name_hints = {
        "SZ159934": "黄金ETF",
        "SH518880": "黄金ETF华安",
        "SH513100": "纳指ETF",
        "SH513500": "标普500ETF",
        "SH511010": "国债ETF",
        "SH511880": "银华日利",
    }
    name_map = {**name_hints, **name_map}

    def _market_slice(cfg: dict) -> dict:
        """生产/影子各自打分宇宙: 池 + 该策略 extra_universe."""
        codes_set = {c for c, _ in etf_list}
        for c in cfg.get("extra_universe") or []:
            if c:
                codes_set.add(str(c))
        out = {c: market[c] for c in codes_set if c in market}
        if bench in market and bench not in out:
            out[bench] = market[bench]
        return out

    pool_market = _market_slice(strat)
    if bench in market and bench not in pool_market:
        pool_market[bench] = market[bench]
    weights = strat.get("weights") or {"eff": 0.6, "mtf": 0.4}
    signed_eff = bool(strat.get("signed_eff", False))
    require_abs_mom = bool(strat.get("require_abs_mom", False) or strat.get("abs_m", False))
    etf_data, rejected = factors.build_etf_table(
        pool_market,
        name_map,
        weights,
        overheat=float(strat.get("overheat", 0.3)),
        max_1d_abs=float(strat.get("max_1d_abs", 0.12)),
        max_20d_abs=float(strat.get("max_20d_abs", 0.45)),
        signed_eff=signed_eff,
        require_abs_mom=require_abs_mom,
    )
    if len(etf_data) < 5:
        print(f"❌ 有效ETF仅{len(etf_data)}只")
        return {"error": "insufficient_data", "n": len(etf_data)}

    ranked = sorted(etf_data.items(), key=lambda x: x[1]["score"], reverse=True)
    up_n = sum(1 for d in etf_data.values() if d["mom20"] > 0)
    breadth = up_n / len(etf_data)

    # 交易日日历: 基准优先, 否则池内并集 (与回测交易日对齐)
    cal_sources = []
    if bench in market and market[bench].get("dates"):
        cal_sources.append(market[bench]["dates"])
    for bars in pool_market.values():
        if bars.get("dates"):
            cal_sources.append(bars["dates"])
    calendar = merge_calendar(*cal_sources) if cal_sources else None
    # 行情截至日 (写 nav/live 用; 避免 wall-clock 超前行情造假日)
    market_asof = None
    if calendar:
        market_asof = str(calendar[-1])[:10]
    elif bench in market and (market[bench].get("dates") or []):
        market_asof = str(market[bench]["dates"][-1])[:10]
    asof_day = market_asof or now.strftime("%Y-%m-%d")
    if market_asof and market_asof < now.strftime("%Y-%m-%d"):
        print(f" 行情截至 {market_asof} (wall {now.strftime('%Y-%m-%d')}) → nav/live 用 asof")

    # —— 研究影子: 完整 decide + 可选 execute; 主线 extra 一并日更 ——
    # 兼容旧 c13 风格: 无 research/vol_target/regime 时仍输出因子对照
    research_shadow = bool(
        shadow.get("research")
        or shadow.get("vol_target")
        or shadow.get("regime_map")
        or shadow.get("inv_vol")
    )
    shadow_ranked: list[tuple[str, dict]]
    shadow_picks: list[tuple[str, dict]]
    shadow_decision: dict | None = None
    sm_ok = market_ok
    shadow_action = "无"
    mainline_rows: list[dict[str, Any]] = []

    if research_shadow:
        primary = _process_research_shadow(
            shadow_name=shadow_name,
            shadow=shadow,
            strat=strat,
            market=market,
            etf_list=etf_list,
            name_map=name_map,
            bench=bench,
            calendar=calendar,
            now=now,
            asof_day=asof_day,
            default_commission=commission,
            shadow_execute=shadow_execute,
            etf_data_fallback=etf_data,
            market_slice_fn=_market_slice,
        )
        shadow_decision = primary["decision"]
        shadow_ranked = primary["ranked"]
        shadow_picks = primary["picks"]
        sm_ok = primary["market_ok"]
        shadow_action = primary["action"]
        shadow_decision["_state_path"] = primary["state_path"]
        shadow_decision["_state_snapshot"] = primary["state"]
        mainline_rows.append(
            {
                "name": primary["name"],
                "action": primary["action"],
                "market_ok": primary["market_ok"],
                "state": primary["state"],
                "signal": True,
            }
        )
        # 其余主线影子: 共用 market, 独立 state 成交
        for n in extra_names:
            try:
                scfg = cfgmod.load_strategy(n)
            except Exception as e:
                mainline_rows.append(
                    {"name": n, "error": str(e), "signal": False}
                )
                continue
            is_research = bool(
                scfg.get("research")
                or scfg.get("vol_target")
                or scfg.get("regime_map")
                or scfg.get("inv_vol")
            )
            if not is_research:
                continue
            try:
                row = _process_research_shadow(
                    shadow_name=n,
                    shadow=scfg,
                    strat=strat,
                    market=market,
                    etf_list=etf_list,
                    name_map=name_map,
                    bench=bench,
                    calendar=calendar,
                    now=now,
                    asof_day=asof_day,
                    default_commission=commission,
                    shadow_execute=shadow_execute,
                    etf_data_fallback=etf_data,
                    market_slice_fn=_market_slice,
                )
                mainline_rows.append(
                    {
                        "name": row["name"],
                        "action": row["action"],
                        "market_ok": row["market_ok"],
                        "state": row["state"],
                        "signal": False,
                    }
                )
            except Exception as e:
                mainline_rows.append(
                    {"name": n, "error": str(e), "signal": False}
                )
    else:
        # 旧影子: 仅因子对照
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
            (c, d)
            for c, d in shadow_ranked
            if not d["overheat"] and d["shadow"] > 0
        ][:top_n]
        if bench in market:
            sm_ok, _, _, _ = market_trend(
                market[bench], dual_ma=bool(shadow.get("dual_ma", True))
            )
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
        holdings=state.get("holdings"),
        etf_data=etf_data,
        ranked=ranked,
        market_ok=market_ok,
        now=now,
        last_rebalance=state.get("last_rebalance"),
        cfg=strat,
        calendar=calendar,
        bench_bars=market.get(bench),
        port_rets=list(state.get("port_rets") or []),
    )
    executed: tuple | None = None
    sh_payload: dict[str, Any] = {
        "config": shadow.get("name"),
        "research": research_shadow,
        "market_ok": sm_ok,
        "picks": [
            {
                "code": c,
                "name": d["name"],
                "score": round(d.get("score", d.get("shadow", 0)), 3),
            }
            for c, d in shadow_picks
        ],
        "action": shadow_action,
        "mainline": [
            {
                "name": r.get("name"),
                "action": r.get("action"),
                "market_ok": r.get("market_ok"),
                "signal": bool(r.get("signal")),
                "error": r.get("error"),
                "holdings": (r.get("state") or {}).get("holdings_str"),
                "n_holdings": (r.get("state") or {}).get("n_holdings"),
                "total_value": (r.get("state") or {}).get("total_value"),
                "return_pct": (r.get("state") or {}).get("return_pct"),
                "live": (r.get("state") or {}).get("live"),
                "live_return_pct": ((r.get("state") or {}).get("live") or {}).get(
                    "return_pct"
                ),
                "executed": (r.get("state") or {}).get("executed"),
            }
            for r in mainline_rows
        ],
    }
    if shadow_decision is not None:
        sh_payload["decision"] = {
            "action": shadow_decision.get("action"),
            "target": shadow_decision.get("target"),
            "name": shadow_decision.get("name"),
            "target_exposure": shadow_decision.get("target_exposure"),
            "reasons": shadow_decision.get("reasons"),
            "exposure": shadow_decision.get("exposure"),
        }
        if shadow_decision.get("_state_path"):
            sh_payload["state_path"] = shadow_decision["_state_path"]
        if shadow_decision.get("_state_snapshot"):
            sh_payload["state"] = shadow_decision["_state_snapshot"]

    if dry_run:
        # 不改生产仓位, 但仍挂影子与决策摘要供报告/JSON
        new_state = dict(state)
        new_state["breadth"] = round(breadth, 3)
        new_state["shadow"] = sh_payload
        new_state["decision_reasons"] = decision["reasons"]
        holding0 = new_state.get("holding")
        if holding0:
            new_state["action_summary"] = f"🟡 持有 {holding0['name']} (dry-run)"
        else:
            new_state["action_summary"] = "⚪ 空仓观望 (dry-run)"
    else:
        new_state, executed = portfolio.execute(
            state, decision, etf_data, market_ok, now, strat, commission, initial
        )
        new_state["breadth"] = round(breadth, 3)
        new_state["shadow"] = sh_payload
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

    # 持仓浮盈 / 净值历史 (生产 + 报告用; 支持 multi holdings)
    holding = new_state.get("holding")
    nav_now = _mark_nav(new_state, etf_data, initial)
    portfolio.update_nav_history(
        new_state, date_str=asof_day, nav=nav_now
    )
    if not dry_run:
        portfolio.save_state(STATE_FILE, new_state)

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
        shadow_decision=shadow_decision,
        research_shadow=research_shadow,
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

    # 保留上一轮 live 回写的有效收益, 避免 signal 覆盖掉 signal_live
    prev_signal_live = None
    if LATEST_JSON.exists():
        try:
            prev = json.loads(Path(LATEST_JSON).read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("signal_live"), dict):
                prev_signal_live = prev.get("signal_live")
        except Exception:
            prev_signal_live = None

    payload = {
        "time": now.strftime("%Y-%m-%d %H:%M"),
        "market_asof": asof_day,
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
    if isinstance(prev_signal_live, dict):
        payload["signal_live"] = prev_signal_live
    report.write_latest_json(LATEST_JSON, payload)

    # signal 重写 latest.txt 会冲掉 live 块; 有 signal_live 则补回
    if isinstance(payload.get("signal_live"), dict):
        try:
            scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from shadow_live import patch_latest_txt  # type: ignore

            patch_latest_txt(payload["signal_live"])
        except Exception:
            pass

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
