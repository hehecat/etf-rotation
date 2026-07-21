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
    # 结构牛软趋势: 趋势关时不强制空仓, 改为停靠基准 (避免现金拖累)
    soft_trend = bool(p.get("soft_trend", False))
    # 额外宇宙 (黄金/海外等) 与行业一起打分; 基准仍只做趋势/底仓
    extra_universe = set(p.get("extra_universe") or [])
    # 结构扩展
    state_pos = bool(p.get("state_pos", False))
    park_scale = float(p.get("park_scale", 0.70))
    park_assets = [c for c in (p.get("park_assets") or []) if c]
    park_set = set(park_assets)
    # regime 现金分配: {bull,chop,riskoff,bear} -> 进攻仓位比例 (剩余现金)
    # 这是状态依赖风险预算, 不是固定 ps 线性缩仓
    regime_map = p.get("regime_map") or None
    if regime_map is not None:
        regime_map = {str(k): float(v) for k, v in dict(regime_map).items()}
    # 组合波动目标: 用已实现组合收益滚动波动缩放总暴露
    vol_target = p.get("vol_target", None)
    vol_target = float(vol_target) if vol_target not in (None, 0, 0.0, False) else 0.0
    vol_lookback = int(p.get("vol_lookback", 20))
    vol_wmin = float(p.get("vol_wmin", 0.15))
    vol_wmax = float(p.get("vol_wmax", 1.0))
    # std=普通标准差, down=下行波动, ewma=指数加权
    vol_mode = str(p.get("vol_mode", "std") or "std").lower()
    vol_ewma_span = int(p.get("vol_ewma_span", 20))
    # 组合回撤节流: 从峰值回撤超过阈值后额外降暴露
    dd_throttle = p.get("dd_throttle", None)
    dd_throttle = float(dd_throttle) if dd_throttle not in (None, 0, 0.0, False) else 0.0
    dd_throttle_floor = float(p.get("dd_throttle_floor", 0.25))

    if not all_data:
        return None
    # park/extra 与短历史代码不参与公共日期交集, 避免全样本被压到近两年
    optional = set(park_assets) | set(extra_universe)
    date_lens = {c: len(all_data[c].get("dates") or []) for c in all_data}
    max_n = max(date_lens.values()) if date_lens else 0
    if max_n > 0:
        for c, n in date_lens.items():
            if n < max_n * 0.5:
                optional.add(c)
    optional.discard(bench)
    core_codes = [c for c in all_data if c not in optional]
    if len(core_codes) < 2:
        core_codes = list(all_data.keys())
    sd = sorted(set.intersection(*[set(all_data[c]["dates"]) for c in core_codes]))
    if date_range:
        d0, d1 = date_range
        sd = [d for d in sd if d0 <= d <= d1]
    if len(sd) < 80:
        return None
    # 短历史/停靠资产默认不进进攻打分, 除非 extra_universe 明确纳入
    score_block = set(optional) | set(park_assets)
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
    port_rets: list[float] = []
    prev_nav = float(INITIAL_CAPITAL)

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
        if not mu and not soft_trend:
            for c in list(pos.keys()):
                ts.append((c, "空仓"))
        elif not mu and soft_trend:
            # 软趋势: 非底仓/非对冲停靠清掉, 准备停靠 (不裸奔现金)
            for c in list(pos.keys()):
                is_park = bool(pos[c].get("park")) or c == bench or c in park_set
                if not is_park:
                    ts.append((c, "软趋势回撤"))
        trg = ps
        tb: list = []
        park_buy = False
        regime_applied = False
        drb = dc - lrb >= rb
        # 空仓时允许立即评估买入 (不占用轮动窗)
        # 软趋势关闭时仍可评估 (改为停靠/多资产), 硬趋势关闭则不评分
        can_score = (mu or soft_trend) and (drb or (empty_free and not pos))

        def _m20(code: str) -> float:
            if date not in di or code not in di[date]:
                return -999.0
            xi = di[date][code]
            if xi < 20:
                return -999.0
            c0 = all_data[code]["close"][xi - 20]
            c1 = all_data[code]["close"][xi]
            if c0 <= 0 or c1 <= 0:
                return -999.0
            return c1 / c0 - 1.0

        def _pick_park() -> str | None:
            """趋势关/宽度不足时的停靠标的: bench + park_assets 中 m20 最强且可用."""
            cands = []
            if bench in pr:
                cands.append(bench)
            for c in park_assets:
                if c in pr and c in all_data:
                    cands.append(c)
            if not cands:
                return None
            # 去重保序
            seen_c = set()
            uniq_c = []
            for c in cands:
                if c not in seen_c:
                    seen_c.add(c)
                    uniq_c.append(c)
            return max(uniq_c, key=_m20)

        def _is_park_code(code: str) -> bool:
            return code == bench or code in park_set

        def _state_scale(mv_local: float, ma_dist: float) -> float:
            """非线性状态仓: 趋势强度 + 波动 共同缩放, 不做固定降仓伪装."""
            s = 1.0
            if state_pos:
                # 贴近均线的弱趋势降暴露; 强趋势保留
                if ma_dist < 0.005:
                    s *= 0.55
                elif ma_dist < 0.02:
                    s *= 0.75
                elif ma_dist > 0.06:
                    s *= 0.85  # 过热趋势略降
            # 旧 vol 阶梯
            if mv_local > vh:
                s *= 0.25
            elif mv_local > vm:
                s *= 0.55
            if iv:
                s *= max(0.3, min(1.0, 0.015 / mv_local)) if mv_local > 0 else 0.9
            return max(0.15, min(1.0, s))

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
            # 打分宇宙: 行业 + extra; 基准/停靠/短历史资产默认不进攻
            raw_sec = {
                c: f
                for c, f in raw.items()
                if (c != bench or c in extra_universe)
                and (c not in score_block or c in extra_universe)
            }
            # 相对基准动量 (结构因子)
            if bench in raw:
                bm20 = raw[bench].get("m20", 0.0)
                for c, f in raw_sec.items():
                    f["rel_m20"] = f.get("m20", 0.0) - bm20
            # 趋势关 + soft_trend: 不追行业, 直接准备停靠
            if not mu and soft_trend:
                sc, br = {}, 0.0
            else:
                sc, br = score_cross_section(raw_sec, w, abs_mom=am, breadth_min=bm)
            if not sc:
                trg = 0
                # 宽度不足 / 趋势关 / 无行业标: 非底仓清掉, 停靠基准/对冲资产
                if park_bench or soft_trend:
                    for c in list(pos.keys()):
                        if (not _is_park_code(c)) and not pos[c].get("park"):
                            ts.append((c, "宽度/软趋势停靠"))
                    park_c = _pick_park()
                    if park_c:
                        has_park = any(
                            _is_park_code(c) or bool(pos.get(c, {}).get("park"))
                            for c in pos
                            if c not in {x[0] for x in ts}
                        )
                        remaining = [
                            c for c in pos
                            if c not in {x[0] for x in ts}
                        ]
                        if (not remaining) or (not has_park and not pos):
                            park_buy = True
                            tb = [park_c]
                            # 停靠仓位: 状态仓用 park_scale, 否则满 ps
                            trg = ps * (park_scale if state_pos else 1.0)
                        elif has_park:
                            pass  # 已有底仓则保持
            else:
                rk = sorted(sc.items(), key=lambda x: x[1], reverse=True)
                mv = 0.015
                ma_dist = 0.0
                ma_up = True
                if bench in all_data and date in di and bench in di[date]:
                    hi = di[date][bench]
                    if hi >= 20:
                        hr = [
                            all_data[bench]["close"][i] / all_data[bench]["close"][i - 1] - 1
                            for i in range(hi - 19, hi + 1)
                            if all_data[bench]["close"][i - 1] > 0
                        ]
                        mv = statistics.stdev(hr) if len(hr) > 5 else 0.015
                        ma20b = sum(all_data[bench]["close"][hi - 19: hi + 1]) / 20
                        if ma20b > 0:
                            ma_dist = all_data[bench]["close"][hi] / ma20b - 1
                        if hi >= 60:
                            ma60b = sum(all_data[bench]["close"][hi - 59: hi + 1]) / 60
                            ma_up = ma20b > ma60b
                if state_pos:
                    trg = ps * _state_scale(mv, ma_dist)
                else:
                    if mv > vh:
                        trg = ps * 0.25
                    elif mv > vm:
                        trg = ps * 0.55
                    if iv:
                        trg *= max(0.3, min(1.0, 0.015 / mv)) if mv > 0 else 0.9
                # regime 现金分配 (在 inv_vol/state 之后)
                if regime_map:
                    if not mu and not ma_up:
                        rg = "bear"
                    elif not mu:
                        rg = "riskoff"
                    elif mu and ma_up and mv < 0.02:
                        rg = "bull"
                    else:
                        rg = "chop"
                    trg *= max(0.0, min(1.0, regime_map.get(rg, 1.0)))
                    regime_applied = True

                fl = []
                for c, s_ in rk:
                    # 基准默认不进行业轮动, 除非 extra_universe 明确纳入
                    if c == bench and c not in extra_universe:
                        continue
                    if c in di[date]:
                        xi = di[date][c]
                        if xi >= 20:
                            m20 = all_data[c]["close"][xi] / all_data[c]["close"][xi - 20] - 1
                            if m20 > overheat:
                                continue
                    fl.append((c, s_))
                tcs = [c for c, s_ in fl[:tn] if s_ > 0]
                # 行业弱于基准 → 改持基准/对冲资产 (宽基慢牛时减少跑输)
                if prefer_bench and tcs and bench in pr and date in di and bench in di[date]:
                    bi = di[date][bench]
                    if bi >= 20:
                        bm20 = all_data[bench]["close"][bi] / all_data[bench]["close"][bi - 20] - 1
                        top = tcs[0]
                        ti = di[date].get(top)
                        if ti is not None and ti >= 20:
                            sm20 = all_data[top]["close"][ti] / all_data[top]["close"][ti - 20] - 1
                            if sm20 < bm20:
                                park_c = _pick_park() or bench
                                tcs = [park_c]
                                park_buy = True
                tcs_set = set(tcs)
                # 持仓是底仓时: 有更强行业则换出
                for c in list(pos.keys()):
                    if c in tcs_set:
                        continue
                    hd = dc - pos[c]["ed"]
                    is_park = bool(pos[c].get("park")) or _is_park_code(c)
                    if is_park and tcs and not _is_park_code(tcs[0]):
                        ts.append((c, "底仓换行业"))
                        continue
                    if is_park and tcs and _is_park_code(tcs[0]):
                        # 底仓之间也可轮换到更强停靠标
                        if c != tcs[0] and hd >= mh:
                            ts.append((c, "底仓轮换"))
                        continue
                    if hd < mh:
                        continue
                    if not drb and not is_park:
                        continue
                    # top_n>1: 不在目标集合且调仓窗到 → 直接换出
                    if tn > 1:
                        if tcs and c not in tcs_set:
                            ts.append((c, "换仓"))
                    else:
                        cs = sc.get(c, -999)
                        if rk and rk[0][1] > cs * (1 + hyst) and tcs and not _is_park_code(tcs[0]):
                            ts.append((c, "换仓"))
                for c in tcs:
                    if c not in pos and c in pr:
                        if c in cd and dc < cd[c]:
                            continue
                        tb.append(c)
                if not tcs and (park_bench or soft_trend) and not pos:
                    park_c = _pick_park()
                    if park_c:
                        park_buy = True
                        tb = [park_c]
                        trg = ps * (park_scale if state_pos else 1.0)
                if drb and tcs and not _is_park_code(tcs[0]):
                    lrb = dc
        elif not mu and not soft_trend:
            trg = 0

        # 非调仓日也应用 regime: 用当前持仓市值相对总资产的目标暴露
        if regime_map and not regime_applied:
            ma_up = True
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
                if hi >= 60:
                    ma20b = sum(all_data[bench]["close"][hi - 19: hi + 1]) / 20
                    ma60b = sum(all_data[bench]["close"][hi - 59: hi + 1]) / 60
                    ma_up = ma20b > ma60b
            if not mu and not ma_up:
                rg = "bear"
            elif not mu:
                rg = "riskoff"
            elif mu and ma_up and mv < 0.02:
                rg = "bull"
            else:
                rg = "chop"
            base = ps
            if iv and mv > 0:
                base = ps * max(0.3, min(1.0, 0.015 / mv))
            trg = base * max(0.0, min(1.0, regime_map.get(rg, 1.0)))
            if not mu and not soft_trend:
                trg = 0

        # 组合波动目标: 用已实现组合波动缩放总暴露 (状态风险预算, 非固定ps)
        if vol_target and vol_target > 0 and len(port_rets) >= max(5, vol_lookback // 2):
            window = port_rets[-vol_lookback:] if len(port_rets) >= vol_lookback else port_rets
            if len(window) >= 5:
                if vol_mode == "down":
                    downs = [x for x in window if x < 0]
                    if len(downs) >= 3:
                        mu = 0.0
                        var = sum((x - mu) ** 2 for x in downs) / len(downs)
                        sig = var ** 0.5
                    else:
                        sig = statistics.stdev(window)
                elif vol_mode == "ewma":
                    # 递归 EWMA 方差, span≈N → alpha=2/(N+1)
                    span = max(5, vol_ewma_span)
                    alpha = 2.0 / (span + 1.0)
                    # 用更长历史更稳
                    series = port_rets[-max(span * 3, vol_lookback):]
                    v = series[0] ** 2
                    for x in series[1:]:
                        v = alpha * (x ** 2) + (1 - alpha) * v
                    sig = v ** 0.5
                else:
                    sig = statistics.stdev(window)
                ann = sig * (250 ** 0.5)
                if ann > 1e-6:
                    vw = vol_target / ann
                    vw = max(vol_wmin, min(vol_wmax, vw))
                    trg *= vw

        # 组合回撤节流: 相对权益峰值越深, 暴露越低
        if dd_throttle and dd_throttle > 0 and daily:
            peak = max(dv["v"] for dv in daily) if daily else prev_nav
            peak = max(peak, prev_nav)
            cur = prev_nav
            if peak > 0:
                dd_now = (cur - peak) / peak  # ≤0
                if dd_now < 0:
                    # 线性: dd=0 → 1.0; dd=-dd_throttle → floor
                    depth = min(1.0, abs(dd_now) / dd_throttle)
                    scale = 1.0 - depth * (1.0 - max(0.0, min(1.0, dd_throttle_floor)))
                    trg *= max(0.0, min(1.0, scale))

        # 去重卖出列表
        seen_s = set()
        ts_u = []
        for c, rea in ts:
            if c not in seen_s:
                seen_s.add(c)
                ts_u.append((c, rea))
        ts = ts_u

        if (regime_map or vol_target or dd_throttle) and pos and pr:
            inv_now = sum(pos[_c]["s"] * pr.get(_c, 0) for _c in pos if _c in pr)
            nav = cash + inv_now
            if nav > 0 and inv_now > nav * trg + 100:
                target_inv = max(0.0, nav * trg)
                keep_frac = target_inv / inv_now if inv_now > 0 else 0.0
                if keep_frac < 0.98:
                    for c in list(pos.keys()):
                        if c not in pr or pr[c] <= 0:
                            continue
                        if any(x[0] == c for x in ts):
                            continue
                        s0 = pos[c]["s"]
                        s_keep = int(s0 * keep_frac / 100) * 100
                        s_sell = s0 - s_keep
                        if s_sell >= 100:
                            if s_keep <= 0:
                                ts.append((c, "暴露减仓"))
                            else:
                                pos[c]["_partial_sell"] = s_sell

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
            # 部分减仓
            for c in list(pos.keys()):
                psell = pos[c].pop("_partial_sell", 0)
                if psell and c in pr and pr[c] > 0:
                    p_ = pr[c]
                    s = min(psell, pos[c]["s"])
                    if s > 0:
                        cash += s * p_ - max(s * p_ * cost, 0)
                        pos[c]["s"] -= s
                        trds.append({"date": date, "a": "S", "c": c, "p": 0.0, "r": "regime减仓"})
                        if pos[c]["s"] <= 0:
                            del pos[c]
            allow_buy = trg > 0 and tb and (mu or park_buy) and (
                drb or (empty_free and not pos) or park_buy
            )
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
                                is_park_pos = bool(
                                    (c == bench or c in park_set)
                                    and (park_bench or soft_trend or park_buy)
                                )
                                pos[c] = {
                                    "s": s, "ep": p_, "ed": dc, "pk": p_,
                                    "park": is_park_pos,
                                }
                                trds.append({"date": date, "a": "B", "c": c})
                                if (c != bench and c not in park_set) and not park_buy:
                                    lrb = dc
        else:
            # next_open: 挂到下一交易日开盘
            for c, rea in ts:
                if c in pos and c in pr:
                    pending.append({
                        "side": "S", "c": c, "r": rea, "sig_px": pr[c],
                    })
            for c in list(pos.keys()):
                psell = pos[c].pop("_partial_sell", 0)
                if psell and c in pr and pr[c] > 0:
                    # 部分卖: 挂卖出份额 (开盘路径目前只支持整仓卖)
                    # 降级为整仓卖出若减仓>=50%; 否则保留 (避免复杂 partial pending)
                    if psell >= pos[c]["s"] * 0.5:
                        pending.append({
                            "side": "S", "c": c, "r": "regime减仓", "sig_px": pr[c],
                        })
            allow_buy = trg > 0 and tb and (mu or park_buy) and (
                drb or (empty_free and not pos) or park_buy
                or any(pos.get(c, {}).get("park") for c in list(pos))
            )
            sell_codes = {o["c"] for o in pending if o["side"] == "S"}
            if sell_codes and tb and trg > 0 and (mu or park_buy):
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
                            is_park_pos = bool(
                                (c == bench or c in park_set)
                                and (park_bench or soft_trend or park_buy)
                            )
                            pending.append({
                                "side": "B", "c": c, "budget": per, "sig_px": pr[c],
                                "park": is_park_pos,
                            })

        inv = sum(pos[_c]["s"] * pr.get(_c, 0) for _c in pos if _c in pr)
        nav_now = cash + inv
        if prev_nav > 0:
            port_rets.append(nav_now / prev_nav - 1)
        prev_nav = nav_now
        daily.append({"d": date, "v": nav_now, "mu": mu})

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
    # 旧字段 sp: 年化/回撤 (Calmar 近似, 历史兼容)
    sp = ann / abs(mdd * 100) if mdd != 0 else 0
    # 真实日收益夏普 (无风险自由利率, 250 日年化)
    rets = []
    for i in range(1, len(daily)):
        v0 = daily[i - 1]["v"]
        v1 = daily[i]["v"]
        if v0 > 0:
            rets.append(v1 / v0 - 1)
    if len(rets) > 5:
        mu = statistics.mean(rets)
        sig = statistics.stdev(rets)
        sharpe = (mu / sig) * (250 ** 0.5) if sig > 0 else 0.0
    else:
        sharpe = 0.0
    calmar = (ann / 100.0) / abs(mdd) if mdd != 0 else 0.0
    avg_win = statistics.mean([t["p"] for t in wn]) if wn else 0.0
    losses = [t["p"] for t in sl if t["p"] <= 0]
    avg_loss = statistics.mean(losses) if losses else 0.0
    # 盈亏比用 |均盈/均亏|; 期望值 = wr*avg_win + (1-wr)*avg_loss
    wr_ratio = (len(wn) / len(sl)) if sl else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else (999.0 if avg_win > 0 else 0.0)
    expectancy = wr_ratio * avg_win + (1 - wr_ratio) * avg_loss
    avg_gap = statistics.mean(gap_pnls) * 100 if gap_pnls else 0.0
    return {
        "fv": fv,
        "ret": (fv - INITIAL_CAPITAL) / INITIAL_CAPITAL,
        "ann": ann,
        "dd": mdd,
        "n": len(trds),
        "wr": wr_ratio * 100,
        "sp": sp,  # 兼容: 实际是 calmar 近似
        "sharpe": sharpe,
        "calmar": calmar,
        "payoff": payoff,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "n_sells": len(sl),
        "days": len(daily),
        "d0": daily[0]["d"],
        "d1": daily[-1]["d"],
        "fill": fill,
        "avg_gap_pct": avg_gap,
        "n_gaps": len(gap_pnls),
        "equity": daily,
    }


def format_result(r: dict | None, label: str = "") -> str:
    if not r:
        return f"  {label}: NO DATA"
    extra = ""
    if r.get("n_gaps"):
        extra = f" 跳空均:{r['avg_gap_pct']:+.2f}%"
    sh = r.get("sharpe", r.get("sp", 0))
    cal = r.get("calmar", r.get("sp", 0))
    return (
        f"  {label:48s} 收益:{r['ret']*100:>+7.1f}% 年化:{r['ann']:>+6.1f}% "
        f"回撤:{r['dd']*100:>-5.1f}% 夏普:{sh:>4.2f} 卡玛:{cal:>4.2f} "
        f"胜率:{r['wr']:>4.0f}% 交易:{r['n']:>3d}{extra}"
    )
