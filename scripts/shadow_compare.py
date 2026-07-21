#!/usr/bin/env python3
"""主线影子对照表 (只读): 暖机/净值/持仓/成本/门禁门槛.

用法:
  python3 scripts/shadow_compare.py
  python3 scripts/shadow_compare.py --text-out output/shadow_compare.txt
  python3 scripts/shadow_compare.py --json-out output/risk_audit/shadow_compare.json
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
from etf_rotation.research_mainline import (  # noqa: E402
    LONG_GATES,
    MONITOR_SHADOWS,
    SIGNAL_SHADOW,
)


def _load_cost() -> dict:
    p = ROOT / "output" / "risk_audit" / "cost_sensitivity.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_live_map() -> dict[str, dict]:
    p = ROOT / "output" / "risk_audit" / "shadow_live.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        if isinstance(r, dict) and r.get("name"):
            out[str(r["name"])] = r
    return out



def _cost_3bp(cost_row: dict) -> tuple[float | None, float | None]:
    """兼容 cost_sensitivity 新旧结构."""
    if not cost_row:
        return None, None
    by = cost_row.get("by_comm")
    if isinstance(by, dict):
        cell = by.get("0.0003") or by.get(0.0003)
        if isinstance(cell, dict):
            return cell.get("sharpe"), cell.get("dd")
    # 扁平字段
    sh = cost_row.get("sharpe_3bp") or cost_row.get("cost_3bp_sharpe")
    dd = cost_row.get("dd_3bp") or cost_row.get("cost_3bp_dd")
    return sh, dd


def collect(names: list[str] | None = None) -> list[dict]:
    names = list(names or MONITOR_SHADOWS)
    cost = _load_cost()
    cost_strats = cost.get("strategies") or {}
    live_all = _load_live_map()
    rows: list[dict] = []
    for name in names:
        path = shadow_state_file(name)
        if not path.exists():
            rows.append(
                {
                    "name": name,
                    "exists": False,
                    "is_signal_default": name == SIGNAL_SHADOW,
                    "signal": name == SIGNAL_SHADOW,
                    "gate": LONG_GATES.get(name),
                }
            )
            continue
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            rows.append(
                {
                    "name": name,
                    "exists": True,
                    "error": str(e),
                    "is_signal_default": name == SIGNAL_SHADOW,
                    "signal": name == SIGNAL_SHADOW,
                    "gate": LONG_GATES.get(name),
                }
            )
            continue

        holds = st.get("holdings") or ([st["holding"]] if st.get("holding") else [])
        holds = [h for h in holds if h]
        warm = st.get("warmup") or {}
        cash = float(st.get("cash") or 0)
        tv = float(st.get("total_value") or 0)
        cost_row = cost_strats.get(name) or {}
        c3_sh, c3_dd = _cost_3bp(cost_row)
        trades = st.get("trades") or []
        last_trade = trades[-1] if trades else None
        gate = LONG_GATES.get(name)
        bt_sh = warm.get("bt_sharpe")
        bt_dd = warm.get("bt_dd")
        gate_ok = None
        if gate and bt_sh is not None and bt_dd is not None:
            gate_ok = float(bt_sh) >= float(gate["min_sharpe"]) and abs(
                float(bt_dd)
            ) <= float(gate["max_dd"])

        live = st.get("live") if isinstance(st.get("live"), dict) else None
        if live is None or st.get("live_anchor_nav") is None:
            try:
                from etf_rotation.portfolio import apply_live_metrics

                apply_live_metrics(st)
                live = st.get("live")
            except Exception:
                live = live or {}

        live_ret = (live or {}).get("return_pct")
        bench_ret = None
        excess = None
        days_live = (live or {}).get("n_rets")
        thin_live = None
        live_map = live_all.get(name) or {}
        if live_map:
            if live_ret is None:
                live_ret = live_map.get("live_return_pct")
            bench_ret = live_map.get("bench_return_pct")
            excess = live_map.get("live_excess_pct")
            if live_map.get("days_live") is not None:
                days_live = live_map.get("days_live")
            elif live_map.get("live_n_rets") is not None:
                days_live = live_map.get("live_n_rets")
            thin_live = live_map.get("thin_live")
        if excess is None and live_ret is not None and bench_ret is not None:
            excess = round(float(live_ret) - float(bench_ret), 4)
        if thin_live is None and days_live is not None:
            try:
                thin_live = int(days_live) < 5
            except Exception:
                thin_live = None

        rows.append(
            {
                "name": name,
                "exists": True,
                "is_signal_default": name == SIGNAL_SHADOW,
                "signal": name == SIGNAL_SHADOW,
                "total_value": tv,
                "return_pct": st.get("return_pct"),
                "n_port_rets": len(st.get("port_rets") or []),
                "n_nav": len(st.get("nav_history") or []),
                "n_holdings": len(holds),
                "holdings": ",".join(
                    h.get("name", h.get("code", "?")) for h in holds
                )
                or "空仓",
                "cash": cash,
                "cash_eq_nav": abs(cash - tv) < 1.0 if tv else True,
                "cash_nav_aligned": abs(cash - tv) < 1.0 if tv else True,
                "bt_sharpe": bt_sh,
                "bt_dd": bt_dd,
                "warmup": bool(warm),
                "last_update": st.get("last_update"),
                "last_trade": (
                    f"{last_trade.get('action')} {last_trade.get('name')}"
                    if isinstance(last_trade, dict)
                    else None
                ),
                "live": live or {},
                "live_return_pct": live_ret,
                "live_anchor": (live or {}).get("anchor_nav"),
                "live_start": (live or {}).get("start_date"),
                "live_sharpe": (live or {}).get("sharpe"),
                "live_n_rets": days_live if days_live is not None else (live or {}).get("n_rets"),
                "days_live": days_live,
                "thin_live": thin_live,
                "bench_return_pct": bench_ret,
                "live_excess_pct": excess,
                "cost_3bp_sharpe": c3_sh,
                "cost_3bp_dd": c3_dd,
                "cost_3bp_sh": c3_sh,
                "gate": gate,
                "gate_ok": gate_ok,
            }
        )
    return rows




def format_text(rows: list[dict]) -> str:
    lines = [
        "======== 主线影子对照 ========",
        f"信号默认: {SIGNAL_SHADOW}",
        f"目录: {SHADOW_DIR}",
        "----------------------------",
        f"{'name':30s} {'sh':>5} {'dd':>6} {'live%':>7} {'xs%':>7} {'tv':>10} {'gate':>4} note",
    ]
    for r in rows:
        if not r.get("exists"):
            lines.append(f"  {r['name']:28s}  MISSING")
            continue
        if r.get("error"):
            lines.append(f"  {r['name']:28s}  ERROR {r['error']}")
            continue
        sh = r.get("bt_sharpe")
        dd = r.get("bt_dd")
        sh_s = f"{float(sh):.2f}" if sh is not None else "—"
        dd_s = f"{float(dd)*100:.1f}%" if dd is not None else "—"
        lr = r.get("live_return_pct")
        xs = r.get("live_excess_pct")
        lr_s = f"{float(lr):+.2f}" if lr is not None else "—"
        xs_s = f"{float(xs):+.2f}" if xs is not None else "—"
        gate = "OK" if r.get("gate_ok") else ("?" if r.get("gate") else "—")
        if r.get("gate") and r.get("gate_ok") is False:
            gate = "FAIL"
        note = []
        if r.get("is_signal_default") or r.get("signal"):
            note.append("SIGNAL")
        if r.get("warmup"):
            note.append("warmup")
        if r.get("cash_nav_aligned") is False:
            note.append("CASH≠NAV")
        holds = r.get("holdings") or "空仓"
        if holds and holds != "空仓":
            note.append(f"H={holds}")
        if r.get("last_trade"):
            note.append(f"T={r['last_trade']}")
        if r.get("live_start"):
            note.append(f"L@{r['live_start']}")
        # THIN: live 样本 <5 (来自 shadow_live 字段或 n_rets)
        dl = r.get("days_live")
        if dl is None:
            dl = r.get("live_n_rets")
        if r.get("thin_live") or (dl is not None and int(dl) < 5):
            note.append("THIN")
        lines.append(
            f"  {r['name']:28s} {sh_s:>5} {dd_s:>6} {lr_s:>7} {xs_s:>7} "
            f"{float(r.get('total_value') or 0):>10.0f} {gate:>4} {' '.join(note)}"
        )
    lines.append("----------------------------")
    lines.append("说明: sh/dd=暖机; live%/xs%=暖机末→今 (xs=live-基准); THIN=样本<5日")
    lines.append("  python3 scripts/etf.py live")
    lines.append("  python3 scripts/etf.py check")
    lines.append("========")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="主线影子对照")
    ap.add_argument("--names", default="", help="逗号分隔; 默认 MONITOR_SHADOWS")
    ap.add_argument("--json-out", default="output/risk_audit/shadow_compare.json")
    ap.add_argument("--text-out", default="output/shadow_compare.txt")
    args = ap.parse_args()
    ensure_dirs()
    names = [x.strip() for x in args.names.split(",") if x.strip()] or None
    rows = collect(names)
    text = format_text(rows)
    print(text)
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
