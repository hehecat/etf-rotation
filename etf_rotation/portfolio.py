"""模拟账户状态与交易执行."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .calendar_util import trading_days_since


def default_state(initial_capital: float = 100000, config_name: str = "C01") -> dict:
    return {
        "cash": initial_capital,
        "holding": None,
        "trades": [],
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl": 0.0,
        "total_value": initial_capital,
        "return_pct": 0.0,
        "last_rebalance": None,
        "config": config_name,
        "last_update": None,
    }


def load_state(path: Path | str, initial_capital: float = 100000, config_name: str = "C01") -> dict:
    p = Path(path)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default_state(initial_capital, config_name)


def save_state(path: Path | str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def days_since(date_str: str | None, now: datetime) -> int:
    """自然日 (兼容旧调用); 调仓/持仓请用 trading_days_since."""
    if not date_str:
        return 999
    try:
        return (now - datetime.strptime(date_str, "%Y-%m-%d")).days
    except Exception:
        return 999


def _bench_realized_vol(bench_bars: dict | None, lookback: int = 20) -> float | None:
    """基准近 lookback 日收益标准差 (日波动)."""
    if not bench_bars:
        return None
    closes = bench_bars.get("close") or []
    if len(closes) < lookback + 1:
        return None
    rets = []
    for i in range(-lookback, 0):
        a, b = closes[i - 1], closes[i]
        if a and a > 0:
            rets.append(b / a - 1.0)
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return var ** 0.5


def realized_vol_from_rets(rets: list[float] | None, lookback: int = 20) -> float | None:
    """组合日收益序列 → 日波动 (样本标准差)."""
    if not rets:
        return None
    window = rets[-lookback:] if len(rets) >= lookback else list(rets)
    if len(window) < 5:
        return None
    mean = sum(window) / len(window)
    var = sum((x - mean) ** 2 for x in window) / (len(window) - 1)
    return var ** 0.5


def update_nav_history(
    state: dict,
    *,
    date_str: str,
    nav: float,
    max_len: int = 260,
) -> dict:
    """追加净值点并维护 port_rets (相邻交易日收益). 原地更新并返回 state.

    若 hist 尾部日期 > date_str (曾用 wall-clock 写了超前点), 先截断再写,
    避免 live 样本被虚假交易日撑开.
    """
    hist = list(state.get("nav_history") or [])
    date_str = str(date_str)[:10]
    # 截断超前日期点, 并重建 port_rets
    if hist and str(hist[-1].get("date") or "") > date_str:
        while hist and str(hist[-1].get("date") or "") > date_str:
            hist.pop()
        rets: list[float] = []
        for i in range(1, len(hist)):
            prev = float(hist[i - 1].get("nav") or 0)
            cur = float(hist[i].get("nav") or 0)
            if prev > 0:
                rets.append(cur / prev - 1.0)
        state["nav_history"] = hist[-max_len:]
        state["port_rets"] = rets[-max_len:]
        hist = list(state.get("nav_history") or [])
    # 同日覆盖
    if hist and hist[-1].get("date") == date_str:
        prev_nav = hist[-2]["nav"] if len(hist) >= 2 else None
        hist[-1] = {"date": date_str, "nav": float(nav)}
        rets = list(state.get("port_rets") or [])
        if prev_nav and prev_nav > 0 and rets:
            rets[-1] = float(nav) / float(prev_nav) - 1.0
            state["port_rets"] = rets[-max_len:]
        state["nav_history"] = hist[-max_len:]
        return state
    rets = list(state.get("port_rets") or [])
    if hist:
        prev = hist[-1].get("nav")
        if prev and prev > 0:
            rets.append(float(nav) / float(prev) - 1.0)
    hist.append({"date": date_str, "nav": float(nav)})
    state["nav_history"] = hist[-max_len:]
    state["port_rets"] = rets[-max_len:]
    return state


def apply_live_metrics(state: dict, *, date_str: str | None = None) -> dict:
    """拆分暖机净值 vs 信号日更 live 段收益 (原地写 state['live']).

    锚点优先级:
    1. state.live_anchor_nav (显式)
    2. warmup 末净值 (nav_history 在 warmup.d1)
    3. 无暖机 → 首个净值点 / cash
    """
    initial = 100000.0
    nav = float(state.get("total_value") or state.get("cash") or initial)
    warm = state.get("warmup") if isinstance(state.get("warmup"), dict) else {}
    hist = list(state.get("nav_history") or [])

    anchor = state.get("live_anchor_nav")
    start = state.get("live_start_date")

    if anchor is None and warm:
        d1 = warm.get("d1")
        if d1 and hist:
            for row in reversed(hist):
                if row.get("date") == d1:
                    anchor = float(row.get("nav") or 0) or None
                    break
        if anchor is None:
            anchor = float(state.get("cash") or nav)
        if not start:
            start = d1
        state["live_anchor_nav"] = float(anchor)
        if start:
            state["live_start_date"] = start

    if anchor is None:
        if hist:
            anchor = float(hist[0].get("nav") or state.get("cash") or initial)
            if not start:
                start = hist[0].get("date")
                state["live_start_date"] = start
        else:
            anchor = float(state.get("cash") or initial)
        state["live_anchor_nav"] = float(anchor)

    anchor = float(anchor or initial)
    live_ret = (nav / anchor - 1.0) * 100.0 if anchor > 0 else 0.0

    # live 段日收益: start 之后 (start 当日为锚, 不计入 live 日收益)
    live_rets: list[float] = []
    live_n_nav = 0
    if hist:
        prev = None
        for row in hist:
            d = row.get("date")
            v = float(row.get("nav") or 0)
            if start and d is not None and d < start:
                prev = v
                continue
            if start and d == start:
                prev = v
                live_n_nav += 1
                continue
            live_n_nav += 1
            if prev and prev > 0:
                live_rets.append(v / prev - 1.0)
            prev = v

    live_sharpe = None
    if len(live_rets) >= 5:
        import statistics

        mu = statistics.mean(live_rets)
        sig = statistics.stdev(live_rets)
        if sig > 0:
            live_sharpe = round((mu / sig) * (250 ** 0.5), 3)

    trades = list(state.get("trades") or [])
    live_trades = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        td = t.get("date")
        if start and td and td < start:
            continue
        if start and td == start and t.get("reason") == "warmed":
            continue
        live_trades.append(t)

    holds = state.get("holdings") or ([state["holding"]] if state.get("holding") else [])
    holds = [h for h in holds if h]

    state["live"] = {
        "anchor_nav": round(anchor, 2),
        "start_date": start,
        "nav": round(nav, 2),
        "return_pct": round(live_ret, 3),
        "n_nav": live_n_nav,
        "n_rets": len(live_rets),
        "sharpe": live_sharpe,
        "n_trades": len(live_trades),
        "n_holdings": len(holds),
        "holdings": ",".join(h.get("name", h.get("code", "?")) for h in holds) or "空仓",
        "asof": date_str or state.get("last_update"),
        "from_warmup": bool(warm),
    }
    return state




def classify_regime(market_ok: bool, bench_bars: dict | None) -> tuple[str, dict[str, Any]]:
    """与回测引擎同语义的状态分类: bull/chop/riskoff/bear."""
    info: dict[str, Any] = {"market_ok": market_ok}
    if not bench_bars:
        rg = "bear" if not market_ok else "chop"
        info["reason"] = "无基准K线"
        return rg, info
    closes = bench_bars.get("close") or []
    n = len(closes)
    ma20 = sum(closes[-20:]) / 20 if n >= 20 else (sum(closes) / n if n else 0.0)
    ma60 = sum(closes[-60:]) / 60 if n >= 60 else ma20
    ma_up = ma20 > ma60 if n >= 60 else market_ok
    mv = _bench_realized_vol(bench_bars, 20) or 0.015
    info.update({"ma20": ma20, "ma60": ma60, "ma_up": ma_up, "vol20": mv})
    if not market_ok and not ma_up:
        rg = "bear"
    elif not market_ok:
        rg = "riskoff"
    elif market_ok and ma_up and mv < 0.02:
        rg = "bull"
    else:
        rg = "chop"
    return rg, info


def compute_target_exposure(
    cfg: dict,
    *,
    market_ok: bool,
    bench_bars: dict | None = None,
    port_rets: list[float] | None = None,
) -> dict[str, Any]:
    """信号侧风险预算: position_pct × inv_vol × regime × vol_target.

    vol_target 优先用组合已实现波动 (port_rets); 不足时回退基准代理.
    """
    ps = float(cfg.get("position_pct", 0.95))
    trg = ps
    parts: dict[str, Any] = {"position_pct": ps}
    notes: list[str] = []
    lb = int(cfg.get("vol_lookback", 20))

    # inv_vol: 优先组合波动, 否则基准
    inv_vol = bool(cfg.get("inv_vol", False))
    port_mv = realized_vol_from_rets(port_rets, lb)
    bench_mv = _bench_realized_vol(bench_bars, 20)
    mv = port_mv if port_mv is not None else bench_mv
    if inv_vol:
        if mv and mv > 0:
            scale = max(0.3, min(1.0, 0.015 / mv))
        else:
            scale = 0.9
        trg *= scale
        parts["inv_vol_scale"] = round(scale, 4)
        parts["inv_vol_src"] = "portfolio" if port_mv is not None else "bench"
        parts["vol20"] = None if mv is None else round(mv, 6)
    else:
        parts["inv_vol_scale"] = 1.0

    # regime_map
    rmap = cfg.get("regime_map")
    if rmap:
        rg, rg_info = classify_regime(market_ok, bench_bars)
        mult = float(dict(rmap).get(rg, 1.0))
        mult = max(0.0, min(1.0, mult))
        trg *= mult
        parts["regime"] = rg
        parts["regime_mult"] = mult
        parts["regime_info"] = {
            k: (round(v, 6) if isinstance(v, float) else v) for k, v in rg_info.items()
        }
    else:
        parts["regime"] = None
        parts["regime_mult"] = 1.0

    # vol_target
    vt = cfg.get("vol_target")
    vt = float(vt) if vt not in (None, 0, 0.0, False) else 0.0
    if vt > 0:
        wmin = float(cfg.get("vol_wmin", 0.15))
        wmax = float(cfg.get("vol_wmax", 1.0))
        # 优先组合已实现
        use_mv = port_mv if port_mv is not None else bench_mv
        src = "portfolio" if port_mv is not None else "bench"
        if use_mv and use_mv > 1e-8:
            ann = use_mv * (252 ** 0.5)
            vw = vt / ann
            vw = max(wmin, min(wmax, vw))
            trg *= vw
            parts["vol_target"] = vt
            parts["vol_ann"] = round(ann, 4)
            parts["vol_scale"] = round(vw, 4)
            parts["vol_src"] = src
            if src == "bench":
                notes.append("vol_target 用基准波动代理 (组合收益样本不足)")
            else:
                notes.append("vol_target 用组合已实现波动")
        else:
            parts["vol_target"] = vt
            parts["vol_scale"] = 1.0
            parts["vol_src"] = "none"
            notes.append("vol_target 缺波动数据, 未缩放")
    else:
        parts["vol_target"] = 0.0
        parts["vol_scale"] = 1.0

    soft_trend = bool(cfg.get("soft_trend", False))
    if not market_ok and not soft_trend:
        trg = 0.0
        notes.append("趋势关 → 目标暴露 0")
    elif not market_ok and soft_trend:
        notes.append("soft_trend 趋势关, 保留停靠预算")

    trg = max(0.0, min(1.0, float(trg)))
    return {
        "target_exposure": round(trg, 4),
        "parts": parts,
        "notes": notes,
    }


def _as_holdings(holding: dict | None, holdings: list[dict] | None) -> list[dict]:
    if holdings:
        return [h for h in holdings if h]
    if holding:
        return [holding]
    return []


def _eligible_picks(
    ranked: list[tuple[str, dict]], top_n: int
) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for c, d in ranked:
        if d.get("eligible") and float(d.get("score") or 0) > 0:
            out.append((c, d))
        if len(out) >= top_n:
            break
    return out


def decide(
    *,
    holding: dict | None,
    etf_data: dict[str, dict],
    ranked: list[tuple[str, dict]],
    market_ok: bool,
    now: datetime,
    last_rebalance: str | None,
    cfg: dict,
    calendar: list[str] | None = None,
    bench_bars: dict | None = None,
    port_rets: list[float] | None = None,
    holdings: list[dict] | None = None,
) -> dict[str, Any]:
    """决策: 返回 action/target/reasons/checks/can_rebalance 等.

    top_n=1: 单持仓路径 (生产默认).
    top_n>1: 多持仓目标集合 + 等权暴露拆分 (研究/影子).
    """
    top_n = int(cfg.get("top_n", 1) or 1)
    holds = _as_holdings(holding, holdings)
    holding = holds[0] if holds else None
    if top_n > 1:
        return _decide_multi(
            holds=holds,
            etf_data=etf_data,
            ranked=ranked,
            market_ok=market_ok,
            now=now,
            last_rebalance=last_rebalance,
            cfg=cfg,
            calendar=calendar,
            bench_bars=bench_bars,
            port_rets=port_rets,
            top_n=top_n,
        )

    rb_days = cfg.get("rb_days", 15)
    min_hold = cfg.get("min_hold", 5)
    stop = cfg.get("stop", -0.08)
    hyst = cfg.get("hyst", 0.2)

    days_entry = trading_days_since(
        holding.get("buy_date") if holding else None, now, calendar
    )
    days_rb = trading_days_since(last_rebalance, now, calendar)
    can_rb = days_rb >= rb_days or last_rebalance is None
    days_to_rb = 0 if can_rb else max(0, rb_days - days_rb)

    checks = []
    checks.append(("大盘>MA20", market_ok, "趋势过滤"))
    checks.append(
        ("调仓窗口", can_rb, f"距上次{days_rb}日 / 需≥{rb_days}日" if last_rebalance else "从未调仓→可调")
    )
    if holding:
        checks.append(("最小持仓", days_entry >= min_hold, f"已持{days_entry}/{min_hold}日"))
        cp0 = etf_data.get(holding["code"], {}).get("close", holding["buy_price"])
        pnl0 = (cp0 - holding["buy_price"]) / holding["buy_price"]
        checks.append(("未触止损", pnl0 > stop, f"浮盈亏{pnl0*100:+.1f}% / 线{stop*100:.0f}%"))
    else:
        checks.append(("空仓可买", True, "当前无持仓"))

    pick = next(((c, d) for c, d in ranked if d.get("eligible")), None)
    if pick:
        checks.append(("有合格标的", True, f"{pick[1]['name']} 得分{pick[1]['score']:+.2f}"))
    else:
        checks.append(("有合格标的", False, "全部过热或得分≤0"))

    action = "HOLD"
    target = None
    price = 0.0
    name = ""
    reasons: list[str] = []

    if holding:
        cp = etf_data.get(holding["code"], {}).get("close", holding["buy_price"])
        pnl = (cp - holding["buy_price"]) / holding["buy_price"]
        if pnl <= stop:
            action = "SELL"
            reasons.append(f"止损触发 浮亏{pnl*100:.1f}%≤{stop*100:.0f}%")

    if action == "HOLD" and not market_ok:
        if holding:
            action = "SELL"
            reasons.append("沪深300跌破MA20 → 清仓")
        else:
            reasons.append("大盘趋势向下 → 保持空仓")

    # 空仓再入不强制等调仓窗 (窗只约束「持仓轮动」); 否则止损/趋势清仓后纯现金踏空
    empty_free_entry = bool(cfg.get("empty_free_entry", True))
    park_bench = bool(cfg.get("park_bench", False))
    prefer_bench = bool(cfg.get("prefer_bench_if_stronger", False))
    bench_code = cfg.get("bench") or "SH510300"
    is_park_hold = bool(holding and holding.get("park"))

    def _bench_quote():
        b = etf_data.get(bench_code)
        if b and b.get("close", 0) > 0:
            return b
        return None

    def _sector_weaker_than_bench(sec: dict) -> bool:
        b = _bench_quote()
        if not b or not prefer_bench:
            return False
        return float(sec.get("mom20", 0)) < float(b.get("mom20", 0))

    if action == "HOLD" and market_ok:
        if pick:
            target, pd = pick[0], pick[1]
            price = pd["close"]
            name = pd["name"]
            # 行业弱于300 → 改停靠300
            use_bench = _sector_weaker_than_bench(pd)
            if use_bench:
                b = _bench_quote()
                if b:
                    target, price = bench_code, b["close"]
                    name = b.get("name") or "沪深300ETF"
            if holding is None:
                if empty_free_entry or can_rb:
                    action = "BUY"
                    if use_bench:
                        reasons.append(f"行业弱于300 → 停靠 {name}")
                    else:
                        reasons.append(f"空仓+趋势开 → 买入 {name}")
                else:
                    reasons.append(f"调仓窗口未到(还需{days_to_rb}日) → 空仓等待")
            elif is_park_hold:
                if use_bench or target == bench_code:
                    reasons.append(f"继续底仓 {holding['name']} (行业未强过300)")
                elif days_entry < min_hold and not cfg.get("park_ignore_min_hold", True):
                    reasons.append(f"底仓未满最小持仓({days_entry}/{min_hold}) → 暂留")
                else:
                    action = "SELL"
                    reasons.append(f"底仓切换行业: {holding['name']} → {name}")
            elif holding["code"] != target:
                if not can_rb:
                    reasons.append(f"有更优标{name}, 但调仓窗口未到(还需{days_to_rb}日)")
                elif days_entry < min_hold:
                    reasons.append(f"有更优标{name}, 但最小持仓未满({days_entry}/{min_hold})")
                else:
                    cur_sc = etf_data.get(holding["code"], {}).get("score", -999)
                    new_sc = pd["score"]
                    thr = cur_sc * (1 + hyst) if cur_sc > 0 else cur_sc + hyst
                    if new_sc > thr:
                        action = "SELL"
                        reasons.append(
                            f"轮动: {holding['name']}({cur_sc:.2f}) → {name}({new_sc:.2f}) 阈值{thr:.2f}"
                        )
                    else:
                        reasons.append(
                            f"迟滞未满足: 持仓{cur_sc:.2f} 新标{new_sc:.2f} 需>{thr:.2f} → 继续持有"
                        )
            else:
                reasons.append(f"持仓即最优 {holding['name']} → 继续持有")
        else:
            # 无行业合格标
            if park_bench:
                b = etf_data.get(bench_code)
                if holding is None and b and b.get("close", 0) > 0:
                    if empty_free_entry or can_rb:
                        action = "BUY"
                        target = bench_code
                        price = b["close"]
                        name = b.get("name") or "沪深300ETF"
                        reasons.append(f"无行业标+趋势开 → 停靠底仓 {name}")
                    else:
                        reasons.append("无行业标且调仓窗未到 → 等待")
                elif is_park_hold and holding:
                    reasons.append(f"无行业标 → 继续底仓 {holding['name']}")
                elif holding and holding["code"] != bench_code and b and b.get("close", 0) > 0:
                    # 行业持仓失效(过热/得分塌) 且开启停靠: 不强制立即换300, 避免过交易
                    reasons.append("无合格行业标 → 继续持有当前(待窗/止损)")
                else:
                    reasons.append("无合格标的 → 观望")
            else:
                reasons.append("无合格标的 → 观望")

    if not reasons:
        reasons.append("无操作")

    exposure = compute_target_exposure(
        cfg, market_ok=market_ok, bench_bars=bench_bars, port_rets=port_rets
    )
    # 检查清单附带目标暴露
    checks.append(
        (
            "目标暴露",
            exposure["target_exposure"] > 0 or not market_ok,
            f"{exposure['target_exposure']*100:.1f}%"
            + (
                f" regime={exposure['parts'].get('regime')}"
                if exposure["parts"].get("regime")
                else ""
            ),
        )
    )

    return {
        "action": action,
        "target": target,
        "price": price,
        "name": name,
        "reasons": reasons,
        "checks": checks,
        "can_rebalance": can_rb,
        "days_to_rb": days_to_rb,
        "days_since_entry": days_entry,
        "days_since_rb": days_rb,
        "pick": pick,
        "park": bool(target and target == bench_code and park_bench),
        "target_exposure": exposure["target_exposure"],
        "exposure": exposure,
    }


def _decide_multi(
    *,
    holds: list[dict],
    etf_data: dict[str, dict],
    ranked: list[tuple[str, dict]],
    market_ok: bool,
    now: datetime,
    last_rebalance: str | None,
    cfg: dict,
    calendar: list[str] | None,
    bench_bars: dict | None,
    port_rets: list[float] | None,
    top_n: int,
) -> dict[str, Any]:
    """多持仓决策: 目标集合 = top_n 合格标, 等权拆分 target_exposure."""
    rb_days = int(cfg.get("rb_days", 15))
    min_hold = int(cfg.get("min_hold", 5))
    stop = float(cfg.get("stop", -0.08))
    empty_free_entry = bool(cfg.get("empty_free_entry", True))
    park_bench = bool(cfg.get("park_bench", False))
    prefer_bench = bool(cfg.get("prefer_bench_if_stronger", False))
    bench_code = cfg.get("bench") or "SH510300"

    days_rb = trading_days_since(last_rebalance, now, calendar)
    can_rb = days_rb >= rb_days or last_rebalance is None
    days_to_rb = 0 if can_rb else max(0, rb_days - days_rb)

    checks: list[tuple] = []
    checks.append(("大盘>MA20", market_ok, "趋势过滤"))
    checks.append(
        (
            "调仓窗口",
            can_rb,
            f"距上次{days_rb}日 / 需≥{rb_days}日" if last_rebalance else "从未调仓→可调",
        )
    )
    checks.append(("多持仓", True, f"top_n={top_n} 当前{len(holds)}只"))

    # 止损扫描
    stop_codes: list[str] = []
    for h in holds:
        cp = etf_data.get(h["code"], {}).get("close", h["buy_price"])
        pnl = (cp - h["buy_price"]) / h["buy_price"]
        if pnl <= stop:
            stop_codes.append(h["code"])
    if holds:
        checks.append(
            (
                "未触止损",
                not stop_codes,
                f"止损{len(stop_codes)}只" if stop_codes else "全部未触线",
            )
        )

    picks = _eligible_picks(ranked, top_n)
    if picks:
        names = ",".join(d["name"] for _, d in picks)
        checks.append(("有合格标的", True, f"{len(picks)}只: {names}"))
    else:
        checks.append(("有合格标的", False, "全部过热或得分≤0"))

    reasons: list[str] = []
    sell_codes: list[str] = []
    buy_codes: list[str] = []
    target_codes: list[str] = []
    action = "HOLD"

    # 趋势关 → 清仓
    if not market_ok:
        if holds:
            action = "SELL"
            sell_codes = [h["code"] for h in holds]
            reasons.append("沪深300跌破MA20 → 多持仓清仓")
        else:
            reasons.append("大盘趋势向下 → 保持空仓")
    else:
        # 止损优先
        if stop_codes:
            sell_codes.extend(stop_codes)
            action = "REBALANCE"
            reasons.append(f"止损卖出: {','.join(stop_codes)}")

        # 目标集合
        if picks:
            target_codes = [c for c, _ in picks]
            # prefer_bench: 若第一名弱于300则整篮停靠300
            if prefer_bench and target_codes:
                top_c, top_d = picks[0]
                b = etf_data.get(bench_code)
                if b and float(top_d.get("mom20", 0)) < float(b.get("mom20", 0)):
                    target_codes = [bench_code]
                    reasons.append("行业弱于300 → 整篮停靠基准")
        elif park_bench and etf_data.get(bench_code):
            target_codes = [bench_code]
            reasons.append("无行业标 → 停靠基准")

        held_set = {h["code"] for h in holds}
        tgt_set = set(target_codes)

        # 卖: 不在目标且 (止损已列 or 可调仓且满 min_hold)
        for h in holds:
            c = h["code"]
            if c in tgt_set:
                continue
            if c in stop_codes:
                continue
            hd = trading_days_since(h.get("buy_date"), now, calendar)
            if can_rb and hd >= min_hold:
                sell_codes.append(c)
            elif can_rb and hd < min_hold:
                reasons.append(f"{h.get('name', c)} 未满最小持仓({hd}/{min_hold}) 暂留")
            elif not can_rb:
                reasons.append(f"{h.get('name', c)} 非目标但调仓窗未到")

        # 买: 目标中未持有
        can_buy = can_rb or (empty_free_entry and not holds)
        if target_codes and can_buy:
            for c in target_codes:
                if c not in held_set:
                    buy_codes.append(c)
        elif target_codes and not can_buy and not holds:
            reasons.append(f"调仓窗口未到(还需{days_to_rb}日) → 空仓等待")

        sell_codes = list(dict.fromkeys(sell_codes))
        buy_codes = list(dict.fromkeys(buy_codes))

        if sell_codes or buy_codes:
            action = "REBALANCE" if (holds or buy_codes) else "HOLD"
            if sell_codes and buy_codes:
                reasons.append(
                    f"多持仓再平衡 卖{len(sell_codes)}买{len(buy_codes)} 目标{target_codes}"
                )
            elif sell_codes:
                reasons.append(f"多持仓减仓: {sell_codes}")
            elif buy_codes:
                reasons.append(f"多持仓建仓: {buy_codes}")
        elif holds:
            reasons.append(
                "多持仓维持: " + ",".join(h.get("name", h["code"]) for h in holds)
            )
        elif not target_codes:
            reasons.append("无合格标的 → 观望")

    if not reasons:
        reasons.append("无操作")

    exposure = compute_target_exposure(
        cfg, market_ok=market_ok, bench_bars=bench_bars, port_rets=port_rets
    )
    te = float(exposure["target_exposure"])
    # 等权目标
    n_tgt = max(1, len(target_codes)) if target_codes else 0
    per_w = (te / n_tgt) if n_tgt else 0.0
    targets = []
    for c in target_codes:
        d = etf_data.get(c) or {}
        targets.append(
            {
                "code": c,
                "name": d.get("name") or c,
                "price": float(d.get("close") or 0),
                "weight": round(per_w, 4),
                "score": float(d.get("score") or 0),
            }
        )

    checks.append(
        (
            "目标暴露",
            te > 0 or not market_ok,
            f"{te*100:.1f}% × {n_tgt}只等权" if n_tgt else f"{te*100:.1f}%",
        )
    )

    primary = targets[0] if targets else None
    return {
        "action": action,
        "multi": True,
        "top_n": top_n,
        "target": primary["code"] if primary else None,
        "price": primary["price"] if primary else 0.0,
        "name": primary["name"] if primary else "",
        "targets": targets,
        "sell_codes": sell_codes,
        "buy_codes": buy_codes,
        "reasons": reasons,
        "checks": checks,
        "can_rebalance": can_rb,
        "days_to_rb": days_to_rb,
        "days_since_entry": (
            trading_days_since(holds[0].get("buy_date"), now, calendar) if holds else 0
        ),
        "days_since_rb": days_rb,
        "pick": picks[0] if picks else None,
        "park": bool(primary and primary["code"] == bench_code and park_bench),
        "target_exposure": te,
        "exposure": exposure,
        "holdings_now": holds,
    }


def execute(
    state: dict,
    decision: dict,
    etf_data: dict[str, dict],
    market_ok: bool,
    now: datetime,
    cfg: dict,
    commission: float = 0.00005,
    initial_capital: float = 100000,
) -> tuple[dict, tuple | None]:
    """执行模拟交易, 返回 (new_state, executed_trade|None).

    multi 决策走 holdings 列表; 单持仓保持 holding 字段兼容.
    """
    if decision.get("multi"):
        return _execute_multi(
            state, decision, etf_data, market_ok, now, cfg, commission, initial_capital
        )

    state = deepcopy(state)
    holding = state.get("holding")
    cash = float(state.get("cash", initial_capital))
    trades = list(state.get("trades", []))
    total_pnl = float(state.get("total_pnl", 0))
    last_rb = state.get("last_rebalance")
    stop = cfg.get("stop", -0.08)
    # 优先用 decide 算出的风险预算; 缺省回退 position_pct
    if decision.get("target_exposure") is not None:
        pos_pct = float(decision["target_exposure"])
    else:
        pos_pct = float(cfg.get("position_pct", 0.95))
    rb_days = cfg.get("rb_days", 15)

    action = decision["action"]
    target = decision["target"]
    price = decision["price"]
    name = decision["name"]
    can_rb = decision["can_rebalance"]
    days_to_rb = decision["days_to_rb"]
    executed = None

    if action == "SELL" and holding:
        old_code = holding["code"]
        old_price = holding["buy_price"]
        old_shares = holding["shares"]
        cp = etf_data.get(old_code, {}).get("close", old_price)
        proceeds = old_shares * cp
        comm = max(proceeds * commission, 0)
        cash += proceeds - comm
        pnl = (cp - old_price) / old_price * 100
        total_pnl += proceeds - comm - old_shares * old_price
        if pnl <= stop * 100:
            reason = "止损"
        elif not market_ok:
            reason = "趋势空仓"
        else:
            reason = "轮动换仓"
        trades.append({
            "date": now.strftime("%Y-%m-%d"),
            "action": "SELL",
            "code": old_code,
            "name": holding["name"],
            "price": round(cp, 4),
            "shares": old_shares,
            "pnl_pct": round(pnl, 2),
            "reason": reason,
        })
        if pnl > 0:
            state["wins"] = state.get("wins", 0) + 1
        else:
            state["losses"] = state.get("losses", 0) + 1
        state["total_trades"] = state.get("total_trades", 0) + 1
        executed = ("卖出", holding["name"], pnl, reason)
        holding = None
        if reason in ("止损", "趋势空仓") and not bool(cfg.get("empty_free_entry", True)):
            last_rb = now.strftime("%Y-%m-%d")
            can_rb = False
            days_to_rb = rb_days

    if action in ("SELL", "BUY") and target and market_ok and holding is None:
        allow_buy = action == "BUY" or (
            action == "SELL" and executed and trades and trades[-1].get("reason") == "轮动换仓"
        )
        if allow_buy or can_rb:
            buy_shares = int(cash * pos_pct / price / 100) * 100
            if buy_shares > 0:
                cost = buy_shares * price
                comm = max(cost * commission, 0)
                cash -= cost + comm
                is_park = bool(decision.get("park")) or (
                    target == (cfg.get("bench") or "SH510300")
                    and (
                        bool(cfg.get("park_bench"))
                        or bool(cfg.get("prefer_bench_if_stronger"))
                    )
                )
                holding = {
                    "code": target,
                    "name": name,
                    "buy_price": price,
                    "shares": buy_shares,
                    "buy_date": now.strftime("%Y-%m-%d"),
                    "park": is_park,
                }
                buy_reason = "底仓停靠" if is_park else "信号买入"
                trades.append({
                    "date": now.strftime("%Y-%m-%d"),
                    "action": "BUY",
                    "code": target,
                    "name": name,
                    "price": round(price, 4),
                    "shares": buy_shares,
                    "reason": buy_reason,
                })
                executed = ("买入", name, None, buy_reason)
                if not is_park:
                    last_rb = now.strftime("%Y-%m-%d")
                    days_to_rb = rb_days
                    can_rb = False

    hv = 0.0
    if holding:
        cp = etf_data.get(holding["code"], {}).get("close", holding["buy_price"])
        hv = holding["shares"] * cp
    total = cash + hv

    state["cash"] = round(cash, 2)
    state["holding"] = holding
    # 同步 holdings 单元素列表, 便于统一读
    state["holdings"] = [holding] if holding else []
    state["total_value"] = round(total, 2)
    state["total_pnl"] = round(total_pnl, 2)
    state["return_pct"] = round((total - initial_capital) / initial_capital * 100, 2)
    state["last_update"] = now.strftime("%Y-%m-%d %H:%M")
    state["trades"] = trades[-50:]
    state["config"] = cfg.get("name", state.get("config"))
    state["last_rebalance"] = last_rb
    state["days_to_rebalance"] = days_to_rb
    state["market_ok"] = market_ok

    return state, executed


def _execute_multi(
    state: dict,
    decision: dict,
    etf_data: dict[str, dict],
    market_ok: bool,
    now: datetime,
    cfg: dict,
    commission: float,
    initial_capital: float,
) -> tuple[dict, tuple | None]:
    """多持仓执行: 先卖 sell_codes, 再按 targets 等权补齐."""
    state = deepcopy(state)
    holds = _as_holdings(state.get("holding"), state.get("holdings"))
    by_code = {h["code"]: dict(h) for h in holds}
    cash = float(state.get("cash", initial_capital))
    trades = list(state.get("trades", []))
    total_pnl = float(state.get("total_pnl", 0))
    last_rb = state.get("last_rebalance")
    stop = float(cfg.get("stop", -0.08))
    days_to_rb = decision.get("days_to_rb", 0)
    executed = None
    date_s = now.strftime("%Y-%m-%d")

    sell_codes = list(decision.get("sell_codes") or [])
    # 趋势清仓: sell 全部
    if decision.get("action") == "SELL" and not sell_codes:
        sell_codes = list(by_code.keys())

    for code in sell_codes:
        h = by_code.pop(code, None)
        if not h:
            continue
        cp = etf_data.get(code, {}).get("close", h["buy_price"])
        proceeds = h["shares"] * cp
        comm = max(proceeds * commission, 0)
        cash += proceeds - comm
        pnl = (cp - h["buy_price"]) / h["buy_price"] * 100
        total_pnl += proceeds - comm - h["shares"] * h["buy_price"]
        if pnl <= stop * 100:
            reason = "止损"
        elif not market_ok:
            reason = "趋势空仓"
        else:
            reason = "多持仓换出"
        trades.append({
            "date": date_s,
            "action": "SELL",
            "code": code,
            "name": h.get("name", code),
            "price": round(cp, 4),
            "shares": h["shares"],
            "pnl_pct": round(pnl, 2),
            "reason": reason,
        })
        if pnl > 0:
            state["wins"] = state.get("wins", 0) + 1
        else:
            state["losses"] = state.get("losses", 0) + 1
        state["total_trades"] = state.get("total_trades", 0) + 1
        executed = ("卖出", h.get("name", code), pnl, reason)

    targets = list(decision.get("targets") or [])
    te = float(decision.get("target_exposure") or 0)
    # 买入: 对目标中尚未持有的, 用当前总资产 × weight 下单
    if targets and market_ok and te > 0:
        # 先估总资产
        hv0 = 0.0
        for h in by_code.values():
            cp = etf_data.get(h["code"], {}).get("close", h["buy_price"])
            hv0 += h["shares"] * cp
        nav = cash + hv0
        did_buy = False
        for t in targets:
            code = t["code"]
            if code in by_code:
                continue
            price = float(t.get("price") or etf_data.get(code, {}).get("close") or 0)
            if price <= 0:
                continue
            w = float(t.get("weight") or 0)
            budget = nav * w
            buy_shares = int(budget / price / 100) * 100
            if buy_shares <= 0:
                continue
            cost = buy_shares * price
            if cost + max(cost * commission, 0) > cash:
                buy_shares = int(cash / price / 100) * 100
                if buy_shares <= 0:
                    continue
                cost = buy_shares * price
            comm = max(cost * commission, 0)
            cash -= cost + comm
            name = t.get("name") or code
            is_park = code == (cfg.get("bench") or "SH510300")
            by_code[code] = {
                "code": code,
                "name": name,
                "buy_price": price,
                "shares": buy_shares,
                "buy_date": date_s,
                "park": is_park,
            }
            trades.append({
                "date": date_s,
                "action": "BUY",
                "code": code,
                "name": name,
                "price": round(price, 4),
                "shares": buy_shares,
                "reason": "多持仓买入",
            })
            executed = ("买入", name, None, "多持仓买入")
            did_buy = True
        if did_buy or sell_codes:
            last_rb = date_s
            days_to_rb = int(cfg.get("rb_days", 15))

    holds = list(by_code.values())
    hv = 0.0
    for h in holds:
        cp = etf_data.get(h["code"], {}).get("close", h["buy_price"])
        hv += h["shares"] * cp
    total = cash + hv

    state["cash"] = round(cash, 2)
    state["holdings"] = holds
    state["holding"] = holds[0] if holds else None  # 兼容旧报告
    state["total_value"] = round(total, 2)
    state["total_pnl"] = round(total_pnl, 2)
    state["return_pct"] = round((total - initial_capital) / initial_capital * 100, 2)
    state["last_update"] = now.strftime("%Y-%m-%d %H:%M")
    state["trades"] = trades[-50:]
    state["config"] = cfg.get("name", state.get("config"))
    state["last_rebalance"] = last_rb
    state["days_to_rebalance"] = days_to_rb
    state["market_ok"] = market_ok
    return state, executed
