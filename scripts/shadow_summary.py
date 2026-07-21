#!/usr/bin/env python3
"""汇总研究影子独立仓位 (output/shadow_states/).

用法:
  python3 scripts/shadow_summary.py
  python3 scripts/shadow_summary.py --names c01_q10_vt08_soft_oh38,c01_q10_vt08_soft_oh38_t2
  python3 scripts/shadow_summary.py --text-out output/shadow_summary.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import SHADOW_DIR, ensure_dirs  # noqa: E402


def collect_rows(names: str = "") -> list[dict]:
    # live 超额 map
    live_map: dict = {}
    lp = ROOT / "output" / "risk_audit" / "shadow_live.json"
    if lp.exists():
        try:
            for rr in json.loads(lp.read_text(encoding="utf-8")) or []:
                if isinstance(rr, dict) and rr.get("name"):
                    live_map[str(rr["name"])] = rr
        except Exception:
            live_map = {}

    if names:
        files = [SHADOW_DIR / f"{n.strip()}.json" for n in names.split(",") if n.strip()]
    else:
        files = sorted(SHADOW_DIR.glob("*.json"))
    rows = []
    for fp in files:
        if not fp.exists():
            rows.append({"file": fp.name, "missing": True})
            continue
        st = json.loads(fp.read_text(encoding="utf-8"))
        holds = st.get("holdings") or ([st["holding"]] if st.get("holding") else [])
        holds = [h for h in holds if h]
        hnames = ",".join(h.get("name", h.get("code", "?")) for h in holds) or "空仓"
        live = st.get("live") if isinstance(st.get("live"), dict) else {}
        if not live or st.get("live_anchor_nav") is None:
            try:
                from etf_rotation.portfolio import apply_live_metrics

                apply_live_metrics(st)
                live = st.get("live") or {}
            except Exception:
                live = live or {}
        lm = live_map.get(fp.stem) or {}
        live_ret = live.get("return_pct")
        if live_ret is None:
            live_ret = lm.get("live_return_pct")
        days_live = lm.get("days_live")
        if days_live is None:
            days_live = lm.get("live_n_rets")
        if days_live is None:
            days_live = live.get("n_rets")
        thin_live = lm.get("thin_live")
        if thin_live is None and days_live is not None:
            try:
                thin_live = int(days_live) < 5
            except Exception:
                thin_live = None
        rows.append(
            {
                "file": fp.name,
                "stem": fp.stem,
                "config": st.get("config"),
                "total_value": st.get("total_value"),
                "return_pct": st.get("return_pct"),
                "n_holdings": len(holds),
                "holdings": hnames,
                "n_port_rets": len(st.get("port_rets") or []),
                "last_update": st.get("last_update"),
                "last_rebalance": st.get("last_rebalance"),
                "live_return_pct": live_ret,
                "live_start": live.get("start_date") or lm.get("live_start"),
                "live_anchor": live.get("anchor_nav") or lm.get("live_anchor"),
                "bench_return_pct": lm.get("bench_return_pct"),
                "live_excess_pct": lm.get("live_excess_pct"),
                "days_live": days_live,
                "thin_live": thin_live,
                "missing": False,
            }
        )
    return rows



def _data_lag_note() -> tuple[str, str, bool]:
    """返回 (asof, text_note, is_lag)."""
    asof = None
    lag = False
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
                if sl.get("market_asof"):
                    asof = sl.get("market_asof")
    except Exception:
        pass
    note = ""
    if lag or asof:
        note = f"行情截至: {asof or '—'}" + ("  DATA_LAG" if lag else "")
    return str(asof or ""), note, lag

def format_text(rows: list[dict]) -> str:
    lines = [
        "--------",
        f"影子仓位 · {SHADOW_DIR}",
        "--------",
    ]
    if not rows:
        lines.append("(无影子 state)")
        return "\n".join(lines)
    thin_any = False
    for row in rows:
        if row.get("missing"):
            lines.append(f"  缺: {row.get('file')}")
            continue
        lr = row.get("live_return_pct")
        xs = row.get("live_excess_pct")
        try:
            lr_s = f"{float(lr):+.2f}%" if lr is not None else "—"
        except Exception:
            lr_s = "—"
        try:
            xs_s = f"{float(xs):+.2f}%" if xs is not None else "—"
        except Exception:
            xs_s = "—"
        tag = ""
        if row.get("thin_live") or (
            row.get("days_live") is not None and int(row.get("days_live") or 0) < 5
        ):
            tag = " THIN"
            thin_any = True
        lines.append(
            f"  {row.get('stem', row.get('file')):36s} "
            f"资产={row.get('total_value')!s:>10}  "
            f"live={lr_s:>8}{tag}  xs={xs_s:>8}  "
            f"全样本={row.get('return_pct')!s:>7}  "
            f"持仓{row.get('n_holdings')}: {row.get('holdings')}  "
            f"rets={row.get('n_port_rets')}"
        )
        if row.get("last_update"):
            dl = row.get("days_live")
            lines.append(
                f"    更新: {row['last_update']}  上次调仓: {row.get('last_rebalance') or '无'}"
                f"  live_from={row.get('live_start') or '—'}"
                f"{'' if dl is None else f'  Lrets={dl}'}"
            )
    if thin_any:
        lines.append("  注: THIN=live 样本<5日; Lrets=0=仅锚日")
        lines.append("  注: DATA_LAG 时等行情更新后再判 xs")
    _, lag_note, lag = _data_lag_note()
    if lag_note:
        lines.append(f"  {lag_note}" + (" (nav/live 以 asof 为准)" if lag else ""))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="影子仓位摘要")
    ap.add_argument("--names", default="", help="逗号分隔策略名; 默认扫目录全部")
    ap.add_argument("--json-out", default="", help="可选写 JSON")
    ap.add_argument("--text-out", default="", help="可选写纯文本 (供邮件附加)")
    args = ap.parse_args()
    ensure_dirs()

    rows = collect_rows(args.names)
    text = format_text(rows)
    print(text)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"WROTE {out}")
    if args.text_out:
        out = Path(args.text_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"WROTE {out}")


if __name__ == "__main__":
    main()
