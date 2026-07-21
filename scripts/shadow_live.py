#!/usr/bin/env python3
"""主线影子 live 段收益一览 (暖机末净值 → 今).

用法:
  python3 scripts/shadow_live.py
  python3 scripts/shadow_live.py --text-out output/shadow_live.txt
  python3 scripts/etf.py live
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import SHADOW_DIR, ensure_dirs, shadow_state_file  # noqa: E402
from etf_rotation.portfolio import apply_live_metrics  # noqa: E402
from etf_rotation.research_mainline import MONITOR_SHADOWS, SIGNAL_SHADOW  # noqa: E402


def _bench_return_pct(
    start_date: str | None,
    *,
    bench: str = "SH510300",
    bars: int = 160,
    series: dict | None = None,
) -> dict:
    """基准从 start_date 收盘 → 最新收盘的涨跌幅 (%).

    start 当日为锚 (与 live 一致, 不含 start 当日收益).
    若 start 不在序列中, 取 >= start 的第一根; 若仍无, 返回 None.
    """
    out: dict = {
        "bench": bench,
        "start_date": start_date,
        "end_date": None,
        "return_pct": None,
        "n": 0,
    }
    if not start_date:
        return out
    bb = series
    if bb is None:
        try:
            from etf_rotation import data as data_mod

            bb = data_mod.fetch_bench(bench, count=bars, min_bars=22)
        except Exception as e:
            out["error"] = str(e)
            return out
    if not bb:
        out["error"] = "no_bench"
        return out
    dates = list(bb.get("dates") or [])
    closes = list(bb.get("close") or [])
    if not dates or len(dates) != len(closes):
        out["error"] = "bad_bench_series"
        return out
    # 找锚: 精确匹配, 否则第一根 >= start
    idx = None
    for i, d in enumerate(dates):
        if d == start_date:
            idx = i
            break
    if idx is None:
        for i, d in enumerate(dates):
            if d >= start_date:
                idx = i
                break
    if idx is None:
        out["error"] = "start_beyond_bench"
        return out
    c0 = float(closes[idx])
    c1 = float(closes[-1])
    out["start_date"] = dates[idx]
    out["end_date"] = dates[-1]
    out["n"] = len(dates) - idx
    if c0 > 0:
        out["return_pct"] = round((c1 / c0 - 1.0) * 100.0, 4)
    return out


def collect(names: list[str] | None = None, *, bars: int = 160) -> list[dict]:
    names = list(names or MONITOR_SHADOWS)
    try:
        from etf_rotation import config as cfgmod
        from etf_rotation import data as data_mod

        first = cfgmod.load_strategy(names[0]) if names else {}
        bench = (first or {}).get("bench") or "SH510300"
        bench_series = data_mod.fetch_bench(bench, count=bars, min_bars=22)
    except Exception:
        bench = "SH510300"
        bench_series = None

    rows: list[dict] = []
    for name in names:
        path = shadow_state_file(name)
        if not path.exists():
            rows.append(
                {
                    "name": name,
                    "exists": False,
                    "signal": name == SIGNAL_SHADOW,
                }
            )
            continue
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            rows.append({"name": name, "exists": True, "error": str(e)})
            continue
        # 补锚点 (旧 state 无 live_*)
        if st.get("live_anchor_nav") is None and st.get("warmup"):
            w = st["warmup"]
            st["live_anchor_nav"] = float(st.get("cash") or st.get("total_value") or 0)
            st["live_start_date"] = st.get("live_start_date") or w.get("d1")
        apply_live_metrics(st)
        live = st.get("live") or {}
        holds = st.get("holdings") or ([st["holding"]] if st.get("holding") else [])
        holds = [h for h in holds if h]
        trades = st.get("trades") or []
        last_trade = trades[-1] if trades else None

        br = _bench_return_pct(
            live.get("start_date"), bench=bench, bars=bars, series=bench_series
        )
        live_ret = live.get("return_pct")
        bench_ret = br.get("return_pct")
        excess = None
        if live_ret is not None and bench_ret is not None:
            excess = round(float(live_ret) - float(bench_ret), 4)
        n_rets = int(live.get("n_rets") or 0)
        # days_live: 锚日之后的 nav 点数 (含当日锚则 n_nav-1≈n_rets)
        days_live = n_rets
        thin_live = days_live < 5
        rows.append(
            {
                "name": name,
                "exists": True,
                "signal": name == SIGNAL_SHADOW,
                "total_value": st.get("total_value"),
                "return_pct_total": st.get("return_pct"),
                "holdings": ",".join(
                    h.get("name", h.get("code", "?")) for h in holds
                )
                or "空仓",
                "n_holdings": len(holds),
                "last_update": st.get("last_update"),
                "last_trade": (
                    f"{last_trade.get('action')} {last_trade.get('name')}"
                    if isinstance(last_trade, dict)
                    else None
                ),
                "warmup": bool(st.get("warmup")),
                "bt_sharpe": (st.get("warmup") or {}).get("bt_sharpe"),
                "bt_dd": (st.get("warmup") or {}).get("bt_dd"),
                "live": live,
                "live_return_pct": live_ret,
                "live_anchor": live.get("anchor_nav"),
                "live_start": live.get("start_date"),
                "live_sharpe": live.get("sharpe"),
                "live_n_rets": n_rets,
                "live_n_trades": live.get("n_trades"),
                "days_live": days_live,
                "thin_live": thin_live,
                "bench": bench,
                "bench_return_pct": bench_ret,
                "bench_end": br.get("end_date"),
                "live_excess_pct": excess,
            }
        )
    return rows


def format_text(rows: list[dict]) -> str:
    bench = next((r.get("bench") for r in rows if r.get("bench")), "SH510300")
    lines = [
        "======== 主线影子 LIVE 收益 ========",
        f"信号默认: {SIGNAL_SHADOW}",
        f"目录: {SHADOW_DIR}",
        "口径: live% = (当前净值 / 暖机末锚点) - 1; 不含暖机段回测收益",
        f"超额: excess% = live% - 基准{bench}同期%",
        "----------------------------",
        f"{'name':30s} {'live%':>8} {'bench%':>8} {'xs%':>8} {'nav':>10} {'Lrets':>5} note",
    ]
    thin_any = False
    for r in rows:
        if not r.get("exists"):
            lines.append(f"  {r['name']:28s}  MISSING")
            continue
        if r.get("error"):
            lines.append(f"  {r['name']:28s}  ERROR {r['error']}")
            continue
        lr = r.get("live_return_pct")
        br = r.get("bench_return_pct")
        xs = r.get("live_excess_pct")
        lr_s = f"{float(lr):+.3f}%" if lr is not None else "—"
        br_s = f"{float(br):+.3f}%" if br is not None else "—"
        xs_s = f"{float(xs):+.3f}%" if xs is not None else "—"
        nav = r.get("total_value")
        nav_s = f"{float(nav):.0f}" if nav is not None else "—"
        nr = int(r.get("days_live") or r.get("live_n_rets") or 0)
        note = []
        if r.get("signal"):
            note.append("SIGNAL")
        hold = r.get("holdings") or "空仓"
        if hold and hold != "空仓":
            note.append(f"H={hold[:10]}")
        if r.get("live_start"):
            note.append(f"from {r['live_start']}")
        if r.get("last_trade"):
            note.append(str(r["last_trade"]))
        if r.get("live_sharpe") is not None:
            note.append(f"Lsh={r['live_sharpe']}")
        if r.get("thin_live") or nr < 5:
            note.append("THIN")
            thin_any = True
        lines.append(
            f"  {r['name']:28s} {lr_s:>8} {br_s:>8} {xs_s:>8} {nav_s:>10} "
            f"{nr:>5} {' '.join(note)}"
        )
    lines.append("----------------------------")
    lines.append("提示: 趋势关空仓 live≈0; 若基准下跌则 excess>0 表示避险有效")
    if thin_any:
        lines.append(
            "提示: THIN=live 样本<5日 (暖机末≈最新日 / DATA_LAG 时 xs 常为0)"
        )
        lines.append(
            "提示: Lrets=0 表示仅有锚日、尚无 live 样本日; 等行情更新后再判 xs"
        )
    lines.append("  python3 scripts/etf.py daily --dry-run")
    lines.append("========")
    return "\n".join(lines)


def patch_latest_json(rows: list[dict]) -> Path | None:
    """把 SIGNAL live/xs/THIN 写进 latest.json 顶层, 便于 today/面板快读."""
    from etf_rotation.paths import LATEST_JSON

    if not LATEST_JSON.exists():
        return None
    try:
        payload = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    by_name = {
        str(r.get("name")): r
        for r in rows
        if isinstance(r, dict) and r.get("name") and r.get("exists") is not False
    }
    sig = by_name.get(SIGNAL_SHADOW) or next(
        (r for r in by_name.values() if r.get("signal") or r.get("is_signal_default")),
        None,
    )
    if not sig:
        return None

    days = sig.get("days_live")
    if days is None:
        days = sig.get("live_n_rets")
    thin = sig.get("thin_live")
    if thin is None and days is not None:
        try:
            thin = int(days) < 5
        except Exception:
            thin = None

    # 行情截至 / 滞后 (便于 today/面板/邮件同源)
    market_asof = payload.get("market_asof")
    data_lag = False
    try:
        from etf_rotation.calendar_util import resolve_trading_day

        td = resolve_trading_day()
        if not market_asof:
            market_asof = td.get("data_asof")
        data_lag = bool(td.get("data_lag"))
        if market_asof and not payload.get("market_asof"):
            payload["market_asof"] = market_asof
    except Exception:
        pass

    signal_live = {
        "name": sig.get("name"),
        "live_return_pct": sig.get("live_return_pct"),
        "bench_return_pct": sig.get("bench_return_pct"),
        "live_excess_pct": sig.get("live_excess_pct"),
        "days_live": days,
        "thin_live": thin,
        "live_start": sig.get("live_start"),
        "holdings": sig.get("holdings"),
        "total_value": sig.get("total_value"),
        "market_asof": market_asof,
        "data_lag": data_lag,
    }
    payload["signal_live"] = signal_live

    # 同步 shadow.mainline 行上的 excess/thin/days (若有)
    sh = payload.get("shadow")
    if isinstance(sh, dict):
        mainline = sh.get("mainline")
        if isinstance(mainline, list):
            for row in mainline:
                if not isinstance(row, dict):
                    continue
                src = by_name.get(str(row.get("name")))
                if not src:
                    continue
                row["live_return_pct"] = src.get("live_return_pct")
                row["live_excess_pct"] = src.get("live_excess_pct")
                row["bench_return_pct"] = src.get("bench_return_pct")
                row["days_live"] = src.get("days_live")
                row["thin_live"] = src.get("thin_live")
                if src.get("live") is not None:
                    row["live"] = src.get("live")

    LATEST_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # 同步当日时间线: 刷新「最近一日」的 signal_live (避免 latest.time 偏旧时写到旧日)
    try:
        from etf_rotation import report as report_mod
        from etf_rotation.paths import OUTPUT_DIR as _OUT

        ah = _OUT / "action_history.jsonl"
        sl_rec = {
            "name": signal_live.get("name"),
            "live_return_pct": signal_live.get("live_return_pct"),
            "live_excess_pct": signal_live.get("live_excess_pct"),
            "days_live": signal_live.get("days_live"),
            "thin_live": signal_live.get("thin_live"),
            "holdings": signal_live.get("holdings"),
            "market_asof": signal_live.get("market_asof"),
            "data_lag": signal_live.get("data_lag"),
        }
        if ah.exists():
            ah_rows = []
            for ln in ah.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if isinstance(r, dict):
                    ah_rows.append(r)
            ah_rows.sort(key=lambda r: str(r.get("date") or ""))
            if ah_rows:
                ah_rows[-1]["signal_live"] = sl_rec
                ah.write_text(
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ah_rows),
                    encoding="utf-8",
                )
            else:
                report_mod.append_action_history(payload)
        else:
            report_mod.append_action_history(payload)
    except Exception:
        pass
    return LATEST_JSON


def patch_latest_txt(signal_live: dict) -> Path | None:
    """在 latest.txt 末尾 upsert 有效收益块 (不改动作/持仓正文)."""
    from etf_rotation.paths import LATEST_TXT

    if not LATEST_TXT.exists() or not isinstance(signal_live, dict):
        return None
    try:
        text = LATEST_TXT.read_text(encoding="utf-8")
    except Exception:
        return None

    lr = signal_live.get("live_return_pct")
    xs = signal_live.get("live_excess_pct")
    br = signal_live.get("bench_return_pct")
    dl = signal_live.get("days_live")
    thin = signal_live.get("thin_live")
    if thin is None and dl is not None:
        try:
            thin = int(dl) < 5
        except Exception:
            thin = False
    try:
        lr_s = f"{float(lr):+.3f}%" if lr is not None else "—"
    except Exception:
        lr_s = "—"
    try:
        xs_s = f"{float(xs):+.3f}%" if xs is not None else "—"
    except Exception:
        xs_s = "—"
    try:
        br_s = f"{float(br):+.3f}%" if br is not None else "—"
    except Exception:
        br_s = "—"
    tag = " THIN" if thin else ""
    block_lines = [
        "",
        "-------- 有效收益 (SIGNAL live) --------",
        f"  策略: {signal_live.get('name') or SIGNAL_SHADOW}",
        f"  live={lr_s}{tag}  xs={xs_s}  bench={br_s}",
        f"  from={signal_live.get('live_start') or '—'}  Lrets={dl if dl is not None else '—'}  "
        f"持仓={signal_live.get('holdings') or '—'}",
    ]
    if thin:
        if dl is not None:
            try:
                if int(dl) == 0:
                    block_lines.append("  注: Lrets=0=仅锚日, 尚无 live 样本日 (DATA_LAG/刚暖机常见)")
            except Exception:
                pass
        block_lines.append("  注: THIN=样本<5日; DATA_LAG 时等行情更新后再判 xs")
    asof = signal_live.get("market_asof")
    if asof or signal_live.get("data_lag"):
        block_lines.append(
            f"  行情截至: {asof or '—'}"
            + ("  DATA_LAG" if signal_live.get("data_lag") else "")
        )
    block_lines.append("  口径: live%=暖机末→今 · xs%=live−基准 · 非全样本")
    block_lines.append("----------------------------------------")
    block = "\n".join(block_lines)

    start_mark = "-------- 有效收益 (SIGNAL live) --------"
    end_mark = "----------------------------------------"
    if start_mark in text:
        pre, rest = text.split(start_mark, 1)
        if end_mark in rest:
            _, post = rest.split(end_mark, 1)
            # drop one leading newline from post if present
            text = pre.rstrip("\n") + "\n" + block.lstrip("\n") + (
                post if post.startswith("\n") else "\n" + post
            )
        else:
            text = pre.rstrip("\n") + "\n" + block.lstrip("\n") + "\n"
    else:
        text = text.rstrip("\n") + "\n" + block + "\n"

    LATEST_TXT.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return LATEST_TXT




def main() -> None:
    ap = argparse.ArgumentParser(description="主线影子 live 收益")
    ap.add_argument("--names", default="", help="逗号分隔; 默认 MONITOR_SHADOWS")
    ap.add_argument("--json-out", default="output/risk_audit/shadow_live.json")
    ap.add_argument("--text-out", default="output/shadow_live.txt")
    ap.add_argument(
        "--write-states",
        action="store_true",
        help="把补算的 live 字段写回 shadow_states (默认只读)",
    )
    args = ap.parse_args()
    ensure_dirs()
    names = [x.strip() for x in args.names.split(",") if x.strip()] or None
    # write-states: re-apply and save
    if args.write_states:
        from etf_rotation.portfolio import save_state

        for name in names or MONITOR_SHADOWS:
            path = shadow_state_file(name)
            if not path.exists():
                continue
            st = json.loads(path.read_text(encoding="utf-8"))
            if st.get("live_anchor_nav") is None and st.get("warmup"):
                w = st["warmup"]
                st["live_anchor_nav"] = float(
                    st.get("cash") or st.get("total_value") or 0
                )
                st["live_start_date"] = st.get("live_start_date") or w.get("d1")
            apply_live_metrics(st)
            save_state(path, st)
    rows = collect(names)
    text = format_text(rows)
    print(text)
    patched = patch_latest_json(rows)
    if patched is not None:
        print(f"PATCHED {patched} signal_live")
        try:
            payload = json.loads(patched.read_text(encoding="utf-8"))
            sl = payload.get("signal_live") if isinstance(payload, dict) else None
            if isinstance(sl, dict):
                ptxt = patch_latest_txt(sl)
                if ptxt is not None:
                    print(f"PATCHED {ptxt} live block")
        except Exception:
            pass
    if args.json_out:
        p = Path(args.json_out)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {p}")
    if args.text_out:
        p = Path(args.text_out)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
        print(f"WROTE {p}")


if __name__ == "__main__":
    main()
