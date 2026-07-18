"""回测引擎.

成交模式 fill:
  - close:     T日收盘算信号, T日收盘成交 (偏乐观, 旧默认)
  - next_open: T日收盘算信号, T+1开盘成交 (更接近可执行)
"""
from __future__ import annotations

import statistics
from typing import Any

from .factors import factors_at_index, score_cross_section

INITIAL_CAPITAL = 100000


def bt(
    all_data: dict[str, dict],
    p: dict[str, Any],
    date_range: tuple[str, str] | None = None,
    commission: float = 0.00005,
) -> dict[str, Any] | None:
    """
    all_data: {code: {dates, close, volume, open?, name?}}
    p: strategy_for_backtest() 风格参数
       p['fill'] = 'close' | 'next_open'
    """
    rb = p.get("rb", 5)
    tn = p.get("top_n", 1)
    hyst = p.get("hyst", 0.20)
    mh = p.get("min_hold", 5)
    st = p.get("stop", -0.08)
    vh = p.get("vol_h", 0.020)
    vm = p.get("vol_m", 0.015)
    w = p.get("w", {"m20": 0.5, "m5": 0.3, "eff": 0.2})
    lb = p.get("lb", 20)
    am = p.get("abs_m", False)
    bm = p.get("bm", 0)
    dm = p.get("dual_ma", False)
    tr = p.get("trail", 0)
    iv = p.get("inv_vol", False)
    ps = p.get("ps", 0.90)
    bench = p.get("bench", "SH510300")
    overheat = p.get("overheat", 0.30)
    slip = p.get("slip", 0.0)
    cost = commission + slip
    # 默认 next_open: 更接近 15:35 信号 → 次日开盘可执行
    fill = p.get("fill", "next_open")  # close | next_open
    signed_eff = bool(p.get("signed_eff", False))
    # 空仓再入不锁调仓窗; 趋势开且无行业标时可停靠基准
    empty_free = bool(p.get("empty_free_entry", True))
    park_bench = bool(p.get("park_bench", False))
    # 行业20日弱于基准时改持基准 (减轻宽基牛市跑输)
    prefer_bench = bool(p.get("prefer_bench_if_stronger", False))

    if not all_data:
        return None
    sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in all_data]))
    if date_range:
        d0, d1 = date_range
        sd = [d for d in sd if d0 <= d <= d1]
    if len(sd) < 80:
        return None

    di: dict[str, dict[str, int]] = {}
    for c in all_data:
        for i, d in enumerate(all_data[c]["dates"]):
            di.setdefault(d, {})[c] = i

    def px(code: str, date: str, which: str) -> float | None:
        if date not in di or code not in di[date]:
            return None
        i = di[date][code]
        series = all_data[code]
        if which == "open":
            op = series.get("open")
            if op and i < len(op):
                return op[i]
            return series["close"][i]
        return series["close"][i]

    cash = INITIAL_CAPITAL
    pos: dict = {}
    trds: list = []
    daily: list = []
    cd: dict = {}
    lrb = -100
    dc = 0
    start = max(65, lb + 5)

    # next_open: 上一交易日收盘产生的待成交指令
    # {"side":"S"|"B", "c":code, "r":reason, "ed":entry_dc_for_buy}
    pending: list[dict] = []

    gap_pnls: list[float] = []  # 开盘相对昨收的冲击 (仅 next_open 有意义)

    for idx in range(start, len(sd)):
        date = sd[idx]
        dc += 1

        # ---- 1) 先执行隔夜挂单 (T+1 开盘) ----
        if fill == "next_open" and pending:
            still_pending = []
            # 先卖后买
            sells = [o for o in pending if o["side"] == "S"]
            buys = [o for o in pending if o["side"] == "B"]
            for o in sells:
                c = o["c"]
                if c not in pos:
                    continue
                p_ = px(c, date, "open")
                if p_ is None or p_ <= 0:
                    still_pending.append(o)
                    continue
                # 相对信号日收盘的跳空
                sig_close = o.get("sig_px")
                if sig_close and sig_close > 0:
                    gap_pnls.append(p_ / sig_close - 1)
                s = pos[c]["s"]
                cash += s * p_ - max(s * p_ * cost, 0)
                pnl = (p_ - pos[c]["ep"]) / pos[c]["ep"]
                trds.append({"date": date, "a": "S", "c": c, "p": pnl, "r": o.get("r", "卖")})
                del pos[c]
            for o in buys:
                c = o["c"]
                if c in pos:
                    continue
                if c in cd and dc < cd[c]:
                    continue
                p_ = px(c, date, "open")
                if p_ is None or p_ <= 0:
                    continue
                sig_close = o.get("sig_px")
                if sig_close and sig_close > 0:
                    gap_pnls.append(p_ / sig_close - 1)
                # 仓位: 用挂单时记下的目标金额
                budget = o.get("budget", 0)
                if budget < 100:
                    continue
                s = int(budget / p_ / 100) * 100
                if s <= 0:
                    continue
                cash -= s * p_ + max(s * p_ * cost, 0)
                pos[c] = {
                    "s": s, "ep": p_, "ed": dc, "pk": p_,
                    "park": bool(o.get("park")),
                }
                trds.append({"date": date, "a": "B", "c": c})
                if not o.get("park") and c != bench:
                    lrb = dc
            pending = still_pending

        # 当日收盘价 (信号 + 市值)
        pr = {
            c: all_data[c]["close"][di[date][c]]
            for c in all_data if date in di and c in di[date]
        }
        if not pr:
            continue

        # ---- 2) 趋势 ----
        mu = True
        if bench in all_data and date in di and bench in di[date]:
            hi = di[date][bench]
            hc = all_data[bench]["close"]
            if hi >= 20:
                ma20 = sum(hc[hi - 19: hi + 1]) / 20
                if dm and hi >= 10:
                    mu = hc[hi] > ma20 and hc[hi] > sum(hc[hi - 9: hi + 1]) / 10
                else:
                    mu = hc[hi] > ma20

        # ---- 3) 当日信号 (用收盘) ----
        ts: list = []  # (code, reason) 卖
        for c, p0 in list(pos.items()):
            if c not in pr:
                continue
            # 止损用收盘相对成本 (可执行时 next_open 会拖一天)
            pnl = (pr[c] - p0["ep"]) / p0["ep"]
            if pnl <= st:
                ts.append((c, "止损"))
                cd[c] = dc + 5
            if tr and "pk" in p0 and (pr[c] - p0["pk"]) / p0["pk"] <= -tr:
                ts.append((c, "移动止损"))
                cd[c] = dc + 5
            if "pk" in p0:
                p0["pk"] = max(p0["pk"], pr[c])
        if not mu:
            for c in list(pos.keys()):
                ts.append((c, "空仓"))

        trg = ps
        drb = dc - lrb >= rb
        # 空仓时允许立即评估买入 (不占用轮动窗)
        can_score = mu and (drb or (empty_free and not pos))
        tb: list = []
        park_buy = False
        if can_score:
            raw = {}
            for c in all_data:
                if date in di and c in di[date]:
                    fi = factors_at_index(
                        all_data[c]["close"],
                        all_data[c]["volume"],
                        di[date][c],
                        lb,
                        signed_eff=signed_eff,
                    )
                    if fi:
                        raw[c] = fi
            # 打分宇宙不含基准 (基准只作趋势/底仓)
            raw_sec = {c: f for c, f in raw.items() if c != bench}
            sc, br = score_cross_section(raw_sec, w, abs_mom=am, breadth_min=bm)
            if not sc:
                trg = 0
                if park_bench and not pos and bench in pr:
                    park_buy = True
                    tb = [bench]
                    trg = ps
            else:
                rk = sorted(sc.items(), key=lambda x: x[1], reverse=True)
                mv = 0.015
                if bench in all_data and date in di and bench in di[date]:
                    hi = di[date][bench]
                    if hi >= 20:
                        hr = [
                            all_data[bench]["close"][i] / all_data[bench]["close"][i - 1] - 1
                            for i in range(hi - 19, hi + 1)
                            if all_data[bench]["close"][i - 1] > 0
                        ]
                        mv = statistics.stdev(hr) if len(hr) > 5 else 0.015
                if mv > vh:
                    trg = ps * 0.25
                elif mv > vm:
                    trg = ps * 0.55
                if iv:
                    trg *= max(0.3, min(1.0, 0.015 / mv)) if mv > 0 else 0.9

                fl = []
                for c, s_ in rk:
                    if c == bench:
                        continue
                    if c in di[date]:
                        xi = di[date][c]
                        if xi >= 20:
                            m20 = all_data[c]["close"][xi] / all_data[c]["close"][xi - 20] - 1
                            if m20 > overheat:
                                continue
                    fl.append((c, s_))
                tcs = [c for c, s_ in fl[:tn] if s_ > 0]
                # 行业弱于基准 → 改持基准 (宽基慢牛时减少跑输)
                if prefer_bench and tcs and bench in pr and date in di and bench in di[date]:
                    bi = di[date][bench]
                    if bi >= 20:
                        bm20 = all_data[bench]["close"][bi] / all_data[bench]["close"][bi - 20] - 1
                        top = tcs[0]
                        ti = di[date].get(top)
                        if ti is not None and ti >= 20:
                            sm20 = all_data[top]["close"][ti] / all_data[top]["close"][ti - 20] - 1
                            if sm20 < bm20:
                                tcs = [bench]
                                park_buy = True
                # 持仓是底仓时: 有更强行业则换出
                for c in list(pos.keys()):
                    if c not in tcs:
                        hd = dc - pos[c]["ed"]
                        is_park = bool(pos[c].get("park")) or c == bench
                        if is_park and tcs and tcs[0] != bench:
                            ts.append((c, "底仓换行业"))
                            continue
                        if is_park and tcs == [bench]:
                            continue  # 已是底仓目标
                        if hd < mh:
                            continue
                        if not drb and not is_park:
                            continue
                        cs = sc.get(c, -999)
                        if rk and rk[0][1] > cs * (1 + hyst) and tcs and tcs[0] != bench:
                            ts.append((c, "换仓"))
                for c in tcs:
                    if c not in pos and c in pr:
                        if c in cd and dc < cd[c]:
                            continue
                        tb.append(c)
                if not tcs and park_bench and not pos and bench in pr:
                    park_buy = True
                    tb = [bench]
                if drb and tcs and tcs[0] != bench:
                    lrb = dc
        elif not mu:
            trg = 0

        # 去重卖出列表
        seen_s = set()
        ts_u = []
        for c, rea in ts:
            if c not in seen_s:
                seen_s.add(c)
                ts_u.append((c, rea))
        ts = ts_u

        # ---- 4) 成交 ----
        if fill == "close":
            for c, rea in ts:
                if c in pos and c in pr:
                    p_ = pr[c]
                    s = pos[c]["s"]
                    cash += s * p_ - max(s * p_ * cost, 0)
                    pnl = (p_ - pos[c]["ep"]) / pos[c]["ep"]
                    trds.append({"date": date, "a": "S", "c": c, "p": pnl, "r": rea})
                    del pos[c]
            allow_buy = mu and trg > 0 and tb and (drb or (empty_free and not pos) or park_buy)
            if allow_buy:
                inv = sum(pos[_c]["s"] * pr.get(_c, 0) for _c in pos if _c in pr)
                av = cash * trg - inv
                if av > 100:
                    per = av / len(tb)
                    for c in tb:
                        if c in pr and pr[c] > 0:
                            p_ = pr[c]
                            s = int(per / p_ / 100) * 100
                            if s > 0:
                                cash -= s * p_ + max(s * p_ * cost, 0)
                                pos[c] = {
                                    "s": s, "ep": p_, "ed": dc, "pk": p_,
                                    "park": bool(c == bench and park_bench),
                                }
                                trds.append({"date": date, "a": "B", "c": c})
                                if c != bench and not park_buy:
                                    lrb = dc
        else:
            # next_open: 挂到下一交易日开盘
            for c, rea in ts:
                if c in pos and c in pr:
                    pending.append({
                        "side": "S", "c": c, "r": rea, "sig_px": pr[c],
                    })
            allow_buy = mu and trg > 0 and tb and (
                drb or (empty_free and not pos) or park_buy
                or any(pos.get(c, {}).get("park") for c in list(pos))
            )
            # 卖出底仓换行业: 当日挂卖后允许挂买
            sell_codes = {o["c"] for o in pending if o["side"] == "S"}
            if sell_codes and tb and mu and trg > 0:
                allow_buy = True
            if allow_buy:
                inv = sum(pos[_c]["s"] * pr.get(_c, 0) for _c in pos if _c in pr)
                approx_cash = cash
                for c in sell_codes:
                    if c in pos and c in pr:
                        approx_cash += pos[c]["s"] * pr[c]
                inv_keep = sum(
                    pos[_c]["s"] * pr.get(_c, 0)
                    for _c in pos if _c in pr and _c not in sell_codes
                )
                av = approx_cash * trg - inv_keep
                if av > 100:
                    per = av / len(tb)
                    for c in tb:
                        if c in pr and pr[c] > 0:
                            pending.append({
                                "side": "B", "c": c, "budget": per, "sig_px": pr[c],
                                "park": bool(c == bench and park_bench),
                            })

        inv = sum(pos[_c]["s"] * pr.get(_c, 0) for _c in pos if _c in pr)
        daily.append({"d": date, "v": cash + inv, "mu": mu})

    # 若最后还有 pending 卖, 用末日收盘清掉 (避免悬空)
    if pending and sd:
        date = sd[-1]
        pr = {
            c: all_data[c]["close"][di[date][c]]
            for c in all_data if date in di and c in di[date]
        }
        for o in pending:
            if o["side"] == "S" and o["c"] in pos and o["c"] in pr:
                c = o["c"]
                p_ = pr[c]
                s = pos[c]["s"]
                cash += s * p_ - max(s * p_ * cost, 0)
                pnl = (p_ - pos[c]["ep"]) / pos[c]["ep"]
                trds.append({"date": date, "a": "S", "c": c, "p": pnl, "r": o.get("r", "末日清仓")})
                del pos[c]

    if not daily:
        return None
    fv = daily[-1]["v"]
    # 末日市值
    if pos and sd:
        date = sd[-1]
        pr = {
            c: all_data[c]["close"][di[date][c]]
            for c in all_data if date in di and c in di[date]
        }
        inv = sum(pos[_c]["s"] * pr.get(_c, 0) for _c in pos if _c in pr)
        fv = cash + inv
        daily[-1]["v"] = fv

    pk = INITIAL_CAPITAL
    mdd = 0.0
    for dv in daily:
        if dv["v"] > pk:
            pk = dv["v"]
        dd = (dv["v"] - pk) / pk
        if dd < mdd:
            mdd = dd
    yr = len(daily) / 250
    ann = ((fv / INITIAL_CAPITAL) ** (1 / yr) - 1) * 100 if yr > 0 else 0
    sl = [t for t in trds if t["a"] == "S"]
    wn = [t for t in sl if t["p"] > 0]
    sp = ann / abs(mdd * 100) if mdd != 0 else 0
    avg_gap = statistics.mean(gap_pnls) * 100 if gap_pnls else 0.0
    return {
        "fv": fv,
        "ret": (fv - INITIAL_CAPITAL) / INITIAL_CAPITAL,
        "ann": ann,
        "dd": mdd,
        "n": len(trds),
        "wr": len(wn) / len(sl) * 100 if sl else 0,
        "sp": sp,
        "days": len(daily),
        "d0": daily[0]["d"],
        "d1": daily[-1]["d"],
        "fill": fill,
        "avg_gap_pct": avg_gap,
        "n_gaps": len(gap_pnls),
    }


def format_result(r: dict | None, label: str = "") -> str:
    if not r:
        return f"  {label}: NO DATA"
    extra = ""
    if r.get("n_gaps"):
        extra = f" 跳空均:{r['avg_gap_pct']:+.2f}%"
    return (
        f"  {label:48s} 收益:{r['ret']*100:>+7.1f}% 年化:{r['ann']:>+6.1f}% "
        f"回撤:{r['dd']*100:>-5.1f}% 夏普:{r['sp']:>4.1f} 交易:{r['n']:>3d}{extra}"
    )
