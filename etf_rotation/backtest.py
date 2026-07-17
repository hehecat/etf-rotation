"""回测引擎 (从 V8-V10 提炼)."""
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
    all_data: {code: {dates, close, volume, name?}}
    p: strategy_for_backtest() 风格参数
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

    cash = INITIAL_CAPITAL
    pos: dict = {}
    trds: list = []
    daily: list = []
    cd: dict = {}
    lrb = -100
    dc = 0
    start = max(65, lb + 5)

    for idx in range(start, len(sd)):
        date = sd[idx]
        dc += 1
        pr = {
            c: all_data[c]["close"][di[date][c]]
            for c in all_data if date in di and c in di[date]
        }
        if not pr:
            continue

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

        ts: list = []
        for c, p0 in list(pos.items()):
            if c not in pr:
                continue
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
        tb: list = []
        if drb and mu:
            raw = {}
            for c in all_data:
                if date in di and c in di[date]:
                    fi = factors_at_index(
                        all_data[c]["close"], all_data[c]["volume"], di[date][c], lb
                    )
                    if fi:
                        raw[c] = fi
            sc, br = score_cross_section(raw, w, abs_mom=am, breadth_min=bm)
            if not sc:
                trg = 0
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
                    if c in di[date]:
                        xi = di[date][c]
                        if xi >= 20:
                            m20 = all_data[c]["close"][xi] / all_data[c]["close"][xi - 20] - 1
                            if m20 > overheat:
                                continue
                    fl.append((c, s_))
                tcs = [c for c, s_ in fl[:tn] if s_ > 0]
                for c in list(pos.keys()):
                    if c not in tcs:
                        hd = dc - pos[c]["ed"]
                        if hd < mh:
                            continue
                        cs = sc.get(c, -999)
                        if rk and rk[0][1] > cs * (1 + hyst):
                            ts.append((c, "换仓"))
                for c in tcs:
                    if c not in pos and c in pr:
                        if c in cd and dc < cd[c]:
                            continue
                        tb.append(c)
                lrb = dc
        elif not mu:
            trg = 0

        for c, rea in ts:
            if c in pos and c in pr:
                p_ = pr[c]
                s = pos[c]["s"]
                cash += s * p_ - max(s * p_ * cost, 0)
                pnl = (p_ - pos[c]["ep"]) / pos[c]["ep"]
                trds.append({"date": date, "a": "S", "c": c, "p": pnl, "r": rea})
                del pos[c]

        if drb and mu and trg > 0 and tb:
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
                            pos[c] = {"s": s, "ep": p_, "ed": dc, "pk": p_}
                            trds.append({"date": date, "a": "B", "c": c})

        inv = sum(pos[_c]["s"] * pr.get(_c, 0) for _c in pos if _c in pr)
        daily.append({"d": date, "v": cash + inv, "mu": mu})

    if not daily:
        return None
    fv = daily[-1]["v"]
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
    }


def format_result(r: dict | None, label: str = "") -> str:
    if not r:
        return f"  {label}: NO DATA"
    return (
        f"  {label:48s} 收益:{r['ret']*100:>+7.1f}% 年化:{r['ann']:>+6.1f}% "
        f"回撤:{r['dd']*100:>-5.1f}% 夏普:{r['sp']:>4.1f} 交易:{r['n']:>3d}"
    )
