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
) -> dict[str, Any]:
    """决策: 返回 action/target/reasons/checks/can_rebalance 等.

    rb_days / min_hold 按**交易日**计数 (与回测引擎一致).
    calendar: 可选交易日列表 (YYYY-MM-DD); 缺省用工作日近似.
    """
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
    executed_trade: (side, name, pnl|None, reason)
    """
    state = deepcopy(state)
    holding = state.get("holding")
    cash = float(state.get("cash", initial_capital))
    trades = list(state.get("trades", []))
    total_pnl = float(state.get("total_pnl", 0))
    last_rb = state.get("last_rebalance")
    stop = cfg.get("stop", -0.08)
    pos_pct = cfg.get("position_pct", 0.95)
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
        # 止损/趋势清仓: 不再锁死 empty 再入 (empty_free_entry 默认开)
        # 仅当显式关闭 empty_free_entry 时保留冷却锁
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
                # 行业信号买入才锁定调仓窗; 底仓停靠不占用轮动时钟
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
