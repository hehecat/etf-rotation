#!/usr/bin/env python3
"""多影子只读监控: 状态 + 目标暴露 + 告警阈值 (不交易、不改生产).

用法:
  python3 scripts/shadow_monitor.py
  python3 scripts/shadow_monitor.py --shadows c01_q10_vt08_soft_oh38,c01_q10_vt11
  python3 scripts/shadow_monitor.py --bars 120 --text-out output/shadow_monitor.txt
  python3 scripts/shadow_monitor.py --fail-on-alert
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
from etf_rotation import data as data_mod  # noqa: E402
from etf_rotation.paths import STATE_FILE, ensure_dirs, shadow_state_file  # noqa: E402
from etf_rotation.portfolio import compute_target_exposure, load_state  # noqa: E402
from etf_rotation.signal import market_trend  # noqa: E402

try:
    from etf_rotation.research_mainline import MONITOR_SHADOWS

    DEFAULT_SHADOWS = list(MONITOR_SHADOWS)
except Exception:
    DEFAULT_SHADOWS = [
        "c01_q10_vt08_soft_oh38",
        "c01_q10_vt08_soft_oh38_xgn",
        "c01_q10_vt09_oh35",
        "c01_q10_vt11",
    ]
DEFAULT_MIN_RETS = 20
DEFAULT_MIN_VOL_SCALE = 0.05


def evaluate_alerts(
    row: dict,
    *,
    min_rets: int = DEFAULT_MIN_RETS,
    require_portfolio_vol: bool = True,
) -> list[dict]:
    """返回告警列表 [{level, code, msg}]. level=error|warn."""
    alerts: list[dict] = []
    if row.get("error"):
        alerts.append({"level": "error", "code": "monitor_error", "msg": str(row["error"])})
        return alerts
    if not row.get("exists"):
        alerts.append({"level": "error", "code": "missing_state", "msg": "影子 state 不存在"})
        return alerts

    n_rets = int(row.get("n_port_rets") or 0)
    if n_rets < min_rets:
        alerts.append(
            {
                "level": "warn",
                "code": "thin_rets",
                "msg": f"port_rets={n_rets} < {min_rets} (建议 shadow_warmup)",
            }
        )

    has_vt = bool(row.get("has_vol_target"))
    src = row.get("vol_src")
    if require_portfolio_vol and has_vt and n_rets >= min_rets and src != "portfolio":
        alerts.append(
            {
                "level": "error",
                "code": "vol_src_not_portfolio",
                "msg": f"vol_src={src} want portfolio (rets={n_rets})",
            }
        )

    te = float(row.get("target_exposure") or 0)
    if row.get("market_ok") and te <= 0 and has_vt:
        alerts.append(
            {
                "level": "warn",
                "code": "zero_exposure_in_uptrend",
                "msg": f"趋势开但 target_exposure={te}",
            }
        )

    vs = row.get("vol_scale")
    if vs is not None and has_vt and n_rets >= min_rets:
        try:
            v = float(vs)
            if v < DEFAULT_MIN_VOL_SCALE:
                alerts.append(
                    {
                        "level": "warn",
                        "code": "vol_scale_floor",
                        "msg": f"vol_scale={v} 过低",
                    }
                )
        except (TypeError, ValueError):
            pass
    return alerts


def monitor_one(name: str, bars: int, *, min_rets: int = DEFAULT_MIN_RETS) -> dict:
    strat = cfgmod.load_strategy(name)
    pool = cfgmod.load_pool("pool")
    bench = strat.get("bench") or pool.get("bench") or "SH510300"
    bench_bars = data_mod.fetch_bench(bench, count=bars, min_bars=22)
    market_ok = False
    bench_px = ma20 = None
    if bench_bars:
        market_ok, bench_px, ma20, _ = market_trend(
            bench_bars, dual_ma=bool(strat.get("dual_ma", False))
        )
    path = shadow_state_file(name)
    initial = float(strat.get("initial_capital", 100000))
    st = load_state(path, initial, strat.get("name", name)) if path.exists() else {}
    port_rets = list(st.get("port_rets") or [])
    exp = compute_target_exposure(
        strat,
        market_ok=market_ok,
        bench_bars=bench_bars,
        port_rets=port_rets,
    )
    holds = st.get("holdings") or ([st["holding"]] if st.get("holding") else [])
    holds = [h for h in holds if h]
    hnames = ",".join(h.get("name", h.get("code", "?")) for h in holds) or "空仓"
    parts = exp.get("parts") or {}
    vt = strat.get("vol_target")
    has_vt = vt not in (None, 0, 0.0, False)

    # live 段 (暖机末→今); 只读补算, 默认不写回
    live = st.get("live") if isinstance(st.get("live"), dict) else {}
    if not live or st.get("live_anchor_nav") is None:
        try:
            from etf_rotation.portfolio import apply_live_metrics

            apply_live_metrics(st)
            live = st.get("live") or {}
        except Exception:
            live = live or {}

    # 超额/薄样本: 读 shadow_live.json 若有
    bench_ret = None
    excess = None
    days_live = (live or {}).get("n_rets")
    thin_live = None
    try:
        lp = ROOT / "output" / "risk_audit" / "shadow_live.json"
        if lp.exists():
            for rr in json.loads(lp.read_text(encoding="utf-8")) or []:
                if isinstance(rr, dict) and rr.get("name") == name:
                    if live.get("return_pct") is None and rr.get("live_return_pct") is not None:
                        live = dict(live)
                        live["return_pct"] = rr.get("live_return_pct")
                    bench_ret = rr.get("bench_return_pct")
                    excess = rr.get("live_excess_pct")
                    if rr.get("days_live") is not None:
                        days_live = rr.get("days_live")
                    elif rr.get("live_n_rets") is not None:
                        days_live = rr.get("live_n_rets")
                    thin_live = rr.get("thin_live")
                    break
    except Exception:
        pass
    if thin_live is None and days_live is not None:
        try:
            thin_live = int(days_live) < 5
        except Exception:
            thin_live = None

    row = {
        "name": name,
        "path": str(path),
        "exists": path.exists(),
        "market_ok": market_ok,
        "bench_px": bench_px,
        "ma20": ma20,
        "target_exposure": exp.get("target_exposure"),
        "vol_src": parts.get("vol_src"),
        "vol_scale": parts.get("vol_scale"),
        "regime": parts.get("regime"),
        "regime_mult": parts.get("regime_mult"),
        "inv_vol_scale": parts.get("inv_vol_scale"),
        "n_port_rets": len(port_rets),
        "total_value": st.get("total_value"),
        "return_pct": st.get("return_pct"),
        "holdings": hnames,
        "n_holdings": len(holds),
        "warmup": st.get("warmup"),
        "notes": exp.get("notes") or [],
        "has_vol_target": has_vt,
        "research": bool(strat.get("research")),
        "live": live or {},
        "live_return_pct": (live or {}).get("return_pct"),
        "live_start": (live or {}).get("start_date"),
        "live_anchor": (live or {}).get("anchor_nav"),
        "live_sharpe": (live or {}).get("sharpe"),
        "live_n_rets": days_live if days_live is not None else (live or {}).get("n_rets"),
        "days_live": days_live,
        "thin_live": thin_live,
        "bench_return_pct": bench_ret,
        "live_excess_pct": excess,
    }
    row["alerts"] = evaluate_alerts(row, min_rets=min_rets)
    row["alert_error_n"] = sum(1 for a in row["alerts"] if a["level"] == "error")
    row["alert_warn_n"] = sum(1 for a in row["alerts"] if a["level"] == "warn")
    return row


def format_text(rows: list[dict], bench_line: str) -> str:
    lines = [
        "======== 影子只读监控 ========",
        bench_line,
        f"生产 STATE: {STATE_FILE} (本脚本只读)",
        "--------",
    ]
    all_alerts: list[str] = []
    for r in rows:
        if r.get("error") and not r.get("alerts"):
            lines.append(f"{r.get('name')}  ERROR {r.get('error')}")
            lines.append("--------")
            continue
        lines.append(
            f"{r['name']}"
            f"  暴露={float(r.get('target_exposure') or 0)*100:5.1f}%"
            f"  regime={r.get('regime')}×{r.get('regime_mult')}"
            f"  vol_src={r.get('vol_src')} scale={r.get('vol_scale')}"
            f"  rets={r.get('n_port_rets')}"
        )
        lines.append(
            f"  账户={r.get('total_value')} 收益={r.get('return_pct')} "
            f"持仓{r.get('n_holdings')}: {r.get('holdings')}"
        )
        lr = r.get("live_return_pct")
        if lr is not None:
            try:
                lr_s = f"{float(lr):+.3f}%"
            except Exception:
                lr_s = str(lr)
            xs = r.get("live_excess_pct")
            br = r.get("bench_return_pct")
            try:
                xs_s = f"{float(xs):+.3f}%" if xs is not None else "—"
            except Exception:
                xs_s = "—"
            try:
                br_s = f"{float(br):+.3f}%" if br is not None else "—"
            except Exception:
                br_s = "—"
            thin = ""
            if r.get("thin_live") or (
                r.get("days_live") is not None and int(r.get("days_live") or 0) < 5
            ):
                thin = " THIN"
            dl = r.get("days_live")
            lines.append(
                f"  live={lr_s}{thin} xs={xs_s} bench={br_s} "
                f"anchor={r.get('live_anchor')} from={r.get('live_start')}"
                f"{'' if dl is None else f' Lrets={dl}'}"
            )
        if r.get("warmup"):
            w = r["warmup"]
            lines.append(
                f"  暖机: {w.get('d0')}~{w.get('d1')} n_rets={w.get('n_rets')} "
                f"bt_sh={w.get('bt_sharpe')}"
            )
        for n in (r.get("notes") or [])[:2]:
            lines.append(f"  注: {n}")
        for a in r.get("alerts") or []:
            tag = "ALERT" if a["level"] == "error" else "WARN"
            lines.append(f"  !! {tag} [{a['code']}] {a['msg']}")
            all_alerts.append(f"{r['name']}: {a['code']}")
        lines.append("--------")
    err_n = sum(int(r.get("alert_error_n") or 0) for r in rows)
    warn_n = sum(int(r.get("alert_warn_n") or 0) for r in rows)
    lines.append(f"告警汇总: error={err_n} warn={warn_n}")
    if all_alerts:
        lines.append("  " + "; ".join(all_alerts[:12]))
    # DATA_LAG / asof
    try:
        from etf_rotation.calendar_util import resolve_trading_day
        from etf_rotation.paths import LATEST_JSON
        import json as _json
        td = resolve_trading_day()
        asof = td.get("data_asof")
        lag = bool(td.get("data_lag"))
        if LATEST_JSON.exists():
            lj = _json.loads(LATEST_JSON.read_text(encoding="utf-8"))
            if isinstance(lj, dict):
                asof = lj.get("market_asof") or asof
                sl = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else {}
                if sl.get("data_lag") is not None:
                    lag = bool(sl.get("data_lag"))
        if lag or asof:
            lines.append(
                f"行情截至: {asof or '—'}"
                + ("  DATA_LAG (nav/live 以 asof 为准, 等数据更新后再判 xs)" if lag else "")
            )
    except Exception:
        pass
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="多影子只读监控")
    ap.add_argument("--shadows", default=",".join(DEFAULT_SHADOWS))
    ap.add_argument("--bars", type=int, default=120)
    ap.add_argument("--text-out", default="")
    ap.add_argument("--json-out", default="output/risk_audit/shadow_monitor.json")
    ap.add_argument("--min-rets", type=int, default=DEFAULT_MIN_RETS)
    ap.add_argument(
        "--fail-on-alert",
        action="store_true",
        help="存在 error 级告警时 exit 2",
    )
    ap.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="warn 也导致非零退出",
    )
    args = ap.parse_args()
    ensure_dirs()

    names = [x.strip() for x in args.shadows.split(",") if x.strip()]
    first = cfgmod.load_strategy(names[0])
    bench = first.get("bench") or "SH510300"
    bb = data_mod.fetch_bench(bench, count=args.bars, min_bars=22)
    if bb:
        ok, px, ma20, chg = market_trend(bb, dual_ma=False)
        dist = (px / ma20 - 1) * 100 if ma20 else 0
        bench_line = (
            f"基准 {bench}: {px:.3f} MA20={ma20:.3f}({dist:+.1f}%) "
            f"20日={chg:+.1f}% 趋势={'开' if ok else '关'}"
        )
    else:
        bench_line = f"基准 {bench}: 无数据"

    rows = []
    for name in names:
        try:
            rows.append(monitor_one(name, args.bars, min_rets=args.min_rets))
        except Exception as e:
            rows.append(
                {
                    "name": name,
                    "error": str(e),
                    "exists": False,
                    "alerts": [
                        {"level": "error", "code": "monitor_error", "msg": str(e)}
                    ],
                    "alert_error_n": 1,
                    "alert_warn_n": 0,
                }
            )

    text = format_text(rows, bench_line)
    print(text)

    err_n = sum(int(r.get("alert_error_n") or 0) for r in rows)
    warn_n = sum(int(r.get("alert_warn_n") or 0) for r in rows)
    payload = {
        "bench": bench_line,
        "rows": rows,
        "alert_error_n": err_n,
        "alert_warn_n": warn_n,
        "ok": err_n == 0 and (not args.fail_on_warn or warn_n == 0),
    }

    if args.text_out:
        p = Path(args.text_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
        print(f"WROTE {p}")
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {p}")

    if args.fail_on_alert and err_n > 0:
        raise SystemExit(2)
    if args.fail_on_warn and warn_n > 0:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
