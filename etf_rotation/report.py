"""信号报告文本与 JSON 输出."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class Report:
    def __init__(self):
        self.lines: list[str] = []

    def tee(self, s: str = ""):
        self.lines.append(s)
        print(s)

    def text(self) -> str:
        return "\n".join(self.lines)

    def save(self, path: Path | str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.text(), encoding="utf-8")


def render_signal_report(
    *,
    now: datetime,
    cfg: dict,
    market_ok: bool,
    bench_px: float | None,
    ma20: float | None,
    bench_chg: float | None,
    breadth: float,
    n_valid: int,
    n_rejected: int,
    rejected: list,
    ranked: list[tuple[str, dict]],
    shadow_ranked: list[tuple[str, dict]],
    shadow_action: str,
    shadow_market_ok: bool,
    shadow_name: str,
    decision: dict,
    state: dict,
    executed: tuple | None,
    initial_capital: float,
) -> Report:
    r = Report()
    t = r.tee
    stop = cfg.get("stop", -0.08)
    rb = cfg.get("rb_days", 15)
    hyst = cfg.get("hyst", 0.2)
    mh = cfg.get("min_hold", 5)
    w = cfg.get("weights", {})

    t("=" * 64)
    t(f"  ETF跨行业轮动 · {cfg.get('name')}  {now.strftime('%Y-%m-%d %H:%M')}")
    t(f"  因子: {w} | 调仓{rb}日 | 止损{stop*100:.0f}%")
    t(f"  迟滞{hyst*100:.0f}% | 最小持仓{mh}日 | 冻结:{cfg.get('frozen', False)}")
    t("=" * 64)

    if bench_px is not None and ma20 is not None:
        dist = (bench_px / ma20 - 1) * 100
        arrow = "✅ 向上(可开仓)" if market_ok else "❌ 向下(空仓)"
        chg_s = f"{bench_chg:+.1f}%" if bench_chg is not None else "N/A"
        t(f"\n📊 沪深300: {bench_px:.3f}  MA20:{ma20:.3f}({dist:+.1f}%)  20日:{chg_s}  {arrow}")
    else:
        t("\n⚠️ 无沪深300数据")

    t(f"📈 市场宽度: {breadth*100:.0f}%  有效池:{n_valid}  剔除异常:{n_rejected}")
    if rejected[:5]:
        show = ", ".join(f"{n}({rs})" for n, _, rs in rejected[:5])
        more = f" 等{n_rejected}只" if n_rejected > 5 else ""
        t(f"   异常样本: {show}{more}")

    t(f"\n  【主策略】效率排名 TOP10")
    t(f"  {'#':>3s} {'名称':14s} {'现价':>7s} {'20日':>7s} {'效率':>6s} {'MTF':>4s} {'得分':>6s} 状态")
    t(f"  {'-'*62}")
    for rk, (c, d) in enumerate(ranked[:10], 1):
        if d.get("overheat"):
            st = "🔥过热"
        elif not market_ok:
            st = "空仓期"
        elif d.get("score", 0) <= 0:
            st = "得分≤0"
        else:
            st = "可选"
        mark = "★" if rk == 1 and d.get("eligible") and market_ok else " "
        t(
            f"  {rk:>2d}{mark} {d['name']:14s} {d['close']:>7.3f} {d['mom20']*100:>+6.1f}% "
            f"{d['eff']:>6.3f} {d['mtf']:>4.2f} {d['score']:>+6.2f} {st}"
        )

    t(f"\n  【影子】{shadow_name} TOP5 (不交易)")
    t(f"  {'#':>3s} {'名称':14s} {'20日':>7s} {'5日':>6s} {'影子分':>6s}")
    t(f"  {'-'*42}")
    for rk, (c, d) in enumerate(shadow_ranked[:5], 1):
        t(f"  {rk:>3d} {d['name']:14s} {d['mom20']*100:>+6.1f}% {d['mom5']*100:>+5.1f}% {d.get('shadow', 0):>+6.2f}")

    # 动作摘要
    holding = state.get("holding")
    if executed:
        if executed[0] == "买入":
            action_summary = f"🟢 买入 {executed[1]}"
        else:
            action_summary = f"🔴 卖出 {executed[1]} ({executed[3]}) 盈亏{executed[2]:+.1f}%"
    elif holding:
        action_summary = f"🟡 持有 {holding['name']} (无需操作)"
    else:
        action_summary = "⚪ 空仓观望"

    t(f"\n{'='*64}")
    t(f"  ▸ 今日动作: {action_summary}")
    t(f"{'='*64}")
    for reason in decision.get("reasons", []):
        t(f"    · {reason}")

    t("\n  决策检查清单")
    for name, ok, detail in decision.get("checks", []):
        mark = "✓" if ok else "✗"
        t(f"    [{mark}] {name:10s}  {detail}")

    last_rb = state.get("last_rebalance")
    days_rb = decision.get("days_since_rb", 0)
    days_to = decision.get("days_to_rb", 0)
    can_rb = decision.get("can_rebalance", False)
    t(
        f"\n  调仓时钟: 上次 {last_rb or '无'} | 已过 {days_rb if last_rb else 0} 交易日 | "
        f"还需 {days_to} 交易日 | {'可调仓' if can_rb else '锁定中'}"
    )

    t(f"\n{'='*64}")
    t(f"  模拟账户 · {cfg.get('name')} · 起步{initial_capital/10000:.0f}万")
    t(f"{'='*64}")
    cash = state.get("cash", 0)
    tv = state.get("total_value", 0)
    hv = tv - cash
    if holding:
        # 持仓明细由调用方保证 close 已更新; 这里用 state 数值
        t(f"  持仓: {holding['name']}({holding['code']})  {holding['shares']}股")
        t(f"  成本:{holding['buy_price']:.3f}  止损:{holding['buy_price']*(1+stop):.3f}")
        t(f"  持仓天数: {decision.get('days_since_entry', '?')}")
    else:
        t("  持仓: 空仓")
    t(f"  现金: {cash:>10,.2f}   持仓市值: {hv:>10,.2f}")
    t(f"  总资产: {tv:>10,.2f}   总收益: {state.get('return_pct', 0):+.2f}%   累计盈亏: {state.get('total_pnl', 0):+,.2f}")
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    ntr = state.get("total_trades", 0)
    wr = wins / (wins + losses) * 100 if (wins + losses) else 0
    t(f"  交易: {ntr}笔 (胜{wins}/负{losses})  胜率:{wr:.0f}%")

    recent = state.get("trades", [])[-3:]
    if recent:
        t("\n  最近交易:")
        for trd in recent:
            pnl_str = f" 盈亏:{trd.get('pnl_pct', 0):+.2f}%" if trd["action"] == "SELL" else ""
            t(f"    {trd['date']} {trd['action']:4s} {trd['name']:12s} {trd['price']:>7.3f}×{trd['shares']:<5d}{pnl_str} {trd.get('reason', '')}")

    t(f"\n  对照 · {shadow_name}")
    t(f"    影子趋势: {'✅' if shadow_market_ok else '❌'}  建议: {shadow_action}")
    c01_name = holding["name"] if holding else "空仓"
    t(f"    主策略现态: {c01_name}")

    t(f"\n{'='*64}")
    t("  实盘备忘 (非投资建议)")
    if executed and executed[0] == "买入":
        t(f"    1. 买入 {executed[1]}  参考价附近")
        t(f"    2. 止损设在成本×{1+stop:.2f}")
        t(f"    3. 下次评估不早于 {rb} 个交易日后")
    elif executed and executed[0] == "卖出":
        t(f"    1. 卖出 {executed[1]}  原因:{executed[3]}")
        if holding:
            t(f"    2. 已换入 {holding['name']}")
        else:
            t("    2. 当前空仓, 等趋势恢复或下一调仓窗")
    elif holding:
        t(f"    1. 继续持有 {holding['name']}, 无需操作")
        t(f"    2. 盯止损 {holding['buy_price']*(1+stop):.3f}")
        t(f"    3. 调仓窗口还需 {days_to} 交易日")
    else:
        t("    1. 空仓, 不抄底")
        t("    2. 等沪深300回到MA20上方后再看信号")
    t("    · 固定配置至少跑满3个月再调参")
    t(f"{'='*64}")

    r.action_summary = action_summary  # type: ignore
    return r


def write_latest_json(path: Path | str, payload: dict):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
