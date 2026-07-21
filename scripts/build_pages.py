#!/usr/bin/env python3
"""生成静态站点: 日更策略可视化面板 (GitHub Pages / 本地预览).

数据源:
  output/latest.json|txt
  output/risk_audit/shadow_monitor.json
  output/risk_audit/research_status.json
  output/risk_audit/pipeline_last.json
  output/shadow_states/*.json (nav_history)
  output/action_history.jsonl (近 N 日动作)
  output/shadow_monitor.txt / shadow_summary.txt / research_status.txt
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import LATEST_JSON, LATEST_TXT, OUTPUT_DIR  # noqa: E402

CSS = """
:root{
  color-scheme:dark;
  --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#e6edf3; --muted:#8b949e;
  --blue:#58a6ff; --green:#3fb950; --red:#f85149; --amber:#d29922; --purple:#a371f7;
}
*{box-sizing:border-box}
body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  background:var(--bg);color:var(--text);margin:0;line-height:1.5}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
header{max-width:72rem;margin:0 auto;padding:1rem 1.1rem .75rem;border-bottom:1px solid var(--line)}
header h1{font-size:1.25rem;margin:0 0 .25rem;font-weight:650}
.muted{color:var(--muted);font-size:.85rem}
nav{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.7rem}
nav a{padding:.28rem .7rem;border:1px solid var(--line);border-radius:999px;color:var(--text);
  background:#0d1117;font-size:.85rem}
nav a.active,nav a:hover{border-color:var(--blue);color:var(--blue);text-decoration:none}
main{max-width:72rem;margin:0 auto;padding:1rem 1.1rem 2.5rem}
.grid{display:grid;gap:.75rem}
.grid.kpis{grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr))}
.grid.two{grid-template-columns:repeat(auto-fit,minmax(18rem,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:.9rem 1rem}
.card h2,.card h3{margin:.1rem 0 .55rem;font-size:1rem;font-weight:650}
.kpi .label{color:var(--muted);font-size:.75rem}
.kpi .value{font-size:1.2rem;font-weight:700;margin-top:.15rem;word-break:break-word}
.kpi .sub{color:var(--muted);font-size:.75rem;margin-top:.15rem}
.badge{display:inline-block;padding:.12rem .5rem;border-radius:999px;font-size:.75rem;
  font-weight:600;margin-right:.3rem;border:1px solid transparent}
.badge.ok{background:rgba(63,185,80,.15);color:var(--green);border-color:rgba(63,185,80,.35)}
.badge.off{background:rgba(110,118,129,.15);color:#c9d1d9;border-color:#484f58}
.badge.warn{background:rgba(210,153,34,.15);color:var(--amber);border-color:rgba(210,153,34,.35)}
.badge.err{background:rgba(248,81,73,.15);color:var(--red);border-color:rgba(248,81,73,.35)}
.badge.info{background:rgba(88,166,255,.12);color:var(--blue);border-color:rgba(88,166,255,.35)}
.bar{height:.55rem;background:#21262d;border-radius:999px;overflow:hidden;margin-top:.4rem}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#1f6feb,var(--blue));border-radius:999px}
.bar.green>i{background:linear-gradient(90deg,#238636,var(--green))}
.bar.red>i{background:linear-gradient(90deg,#da3633,var(--red))}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th,td{padding:.42rem .35rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.75rem}
tr:last-child td{border-bottom:0}
.num{font-variant-numeric:tabular-nums;font-family:ui-monospace,Menlo,Consolas,monospace}
.pos{color:var(--green)}.neg{color:var(--red)}
.check-ok{color:var(--green)}.check-bad{color:var(--red)}
pre.raw{white-space:pre-wrap;word-break:break-word;background:#0d1117;padding:.85rem;
  border-radius:8px;border:1px solid var(--line);font-size:.8rem;overflow:auto}
details{margin-top:.8rem}
details summary{cursor:pointer;color:var(--muted);font-size:.85rem}
ul.alerts{padding-left:1.1rem;margin:.4rem 0}
ul.alerts li{margin:.28rem 0}
.hero{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;justify-content:space-between}
.hero .action{font-size:1.35rem;font-weight:750}
@media (max-width:640px){
  .kpi .value{font-size:1.05rem}
  th,td{font-size:.8rem}
}
.chart-wrap{width:100%;overflow:hidden}
.chart-wrap svg{width:100%;height:auto;display:block}
.tl{list-style:none;padding:0;margin:0}
.tl li{display:grid;grid-template-columns:6.2rem 1fr;gap:.55rem;padding:.42rem 0;border-bottom:1px solid var(--line)}
.tl li:last-child{border-bottom:0}
.tl .when{color:var(--muted);font-size:.78rem;font-variant-numeric:tabular-nums}
.tl .what{font-size:.88rem}
.tl .why{color:var(--muted);font-size:.78rem;margin-top:.15rem}
.legend{display:flex;flex-wrap:wrap;gap:.55rem;margin:.35rem 0 .2rem;font-size:.75rem;color:var(--muted)}
.legend i{display:inline-block;width:.7rem;height:.25rem;border-radius:2px;margin-right:.25rem;vertical-align:middle}
.legend .l1{background:#58a6ff}.legend .l2{background:#3fb950}.legend .l3{background:#a371f7}
"""


def _read(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_json(path: Path) -> dict[str, Any] | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _pct(x: Any, digits: int = 1) -> str:
    try:
        v = float(x)
    except Exception:
        return "—"
    # 已是百分数 (如 34 表示 34%) 或 0-1
    if abs(v) <= 1.5:
        v *= 100
    return f"{v:.{digits}f}%"


def _money(x: Any) -> str:
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "—"


def _cls_num(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "num"
    if v > 0:
        return "num pos"
    if v < 0:
        return "num neg"
    return "num"


def _page(title: str, body_html: str, active: str = "面板") -> str:
    def link(name: str, href: str) -> str:
        cls = ' class="active"' if name == active else ""
        return f'<a href="{href}"{cls}>{name}</a>'

    nav = " ".join(
        [
            link("面板", "index.html"),
            link("今日", "today.html"),
            link("速览", "brief.html"),
            link("行情", "data.html"),
            link("下一步", "next.html"),
            link("GO", "go.html"),
            link("可判", "ready.html"), link("摘要", "digest.html"), link("EOD", "eod.html"), link("轨迹", "progress.html"), link("脉搏", "pulse.html"),
            link("拉取", "pull.html"),
            link("取证", "asof.html"),
            link("收益", "yield.html"),
            link("对照", "compare.html"),
            link("LIVE", "live.html"),
            link("信号原文", "signal.html"),
            link("状态", "status.html"),
            link("监控", "monitor.html"),
            link("告警", "alerts.html"),
            link("摘要", "summary.html"),
            link("JSON", "latest.json"),
        ]
    )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{_esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>ETF 轮动 · 日更面板</h1>
  <div class="muted">生成于 {_esc(stamp)} · 生产 c01 冻结 · 研究影子 xgn</div>
  <nav>{nav}</nav>
</header>
<main>
{body_html}
</main>
<footer>只读面板 · 非投资建议 · 数据来自本地 output/ 日更产物</footer>
</body>
</html>
"""


def _load_alerts() -> dict:
    mon_path = OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"
    pipe_path = OUTPUT_DIR / "risk_audit" / "pipeline_last.json"
    err_n = warn_n = 0
    items: list[dict] = []
    if mon_path.exists():
        try:
            mon = json.loads(mon_path.read_text(encoding="utf-8"))
            err_n = int(mon.get("alert_error_n") or 0)
            warn_n = int(mon.get("alert_warn_n") or 0)
            for row in mon.get("rows") or []:
                for a in row.get("alerts") or []:
                    items.append(
                        {
                            "shadow": row.get("name"),
                            "level": a.get("level"),
                            "code": a.get("code"),
                            "msg": a.get("msg"),
                        }
                    )
        except Exception:
            pass
    if not items and pipe_path.exists():
        try:
            pipe = json.loads(pipe_path.read_text(encoding="utf-8"))
            err_n = int(pipe.get("alert_error_n") or 0)
            warn_n = int(pipe.get("alert_warn_n") or 0)
            items = list(pipe.get("alerts") or [])
        except Exception:
            pass
    return {"error_n": err_n, "warn_n": warn_n, "items": items}


def _alert_badges(err_n: int, warn_n: int) -> str:
    parts = []
    if err_n:
        parts.append(f'<span class="badge err">error {err_n}</span>')
    if warn_n:
        parts.append(f'<span class="badge warn">warn {warn_n}</span>')
    if not parts:
        parts.append('<span class="badge ok">alerts 0</span>')
    return " ".join(parts)


def _load_nav_series(name: str, limit: int = 120) -> list[dict]:
    """从影子 state 读 nav_history: [{date, nav}]."""
    from etf_rotation.paths import shadow_state_file  # local import ok

    path = shadow_state_file(name)
    data = _load_json(path)
    if not isinstance(data, dict):
        return []
    hist = data.get("nav_history") or []
    out = []
    for p in hist:
        if not isinstance(p, dict):
            continue
        try:
            out.append({"date": str(p.get("date")), "nav": float(p.get("nav"))})
        except Exception:
            continue
    return out[-limit:]


def _load_action_history(limit: int = 30) -> list[dict]:
    """近 N 日动作: 优先 action_history.jsonl, 回退 etf信号_*.txt."""
    import re

    path = OUTPUT_DIR / "action_history.jsonl"
    rows: list[dict] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if isinstance(rec, dict):
                    rows.append(rec)
        except Exception:
            rows = []
    if rows:
        # 同日保留最后一条
        by_day: dict[str, dict] = {}
        for r in rows:
            day = str(r.get("date") or "")[:10]
            if day:
                by_day[day] = r
        ordered = [by_day[k] for k in sorted(by_day.keys())]
        return ordered[-limit:]

    # fallback: parse signal archives
    files = sorted(OUTPUT_DIR.glob("etf信号_*.txt"))
    by_day = {}
    for f in files[-80:]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        day = action = None
        reasons: list[str] = []
        for line in text.splitlines():
            m = re.search(r"(20\d{2}-\d{2}-\d{2})", line)
            if m and ("轮动" in line or "ETF" in line):
                day = m.group(1)
            if "今日动作" in line:
                action = line.split("今日动作")[-1].strip(" :：")
            if line.strip().startswith("·") or line.strip().startswith("•"):
                reasons.append(line.strip(" ·•"))
        if not day:
            # filename stamp 20260721_2056
            m2 = re.search(r"(\d{8})_", f.name)
            if m2:
                s = m2.group(1)
                day = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        if day and action:
            by_day[day] = {
                "date": day,
                "action": action,
                "reasons": reasons[:3],
                "source": f.name,
            }
    ordered = [by_day[k] for k in sorted(by_day.keys())]
    return ordered[-limit:]


def _svg_nav_chart(series_map: dict[str, list[dict]], width: int = 720, height: int = 220) -> str:
    """多序列净值归一化折线 (纯 SVG, 无外部 JS)."""
    colors = ["#58a6ff", "#3fb950", "#a371f7", "#d29922", "#f85149"]
    # union dates
    date_set: set[str] = set()
    cleaned: dict[str, list[tuple[str, float]]] = {}
    for name, pts in series_map.items():
        arr = []
        for p in pts:
            try:
                d = str(p["date"])[:10]
                v = float(p["nav"])
                if v > 0:
                    arr.append((d, v))
                    date_set.add(d)
            except Exception:
                continue
        if len(arr) >= 2:
            cleaned[name] = arr
    if not cleaned:
        return '<p class="muted">暂无净值历史 (影子 warmup 后可见)</p>'

    dates = sorted(date_set)
    pad_l, pad_r, pad_t, pad_b = 36, 12, 12, 28
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b

    # normalize each series to 100 at first point in range
    norms: dict[str, list[tuple[int, float]]] = {}
    all_y: list[float] = []
    date_index = {d: i for i, d in enumerate(dates)}
    for name, arr in cleaned.items():
        base = arr[0][1]
        if base <= 0:
            continue
        seq = []
        for d, v in arr:
            if d not in date_index:
                continue
            y = 100.0 * v / base
            seq.append((date_index[d], y))
            all_y.append(y)
        if len(seq) >= 2:
            norms[name] = seq
    if not norms:
        return '<p class="muted">净值点不足</p>'

    ymin = min(all_y)
    ymax = max(all_y)
    if abs(ymax - ymin) < 1e-9:
        ymin -= 1
        ymax += 1
    # pad 5%
    span = ymax - ymin
    ymin -= span * 0.05
    ymax += span * 0.05

    def xy(i: int, y: float) -> tuple[float, float]:
        x = pad_l + (i / max(1, len(dates) - 1)) * w
        yy = pad_t + (1 - (y - ymin) / (ymax - ymin)) * h
        return x, yy

    polylines = []
    legend = []
    for idx, (name, seq) in enumerate(norms.items()):
        color = colors[idx % len(colors)]
        pts = " ".join(f"{xy(i, y)[0]:.1f},{xy(i, y)[1]:.1f}" for i, y in seq)
        polylines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}" />'
        )
        last_y = seq[-1][1]
        legend.append(
            f'<span><i class="l{idx+1}" style="background:{color}"></i>{_esc(name)} · {last_y:.1f}</span>'
        )

    # axis labels
    y_ticks = []
    for t in range(5):
        val = ymin + (ymax - ymin) * t / 4
        _, yy = xy(0, val)
        y_ticks.append(
            f'<text x="4" y="{yy + 3:.1f}" fill="#8b949e" font-size="10">{val:.0f}</text>'
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" '
            f'stroke="#30363d" stroke-width="1" stroke-dasharray="3,3" />'
        )
    x_labels = []
    for j, di in enumerate([0, len(dates) // 2, len(dates) - 1]):
        if di < 0 or di >= len(dates):
            continue
        x, _ = xy(di, ymin)
        x_labels.append(
            f'<text x="{x:.1f}" y="{height - 8}" fill="#8b949e" font-size="10" text-anchor="middle">{_esc(dates[di][5:])}</text>'
        )

    # baseline 100
    if ymin < 100 < ymax:
        _, y100 = xy(0, 100)
        base_line = (
            f'<line x1="{pad_l}" y1="{y100:.1f}" x2="{width - pad_r}" y2="{y100:.1f}" '
            f'stroke="#6e7681" stroke-width="1" stroke-dasharray="2,4" />'
        )
    else:
        base_line = ""

    svg = f"""
<div class="chart-wrap">
  <div class="legend">{''.join(legend)}</div>
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="影子净值曲线">
    <rect x="0" y="0" width="{width}" height="{height}" fill="#0d1117" rx="8" />
    {''.join(y_ticks)}
    {base_line}
    {''.join(polylines)}
    {''.join(x_labels)}
  </svg>
  <div class="muted">归一化起点=100 · 虚线=基准100 · 数据来自 shadow_states nav_history</div>
</div>
"""
    return svg


def _timeline_html(actions: list[dict], limit: int = 14) -> str:
    if not actions:
        return '<p class="muted">暂无动作历史 (日更写入 action_history.jsonl 后可见)</p>'
    items = []
    for a in actions[-limit:][::-1]:
        day = _esc(str(a.get("date") or "")[:10])
        act = _esc(a.get("action") or "—")
        reasons = a.get("reasons") or []
        why = _esc(" · ".join(str(x) for x in reasons[:2])) if reasons else ""
        mkt = a.get("market_ok")
        badge = ""
        if mkt is True:
            badge = '<span class="badge ok">趋势开</span>'
        elif mkt is False:
            badge = '<span class="badge off">趋势关</span>'
        live_bits = ""
        sl = a.get("signal_live") if isinstance(a.get("signal_live"), dict) else None
        if sl:
            try:
                lr = sl.get("live_return_pct")
                xs = sl.get("live_excess_pct")
                lr_s = f"{float(lr):+.2f}%" if lr is not None else "—"
                xs_s = f"{float(xs):+.2f}%" if xs is not None else "—"
            except Exception:
                lr_s, xs_s = "—", "—"
            thin = sl.get("thin_live")
            if thin is None and sl.get("days_live") is not None:
                try:
                    thin = int(sl.get("days_live")) < 5
                except Exception:
                    thin = False
            tag = " THIN" if thin else ""
            live_bits = f"<div class='why'>live {lr_s}{tag} · xs {xs_s}</div>"
        items.append(
            f"<li><div class='when'>{day}</div><div class='what'>{badge}{act}"
            f"{f'<div class=why>{why}</div>' if why else ''}{live_bits}</div></li>"
        )
    return f"<ul class='tl'>{''.join(items)}</ul>"


def _kpi(label: str, value: str, sub: str = "", tone: str = "") -> str:
    vcls = f"value {tone}".strip()
    return (
        f'<div class="card kpi"><div class="label">{_esc(label)}</div>'
        f'<div class="{vcls}">{value}</div>'
        f'<div class="sub">{_esc(sub)}</div></div>'
    )


def _bar(pct: float, kind: str = "") -> str:
    p = max(0.0, min(100.0, float(pct)))
    cls = f"bar {kind}".strip()
    return f'<div class="{cls}"><i style="width:{p:.1f}%"></i></div>'


def _dashboard(
    latest: dict | None,
    mon: dict | None,
    status: dict | None,
    pipeline: dict | None,
    alerts: dict,
    latest_txt: str,
) -> str:
    ab = _alert_badges(alerts["error_n"], alerts["warn_n"])
    action = (latest or {}).get("action") or "—"
    market_ok = bool((latest or {}).get("market_ok"))
    frozen = bool((latest or {}).get("frozen"))
    cfg = (latest or {}).get("config") or "c01"
    when = (latest or {}).get("time") or "—"
    breadth = (latest or {}).get("breadth")
    ret = (latest or {}).get("return_pct")
    tv = (latest or {}).get("total_value")
    holding = (latest or {}).get("holding")
    hold_name = "空仓"
    if isinstance(holding, dict):
        hold_name = holding.get("name") or holding.get("code") or "持仓中"
    elif holding:
        hold_name = str(holding)

    trend_badge = (
        '<span class="badge ok">趋势开</span>'
        if market_ok
        else '<span class="badge off">趋势关</span>'
    )
    freeze_badge = (
        '<span class="badge info">生产冻结</span>'
        if frozen
        else '<span class="badge warn">未冻结</span>'
    )
    reasons = (latest or {}).get("reasons") or []
    reason_html = (
        "<ul class='alerts'>"
        + "".join(f"<li>{_esc(r)}</li>" for r in reasons[:8])
        + "</ul>"
        if reasons
        else '<p class="muted">无额外原因</p>'
    )

    # checks
    checks = (latest or {}).get("checks") or []
    check_rows = []
    for c in checks:
        ok = bool(c.get("ok"))
        mark = "✓" if ok else "✗"
        cls = "check-ok" if ok else "check-bad"
        check_rows.append(
            f"<tr><td class='{cls} num'>{mark}</td>"
            f"<td>{_esc(c.get('name') or c.get('id'))}</td>"
            f"<td class='muted'>{_esc(c.get('detail') or c.get('msg') or '')}</td></tr>"
        )
    checks_table = (
        "<table><thead><tr><th></th><th>检查项</th><th>说明</th></tr></thead>"
        f"<tbody>{''.join(check_rows) or '<tr><td colspan=3 class=muted>无检查清单</td></tr>'}</tbody></table>"
    )

    # top picks
    top = (latest or {}).get("top3") or []
    top_rows = []
    for i, t in enumerate(top, 1):
        sc = t.get("score")
        try:
            scs = f"{float(sc):+.2f}"
        except Exception:
            scs = "—"
        top_rows.append(
            f"<tr><td class='num'>{i}</td><td>{_esc(t.get('name') or t.get('code'))}</td>"
            f"<td class='muted num'>{_esc(t.get('code'))}</td>"
            f"<td class='{_cls_num(sc)}'>{scs}</td></tr>"
        )
    top_table = (
        "<table><thead><tr><th>#</th><th>名称</th><th>代码</th><th>得分</th></tr></thead>"
        f"<tbody>{''.join(top_rows) or '<tr><td colspan=4 class=muted>无 TOP</td></tr>'}</tbody></table>"
    )

    # monitor cards
    mon_rows = (mon or {}).get("rows") or []
    mon_cards = []
    for r in mon_rows:
        te = r.get("target_exposure")
        try:
            te_pct = (
                float(te) * 100
                if te is not None and abs(float(te)) <= 1.5
                else float(te or 0)
            )
        except Exception:
            te_pct = 0.0
        ae = int(r.get("alert_error_n") or 0)
        aw = int(r.get("alert_warn_n") or 0)
        badge = (
            f'<span class="badge err">E{ae}</span>'
            if ae
            else (
                f'<span class="badge warn">W{aw}</span>'
                if aw
                else '<span class="badge ok">OK</span>'
            )
        )
        mon_cards.append(
            f"""
<div class="card">
  <div class="pillrow"><b>{_esc(r.get('name'))}</b> {badge}
    <span class="badge info">vol:{_esc(r.get('vol_src'))}</span>
    <span class="badge off">regime:{_esc(r.get('regime') or '—')}</span>
  </div>
  <div class="grid kpis" style="margin-top:.55rem">
    {_kpi('目标暴露', f'{te_pct:.1f}%', f"vol_scale={r.get('vol_scale')}")}
    {_kpi('账户', _money(r.get('total_value')), f"全样本 {r.get('return_pct')}%")}
    {_kpi('live%', (f"{float(r.get('live_return_pct')):+.2f}%" if r.get('live_return_pct') is not None else '—'), f"from {r.get('live_start') or '—'}")}
    {_kpi('持仓', _esc(r.get('holdings') or '空仓'), f"rets={r.get('n_port_rets')}")}
  </div>
  {_bar(te_pct, 'green' if te_pct > 0 else '')}
</div>"""
        )
    mon_html = "".join(mon_cards) or '<div class="card muted">暂无影子监控 JSON</div>'

    # status strip
    td = (status or {}).get("trading_day") or {}
    td_ok = td.get("is_trading_day")
    if td_ok:
        td_badge = '<span class="badge ok">交易日</span>'
    elif td_ok is False:
        td_badge = '<span class="badge off">非交易日</span>'
    else:
        td_badge = '<span class="badge off">交易日未知</span>'

    pipe_ok = (pipeline or {}).get("ok")
    if pipe_ok:
        pipe_badge = '<span class="badge ok">pipeline OK</span>'
    elif pipe_ok is False:
        pipe_badge = '<span class="badge err">pipeline FAIL</span>'
    else:
        pipe_badge = '<span class="badge off">pipeline —</span>'

    mon_ok = (mon or {}).get("ok")
    if mon_ok:
        mon_badge = '<span class="badge ok">monitor OK</span>'
    elif mon_ok is False:
        mon_badge = '<span class="badge err">monitor FAIL</span>'
    else:
        mon_badge = '<span class="badge off">monitor —</span>'

    # shadow from latest (structured)
    sh = (latest or {}).get("shadow") or {}
    sh_action = sh.get("action") or "—"
    sh_cfg = sh.get("config") or "研究影子"
    sh_state = sh.get("state") if isinstance(sh.get("state"), dict) else {}
    sh_live = sh_state.get("live") if isinstance(sh_state.get("live"), dict) else {}
    if not sh_live and isinstance(sh.get("live"), dict):
        sh_live = sh.get("live") or {}
    sh_live_pct = sh_live.get("return_pct")
    if sh_live_pct is None:
        sh_live_pct = sh.get("live_return_pct")
    # 超额/薄样本: 优先 latest.signal_live, 再 shadow_live.json
    sh_xs = sh.get("live_excess_pct")
    sh_bench_ret = sh.get("bench_return_pct")
    sh_days = None
    sh_thin = False
    sl = (latest or {}).get("signal_live") if isinstance((latest or {}).get("signal_live"), dict) else None
    if sl:
        if sh_live_pct is None:
            sh_live_pct = sl.get("live_return_pct")
        if sh_xs is None:
            sh_xs = sl.get("live_excess_pct")
        if sh_bench_ret is None:
            sh_bench_ret = sl.get("bench_return_pct")
        sh_days = sl.get("days_live")
        if sh_days is None:
            sh_days = sl.get("live_n_rets")
        sh_thin = bool(
            sl.get("thin_live")
            or (sh_days is not None and int(sh_days) < 5)
        )
    if sh_days is None or sh_xs is None or sh_live_pct is None or not sh_thin:
        try:
            live_rows = _load_json(OUTPUT_DIR / "risk_audit" / "shadow_live.json")
            if isinstance(live_rows, list):
                for rr in live_rows:
                    if rr.get("signal") or rr.get("is_signal_default"):
                        if sh_live_pct is None:
                            sh_live_pct = rr.get("live_return_pct")
                        if sh_xs is None:
                            sh_xs = rr.get("live_excess_pct")
                        if sh_bench_ret is None:
                            sh_bench_ret = rr.get("bench_return_pct")
                        if sh_days is None:
                            sh_days = rr.get("days_live")
                            if sh_days is None:
                                sh_days = rr.get("live_n_rets")
                        if not sh_thin:
                            sh_thin = bool(
                                rr.get("thin_live")
                                or (sh_days is not None and int(sh_days) < 5)
                            )
                        break
        except Exception:
            pass
    try:
        sh_live_s = f"{float(sh_live_pct):+.2f}%" if sh_live_pct is not None else "—"
    except Exception:
        sh_live_s = "—"
    if sh_thin:
        sh_live_s = f"{sh_live_s} THIN"
    try:
        sh_xs_s = f"{float(sh_xs):+.2f}%" if sh_xs is not None else "—"
    except Exception:
        sh_xs_s = "—"
    sh_rets = sh_state.get("n_port_rets")
    if sh_rets is None and isinstance(sh_state.get("port_rets"), list):
        sh_rets = len(sh_state.get("port_rets") or [])
    sh_exp = None
    dec = sh.get("decision") or {}
    if isinstance(dec, dict):
        exp = dec.get("exposure") or {}
        if isinstance(exp, dict):
            sh_exp = exp.get("target_exposure")
        if sh_exp is None:
            sh_exp = dec.get("target_exposure")
    try:
        if sh_exp is not None and abs(float(sh_exp)) <= 1.5:
            sh_exp_pct = float(sh_exp) * 100
        else:
            sh_exp_pct = float(sh_exp or 0)
    except Exception:
        sh_exp_pct = 0.0

    br_pct = 0.0
    try:
        if breadth is not None and abs(float(breadth)) <= 1.5:
            br_pct = float(breadth) * 100
        else:
            br_pct = float(breadth or 0)
    except Exception:
        br_pct = 0.0

    # NAV chart + action timeline
    try:
        from etf_rotation.research_mainline import MONITOR_SHADOWS as _nav_names

        mainline = list(_nav_names)
    except Exception:
        mainline = [
            "c01_q10_vt08_soft_oh38",
            "c01_q10_vt08_soft_oh38_xgn",
            "c01_q10_vt09_oh35",
            "c01_q10_vt11",
        ]
    series_map = {n: _load_nav_series(n) for n in mainline}
    series_map = {k: v for k, v in series_map.items() if v}
    nav_chart = _svg_nav_chart(series_map)
    actions = _load_action_history(30)
    timeline = _timeline_html(actions, limit=14)

    # latest 过旧 / 行情滞后横幅
    stale_banner = ""
    try:
        td_date = str((td or {}).get("date") or "")[:10]
        when_day = str(when or "")[:10]
        data_asof = str((td or {}).get("data_asof") or (latest or {}).get("market_asof") or "")[:10]
        data_lag = bool((td or {}).get("data_lag")) or (
            bool(data_asof and td_date and data_asof < td_date)
        )
        stale = bool((status or {}).get("latest_stale"))
        if not stale and (td or {}).get("is_trading_day") and td_date and when_day and when_day < td_date:
            stale = True
        notes = []
        if data_lag:
            notes.append(
                f"<b>⚠ 行情滞后 DATA_LAG</b> · 行情截至 {_esc(data_asof)} · wall/交易日 {_esc(td_date)} "
                f"(nav/live 以 asof 为准) · <code>./etf asof</code>"
            )
        if stale:
            notes.append(
                f"<b>⚠ latest 过旧</b> · 信号时间 {_esc(when)} · 交易日 {_esc(td_date)} · "
                f"<code>./etf refresh</code>"
            )
        if notes:
            stale_banner = (
                "<div class='card' style='border-color:var(--amber);margin-bottom:.75rem'>"
                + "<br>".join(notes)
                + "</div>"
            )
    except Exception:
        stale_banner = ""

    # data decision + ready level badges
    decision_badge = ""
    ready_badge = ""
    try:
        dobj = _load_json(OUTPUT_DIR / "risk_audit" / "data_status.json")
        if isinstance(dobj, dict) and dobj.get("decision"):
            dec = str(dobj.get("decision"))
            if dec == "wait_data":
                decision_badge = '<span class="badge warn">WAIT_DATA</span>'
            elif dec == "refresh":
                decision_badge = '<span class="badge warn">STALE→refresh</span>'
            elif dec == "ok":
                decision_badge = '<span class="badge ok">DATA_OK</span>'
            else:
                decision_badge = f'<span class="badge off">{_esc(dec)}</span>'
    except Exception:
        decision_badge = ""
    try:
        robj = _load_json(OUTPUT_DIR / "risk_audit" / "ready.json")
        if isinstance(robj, dict) and robj.get("level"):
            lv = str(robj.get("level"))
            if lv == "READY":
                ready_badge = '<span class="badge ok">READY</span>'
            elif lv in ("NOT_READY", "WAIT_DATA"):
                ready_badge = f'<span class="badge warn">{_esc(lv)}</span>'
            elif lv == "THIN":
                ready_badge = '<span class="badge warn">THIN</span>'
            else:
                ready_badge = f'<span class="badge off">{_esc(lv)}</span>'
    except Exception:
        ready_badge = ""

    # digest 推荐行 (真实有效收益结论入口)
    digest_line = ""
    progress_line = ""
    pulse_line = ""
    try:
        dg = _load_json(OUTPUT_DIR / "risk_audit" / "digest.json")
        if isinstance(dg, dict) and (dg.get("level") or dg.get("recommend")):
            lv = dg.get("level") or "—"
            rec = dg.get("recommend") or "./etf go"
            if isinstance(rec, str):
                rec = rec.replace("python3 scripts/etf.py ", "./etf ")
            lr, xs, dl = dg.get("live_return_pct"), dg.get("live_excess_pct"), dg.get("days_live")
            dtr = dg.get("days_to_ready")
            if dtr is None and dl is not None:
                try:
                    dtr = max(0, 5 - int(dl))
                except Exception:
                    dtr = None
            try:
                lr_s = f"{float(lr):+.2f}%" if lr is not None else "—"
            except Exception:
                lr_s = "—"
            try:
                xs_s = f"{float(xs):+.2f}%" if xs is not None else "—"
            except Exception:
                xs_s = "—"
            dtr_s = f" · 距可判={dtr}日" if dtr is not None else ""
            eta = dg.get("eta_note") or ""
            if not eta and dtr is not None:
                try:
                    di = int(dtr)
                    if di <= 0:
                        eta = "样本已够"
                    else:
                        eta = f"约再{di}个交易日READY"
                except Exception:
                    eta = ""
            eta_s = f" · ETA {_esc(eta)}" if eta else ""
            digest_line = (
                "<div class='card' style='margin:.55rem 0 .2rem;padding:.55rem .7rem'>"
                f"<b>DIGEST</b> · 可判性 <code>{_esc(lv)}</code> · "
                f"live {_esc(lr_s)} · xs {_esc(xs_s)} · Lrets={_esc(dl if dl is not None else '—')}"
                f"{_esc(dtr_s)}{eta_s} · "
                f"推荐 <code>{_esc(rec)}</code> · "
                "<a href='digest.html'>摘要</a> · <a href='ready.html'>可判性</a> · "
                "<a href='progress.html'>轨迹</a> · <a href='eod.html'>EOD</a>"
                "</div>"
            )
            # progress spark from progress_latest
            try:
                _pp = OUTPUT_DIR / "risk_audit" / "progress_latest.json"
                if _pp.exists():
                    _pj = json.loads(_pp.read_text(encoding="utf-8"))
                    if isinstance(_pj, dict):
                        _plv = _pj.get("level") or "—"
                        _pdl = _pj.get("days_live")
                        _pdtr = _pj.get("days_to_ready")
                        _peta = ""
                        if _pdtr is not None:
                            try:
                                _di = int(_pdtr)
                                if _di <= 0:
                                    _peta = "样本已够"
                                else:
                                    _peta = f"约再{_di}日READY"
                                    if _pj.get("data_lag"):
                                        _peta += "(需asof)"
                            except Exception:
                                _peta = ""
                        _peta_s = f" · ETA {_esc(_peta)}" if _peta else ""
                        progress_line = (
                            "<div class='card' style='margin:.35rem 0 .2rem;padding:.45rem .7rem'>"
                            f"<b>PROGRESS</b> · <code>{_esc(_plv)}</code> · "
                            f"Lrets={_esc(_pdl if _pdl is not None else '—')} · "
                            f"dtr={_esc(_pdtr if _pdtr is not None else '—')}"
                            f"{_peta_s} · "
                            f"asof={_esc(_pj.get('market_asof') or '—')} · "
                            "<a href='progress.html'>轨迹</a> · <a href='ready.html'>可判性</a>"
                            "</div>"
                        )
            except Exception:
                pass

            # pulse card
            try:
                _pul = OUTPUT_DIR / "risk_audit" / "pulse.json"
                if _pul.exists():
                    _pu = json.loads(_pul.read_text(encoding="utf-8"))
                    if isinstance(_pu, dict):
                        _plv = _pu.get("level") or "—"
                        _pdtr = _pu.get("days_to_ready")
                        _peta = _pu.get("eta_note") or ""
                        _pact = _pu.get("next_action") or "—"
                        _pread = _pu.get("readable_yield")
                        pulse_line = (
                            "<div class='card' style='margin:.35rem 0 .2rem;padding:.45rem .7rem'>"
                            f"<b>PULSE</b> · <code>{_esc(_plv)}</code> · "
                            f"dtr={_esc(_pdtr if _pdtr is not None else '—')} · "
                            f"action={_esc(_pact)} · readable={_esc(_pread)} · "
                            f"ETA {_esc(_peta or '—')} · "
                            f"<a href='pulse.html'>脉搏</a> · <code>./etf do</code>"
                            "</div>"
                        )
            except Exception:
                pass
    except Exception:
        digest_line = ""

    body = f"""
{stale_banner}<div class="card hero">
  <div>
    <div class="pillrow">{trend_badge} {freeze_badge} {td_badge} {pipe_badge} {mon_badge} {decision_badge} {ready_badge} {ab}</div>
    <div class="action" style="margin-top:.45rem">{_esc(action)}</div>
    <div class="muted">{_esc(cfg)} · {_esc(when)}</div>
  </div>
    <div class="muted">生产只读 · 研究影子不交易 · live%才是日更有效收益 · THIN=样本&lt;5日 · DATA_LAG=行情未到 · <a href="digest.html">摘要</a> · <a href="ready.html">可判性</a> · <a href="yield.html">有效收益</a> · <code>./etf digest</code> · <code>./etf go --timeout 600</code> · <code>./etf eod</code> · <code>./etf ready</code> · <code>./etf open --launch site</code></div>
</div>
{digest_line}{progress_line}{pulse_line}
<div class="grid kpis" style="margin-top:.75rem">
  {_kpi('持仓', _esc(hold_name), '生产模拟账户')}
  {_kpi('总资产', _money(tv), f"收益 {ret if ret is not None else '—'}%")}
  {_kpi('影子 live%', sh_live_s, _esc(sh_cfg) + ('' if sh_days is None else f' · Lrets={sh_days}') + (' · 仅锚日' if sh_days == 0 else '') + (' · DATA_LAG' if (latest or {}).get('market_asof') and str((td or {}).get('date') or '')[:10] > str((latest or {}).get('market_asof') or '')[:10] else ''), 'green' if (sh_live_pct or 0) > 0 else '')}
  {_kpi('超额 xs%', sh_xs_s, f"vs 基准 {'' if sh_bench_ret is None else f'{float(sh_bench_ret):+.2f}%'}", 'green' if (sh_xs or 0) > 0 else '')}
</div>
<div style="margin:.35rem 0 .9rem">{_bar(br_pct)}</div>

<div class="grid two">
  <div class="card stack">
    <h2>今日动作</h2>
    {reason_html}
    <div class="muted">调仓时钟 days_to_rebalance={_esc((latest or {}).get('days_to_rebalance'))}</div>
  </div>
  <div class="card stack">
    <h2>研究影子</h2>
    <div><b>{_esc(sh_action)}</b></div>
    <div class="muted">账户 {_money(sh_state.get('total_value'))} · live {sh_live_s} · xs {sh_xs_s} · 全样本 {_esc(sh_state.get('return_pct'))}% · rets={_esc(sh_rets)}</div>
    {_bar(sh_exp_pct, 'green' if sh_exp_pct > 0 else '')}
    <div class="muted">path: {_esc(Path(str(sh.get('state_path') or '')).name or '—')}</div>
  </div>
</div>

<div class="grid two" style="margin-top:.75rem">
  <div class="card">
    <h2>影子净值 (归一化)</h2>
    {nav_chart}
  </div>
  <div class="card">
    <h2>近 N 日动作</h2>
    {timeline}
  </div>
</div>

<div class="grid two" style="margin-top:.75rem">
  <div class="card">
    <h2>决策检查清单</h2>
    {checks_table}
  </div>
  <div class="card">
    <h2>主策略 TOP</h2>
    {top_table}
  </div>
</div>

<div style="margin-top:.9rem">
  <h2 style="font-size:1rem;margin:0 0 .5rem">影子监控</h2>
  <div class="stack">{mon_html}</div>
  <p class="muted">{_esc((mon or {}).get('bench') or '')}</p>
</div>

<details>
  <summary>展开信号原文 (latest.txt)</summary>
  <pre class="raw">{_esc(latest_txt) if latest_txt else '暂无 latest.txt'}</pre>
</details>
"""
    return body


def _lag_banner_html(latest_obj: dict | None = None) -> str:
    """DATA_LAG 黄条 (次级页复用)."""
    try:
        from etf_rotation.calendar_util import resolve_trading_day
        td = resolve_trading_day()
        asof = td.get("data_asof")
        lag = bool(td.get("data_lag"))
        if isinstance(latest_obj, dict):
            asof = latest_obj.get("market_asof") or asof
            sl = latest_obj.get("signal_live") if isinstance(latest_obj.get("signal_live"), dict) else {}
            if sl.get("data_lag") is not None:
                lag = bool(sl.get("data_lag"))
            if sl.get("market_asof"):
                asof = sl.get("market_asof")
        if lag or (asof and str(td.get("date") or "")[:10] > str(asof)[:10]):
            return (
                f"<p class='muted' style='color:var(--amber)'>⚠ DATA_LAG: 行情截至 {_esc(asof)} · "
                f"wall/交易日 {_esc(td.get('date'))} · <code>./etf asof</code></p>"
            )
    except Exception:
        pass
    return ""


def build(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_txt = _read(LATEST_TXT)
    monitor_txt = _read(OUTPUT_DIR / "shadow_monitor.txt")
    summary = _read(OUTPUT_DIR / "shadow_summary.txt")
    latest_obj = _load_json(LATEST_JSON)
    if not isinstance(latest_obj, dict):
        latest_obj = None
    mon_obj = _load_json(OUTPUT_DIR / "risk_audit" / "shadow_monitor.json")
    if not isinstance(mon_obj, dict):
        mon_obj = None
    status_obj = _load_json(OUTPUT_DIR / "risk_audit" / "research_status.json")
    if not isinstance(status_obj, dict):
        status_obj = None
    pipeline_obj = _load_json(OUTPUT_DIR / "risk_audit" / "pipeline_last.json")
    if not isinstance(pipeline_obj, dict):
        pipeline_obj = None
    latest_json = _read(LATEST_JSON)
    pipeline = _read(OUTPUT_DIR / "risk_audit" / "pipeline_last.json")
    alerts = _load_alerts()
    ab = _alert_badges(alerts["error_n"], alerts["warn_n"])

    # index = dashboard
    body = _dashboard(latest_obj, mon_obj, status_obj, pipeline_obj, alerts, latest_txt)
    (out_dir / "index.html").write_text(_page("ETF日更面板", body, "面板"), encoding="utf-8")
    if latest_txt:
        (out_dir / "index.txt").write_text(latest_txt, encoding="utf-8")

    # signal raw page (高亮有效收益块)
    if latest_txt:
        mark = "-------- 有效收益 (SIGNAL live) --------"
        if mark in latest_txt:
            pre, rest = latest_txt.split(mark, 1)
            end = "----------------------------------------"
            if end in rest:
                mid, post = rest.split(end, 1)
                block = mark + mid + end
            else:
                block = mark + rest
                post = ""
            sbody = (
                f"<p>{ab}</p>"
                f"<div class='card'><h2>有效收益 (SIGNAL live)</h2>"
                f"<pre class='raw'>{_esc(block)}</pre>"
                f"<p class='muted'>live%=暖机末→今 · xs%=live−基准 · THIN=样本&lt;5日</p></div>"
                f"<details open><summary>信号原文全文</summary>"
                f"<pre class='raw'>{_esc(pre + post)}</pre></details>"
            )
        else:
            sbody = (
                f"<p>{ab}</p><h2>latest 信号原文</h2>"
                f"<pre class='raw'>{_esc(latest_txt)}</pre>"
                f"<p class='muted'>无有效收益块 → <code>./etf live</code></p>"
            )
    else:
        sbody = f"<p>{ab}</p><p>暂无信号 (output/latest.txt 缺失)</p>"
    (out_dir / "signal.html").write_text(_page("信号原文", sbody, "信号原文"), encoding="utf-8")

    # compare page
    cmp_txt = _read(OUTPUT_DIR / "shadow_compare.txt")
    cmp_json = _load_json(OUTPUT_DIR / "risk_audit" / "shadow_compare.json")
    if isinstance(cmp_json, list) and cmp_json:
        rows_html = []
        for r in cmp_json:
            if not r.get("exists"):
                rows_html.append(
                    f"<tr><td>{_esc(r.get('name'))}</td><td colspan='10' class='muted'>MISSING</td></tr>"
                )
                continue
            sh = r.get("bt_sharpe")
            dd = r.get("bt_dd")
            c3 = r.get("cost_3bp_sharpe")
            lr = r.get("live_return_pct")
            xs = r.get("live_excess_pct")
            try:
                lr_s = f"{float(lr):+.2f}%" if lr is not None else ""
            except Exception:
                lr_s = ""
            if r.get("thin_live") or (
                r.get("days_live") is not None and int(r.get("days_live") or 0) < 5
            ):
                if lr_s:
                    lr_s = f"{lr_s} THIN"
            try:
                xs_s = f"{float(xs):+.2f}%" if xs is not None else ""
            except Exception:
                xs_s = ""
            gate = "OK" if r.get("gate_ok") else ("FAIL" if r.get("gate") else "—")
            mark = "SIGNAL" if (r.get("is_signal_default") or r.get("signal")) else ""
            rows_html.append(
                f"<tr>"
                f"<td>{_esc(r.get('name'))} <span class='muted'>{mark}</span></td>"
                f"<td class='num'>{'' if sh is None else f'{float(sh):.2f}'}</td>"
                f"<td class='num'>{'' if dd is None else f'{float(dd)*100:.1f}%'}</td>"
                f"<td class='num'>{_money(r.get('total_value'))}</td>"
                f"<td class='num'>{_esc(lr_s)}</td>"
                f"<td class='num'>{_esc(xs_s)}</td>"
                f"<td>{_esc(r.get('holdings') or '空仓')}</td>"
                f"<td class='num'>{_esc(r.get('days_live') if r.get('days_live') is not None else r.get('n_port_rets'))}</td>"
                f"<td>{gate}</td>"
                f"<td class='num'>{'' if c3 is None else f'{float(c3):.2f}'}</td>"
                f"<td class='muted'>{_esc(r.get('last_trade') or '')}</td>"
                f"</tr>"
            )
        cbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}<div class='card'><h2>主线影子对照</h2>"
            "<table><thead><tr><th>策略</th><th>sh</th><th>dd</th><th>净值</th>"
            "<th>live%</th><th>xs%</th><th>持仓</th><th>Lrets</th><th>gate</th><th>3bp</th><th>最近成交</th></tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table>"
            f"<p class='muted'>sh/dd=暖机 · live%/xs%=暖机末→今 (xs=live−基准) · Lrets=live样本天数 · THIN=&lt;5日 · Lrets=0=仅锚日/DATA_LAG</p></div>"
        )
        if cmp_txt:
            cbody += f"<details><summary>对照原文</summary><pre class='raw'>{_esc(cmp_txt)}</pre></details>"
    elif cmp_txt:
        cbody = f"<p>{ab}</p>{_lag_banner_html(latest_obj)}<h2>主线对照</h2><pre class='raw'>{_esc(cmp_txt)}</pre>"
    else:
        cbody = (
            f"<p>{ab}</p><div class='card'><p>暂无对照 · "
            f"<code>./etf compare</code></p></div>"
        )
    (out_dir / "compare.html").write_text(_page("主线对照", cbody, "对照"), encoding="utf-8")
    if cmp_txt:
        (out_dir / "compare.txt").write_text(cmp_txt, encoding="utf-8")

    # live page
    live_txt = _read(OUTPUT_DIR / "shadow_live.txt")
    live_json = _load_json(OUTPUT_DIR / "risk_audit" / "shadow_live.json")
    if isinstance(live_json, list) and live_json:
        lrows = []
        for r in live_json:
            if not r.get("exists"):
                lrows.append(
                    f"<tr><td>{_esc(r.get('name'))}</td><td colspan='8' class='muted'>MISSING</td></tr>"
                )
                continue
            lr = r.get("live_return_pct")
            br = r.get("bench_return_pct")
            xs = r.get("live_excess_pct")
            mark = "SIGNAL" if r.get("signal") else ""
            thin = (
                " THIN"
                if (
                    r.get("thin_live")
                    or int(r.get("days_live") or r.get("live_n_rets") or 0) < 5
                )
                else ""
            )
            lrows.append(
                f"<tr>"
                f"<td>{_esc(r.get('name'))} <span class='muted'>{mark}{thin}</span></td>"
                f"<td class='num'>{'' if lr is None else f'{float(lr):+.3f}%'}</td>"
                f"<td class='num'>{'' if br is None else f'{float(br):+.3f}%'}</td>"
                f"<td class='num'>{'' if xs is None else f'{float(xs):+.3f}%'}</td>"
                f"<td class='num'>{_money(r.get('total_value'))}</td>"
                f"<td>{_esc(r.get('holdings') or '空仓')}</td>"
                f"<td class='num'>{_esc(r.get('days_live') if r.get('days_live') is not None else r.get('live_n_rets'))}</td>"
                f"<td class='muted'>{_esc(r.get('live_start') or '')} {_esc(r.get('last_trade') or '')}</td>"
                f"</tr>"
            )
        # DATA_LAG / Lrets 说明
        lag_note = ""
        try:
            asof = (latest_obj or {}).get("market_asof") if isinstance(latest_obj, dict) else None
            # status/trading day may not be in scope; use latest + resolve if needed
            from etf_rotation.calendar_util import resolve_trading_day as _rtd
            _td = _rtd()
            asof = asof or _td.get("data_asof")
            if _td.get("data_lag") or (asof and str(_td.get("date") or "")[:10] > str(asof)[:10]):
                lag_note = (
                    f"<p class='muted' style='color:var(--amber)'>⚠ DATA_LAG: 行情截至 {_esc(asof)} · "
                    f"wall/交易日 {_esc(_td.get('date'))} · Lrets=0 常为仅锚日, 等数据更新后再判 xs</p>"
                )
        except Exception:
            lag_note = ""
        lbody = (
            f"<p>{ab}</p>{lag_note}<div class='card'><h2>主线 LIVE 收益</h2>"
            "<p class='muted'>live% = 暖机末→今 · xs% = live−基准 · THIN=样本&lt;5日 · "
            "Lrets=0=仅锚日 (DATA_LAG/刚暖机常见)</p>"
            "<table><thead><tr><th>策略</th><th>live%</th><th>bench%</th><th>xs%</th><th>净值</th>"
            "<th>持仓</th><th>Lrets</th><th>备注</th></tr></thead>"
            f"<tbody>{''.join(lrows)}</tbody></table>"
            f"<p class='muted'><code>./etf live</code></p></div>"
        )
        if live_txt:
            lbody += f"<details><summary>LIVE 原文</summary><pre class='raw'>{_esc(live_txt)}</pre></details>"
    elif live_txt:
        lbody = f"<p>{ab}</p><h2>主线 LIVE</h2><pre class='raw'>{_esc(live_txt)}</pre>"
    else:
        lbody = (
            f"<p>{ab}</p><div class='card'><p>暂无 LIVE · "
            f"<code>./etf live</code></p></div>"
        )
    (out_dir / "live.html").write_text(_page("主线 LIVE", lbody, "LIVE"), encoding="utf-8")
    if live_txt:
        (out_dir / "live.txt").write_text(live_txt, encoding="utf-8")

    # monitor
    if mon_obj and mon_obj.get("rows"):
        rows_html = []
        for r in mon_obj["rows"]:
            lr = r.get("live_return_pct")
            lr_s = "" if lr is None else f"{float(lr):+.2f}%"
            if r.get("thin_live") or (
                r.get("days_live") is not None and int(r.get("days_live") or 0) < 5
            ):
                if lr_s:
                    lr_s = f"{lr_s} THIN"
            xs = r.get("live_excess_pct")
            xs_s = "" if xs is None else f"{float(xs):+.2f}%"
            rows_html.append(
                f"<tr><td>{_esc(r.get('name'))}</td>"
                f"<td class='num'>{_esc(r.get('target_exposure'))}</td>"
                f"<td>{_esc(r.get('vol_src'))}</td>"
                f"<td class='num'>{_money(r.get('total_value'))}</td>"
                f"<td class='num'>{_esc(lr_s)}</td>"
                f"<td class='num'>{_esc(xs_s)}</td>"
                f"<td class='{_cls_num(r.get('return_pct'))}'>{_esc(r.get('return_pct'))}%</td>"
                f"<td>{_esc(r.get('holdings'))}</td>"
                f"<td class='num'>{_esc(r.get('n_port_rets'))}</td>"
                f"<td>E{_esc(r.get('alert_error_n'))}/W{_esc(r.get('alert_warn_n'))}</td></tr>"
            )
        mbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}<div class='card'><h2>影子监控表</h2>"
            f"<p class='muted'>{_esc(mon_obj.get('bench'))} · THIN=live 样本&lt;5日 · Lrets=0=仅锚日/DATA_LAG</p>"
            "<table><thead><tr><th>影子</th><th>暴露</th><th>vol</th><th>资产</th>"
            "<th>live%</th><th>xs%</th><th>全样本</th><th>持仓</th><th>rets</th><th>告警</th></tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody></table></div>"
        )
        if monitor_txt:
            mbody += f"<details><summary>监控原文</summary><pre class='raw'>{_esc(monitor_txt)}</pre></details>"
    elif monitor_txt:
        mbody = f"<p>{ab}</p>{_lag_banner_html(latest_obj)}<h2>影子监控</h2><pre class='raw'>{_esc(monitor_txt)}</pre>"
    else:
        mbody = f"<p>{ab}</p><p>暂无 monitor (先跑 shadow_monitor / pipeline)</p>"
    (out_dir / "monitor.html").write_text(_page("影子监控", mbody, "监控"), encoding="utf-8")
    if monitor_txt:
        (out_dir / "monitor.txt").write_text(monitor_txt, encoding="utf-8")

    # alerts
    if alerts["items"]:
        lis = []
        for a in alerts["items"]:
            lis.append(
                f"<li><b>{_esc(a.get('level'))}</b> [{_esc(a.get('code'))}] "
                f"{_esc(a.get('shadow'))}: {_esc(a.get('msg'))}</li>"
            )
        abody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}<div class='card'><h2>告警列表</h2>"
            f"<ul class='alerts'>{''.join(lis)}</ul>"
            f"<p class='muted'>来源: shadow_monitor.json / pipeline_last.json · DATA_LAG 不是告警</p></div>"
        )
    else:
        abody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>告警列表</h2><p>当前无告警</p></div>"
        )
    (out_dir / "alerts.html").write_text(_page("告警", abody, "告警"), encoding="utf-8")
    (out_dir / "alerts.json").write_text(
        json.dumps(alerts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # summary
    if summary:
        sbody = (
            f"{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>影子仓位摘要</h2>"
            f"<p class='muted'>THIN=样本&lt;5日 · Lrets=0=仅锚日 · DATA_LAG=行情未到</p>"
            f"<pre class='raw'>{_esc(summary)}</pre></div>"
        )
    else:
        sbody = f"{_lag_banner_html(latest_obj)}<div class='card'><p>暂无 summary</p></div>"
    (out_dir / "summary.html").write_text(_page("影子摘要", sbody, "摘要"), encoding="utf-8")
    if summary:
        (out_dir / "summary.txt").write_text(summary, encoding="utf-8")

    # status
    status_txt = _read(OUTPUT_DIR / "research_status.txt")
    status_json = _read(OUTPUT_DIR / "risk_audit" / "research_status.json")
    if status_obj:
        cfgs = status_obj.get("configs") or []
        shs = status_obj.get("shadows") or []
        cfg_rows = "".join(
            f"<tr><td>{_esc(c.get('name'))}</td><td>{_esc(c.get('frozen'))}</td>"
            f"<td>{_esc(c.get('research'))}</td><td class='num'>{_esc(c.get('vol_target'))}</td>"
            f"<td class='num'>{_esc(c.get('top_n'))}</td></tr>"
            for c in cfgs
        )
        def _sh_live_cell(s: dict) -> str:
            lr = s.get("live_return_pct")
            try:
                lr_s = f"{float(lr):+.2f}%" if lr is not None else "—"
            except Exception:
                lr_s = "—"
            thin = s.get("thin_live")
            if thin is None and s.get("days_live") is not None:
                try:
                    thin = int(s.get("days_live")) < 5
                except Exception:
                    thin = False
            if thin and lr_s != "—":
                lr_s = f"{lr_s} THIN"
            return lr_s

        def _sh_xs_cell(s: dict) -> str:
            xs = s.get("live_excess_pct")
            try:
                return f"{float(xs):+.2f}%" if xs is not None else "—"
            except Exception:
                return "—"

        sh_rows = "".join(
            f"<tr><td>{_esc(s.get('name'))}</td>"
            f"<td class='num'>{_money(s.get('total_value'))}</td>"
            f"<td class='num'>{_esc(_sh_live_cell(s))}</td>"
            f"<td class='num'>{_esc(_sh_xs_cell(s))}</td>"
            f"<td class='{_cls_num(s.get('return_pct'))}'>{_esc(s.get('return_pct'))}%</td>"
            f"<td class='num'>{_esc(s.get('days_live') if s.get('days_live') is not None else '—')}</td>"
            f"<td>{_esc(s.get('holdings') or s.get('n_holdings'))}</td></tr>"
            for s in shs
        )
        # SIGNAL live KPI (优先 status_obj.signal_live, 再 latest)
        sl = status_obj.get("signal_live") if isinstance(status_obj.get("signal_live"), dict) else None
        if not sl and isinstance(latest_obj, dict) and isinstance(latest_obj.get("signal_live"), dict):
            sl = latest_obj.get("signal_live")
        sl_live_s = "—"
        sl_xs_s = "—"
        sl_sub = "无 signal_live → etf live"
        if sl:
            try:
                lr = sl.get("live_return_pct")
                sl_live_s = f"{float(lr):+.2f}%" if lr is not None else "—"
            except Exception:
                sl_live_s = "—"
            try:
                xs = sl.get("live_excess_pct")
                sl_xs_s = f"{float(xs):+.2f}%" if xs is not None else "—"
            except Exception:
                sl_xs_s = "—"
            dl = sl.get("days_live")
            thin = sl.get("thin_live")
            if thin is None and dl is not None:
                try:
                    thin = int(dl) < 5
                except Exception:
                    thin = False
            if thin and sl_live_s != "—":
                sl_live_s = f"{sl_live_s} THIN"
            asof0 = sl.get("market_asof")
            # data_asof may not be defined yet here; fill after td0
            sl_sub = f"{sl.get('name') or ''} · Lrets={dl if dl is not None else '—'}"
            if dl == 0:
                sl_sub += " · 仅锚日"
        stale = bool(status_obj.get("latest_stale"))
        td0 = status_obj.get("trading_day") or {}
        data_asof = td0.get("data_asof") or (latest_obj or {}).get("market_asof")
        if isinstance(sl, dict) and sl.get("market_asof"):
            data_asof = sl.get("market_asof")
        data_lag = bool(td0.get("data_lag")) or bool(isinstance(sl, dict) and sl.get("data_lag"))
        if isinstance(sl, dict):
            extra = []
            if sl.get("market_asof") or data_asof:
                extra.append(f"asof={sl.get('market_asof') or data_asof}")
            if data_lag:
                extra.append("DATA_LAG")
            if extra:
                sl_sub = (sl_sub + " · " if sl_sub else "") + " · ".join(extra)
        warn_bits = []
        if data_lag:
            warn_bits.append(
                "<p class='muted' style='color:var(--amber)'>⚠ 行情滞后 DATA_LAG: 行情截至 "
                f"{_esc(data_asof)} · wall/交易日 {_esc(td0.get('date'))} "
                f"(nav/live 以 asof 为准)</p>"
            )
        if stale:
            warn_bits.append(
                "<p class='muted' style='color:var(--amber)'>⚠ latest 过旧: 信号时间 "
                f"{_esc(status_obj.get('latest_time'))} · 交易日 "
                f"{_esc(td0.get('date'))} → "
                f"<code>./etf refresh</code></p>"
            )
        stale_html = "".join(warn_bits)
        st_body = f"""
<p>{ab}</p>
{stale_html}
<div class="grid kpis">
  {_kpi('状态时间', _esc(status_obj.get('stamp')), _esc(status_obj.get('latest_time') or ''))}
  {_kpi('交易日', _esc(td0.get('is_trading_day')), f"date={td0.get('date') or '—'} asof={data_asof or '—'}" + (" DATA_LAG" if data_lag else ""))}
  {_kpi('SIGNAL live%', sl_live_s, sl_sub, 'green' if (sl and (sl.get('live_return_pct') or 0) > 0) else '')}
  {_kpi('SIGNAL xs%', sl_xs_s, 'vs 基准同期', 'green' if (sl and (sl.get('live_excess_pct') or 0) > 0) else '')}
</div>
<div class="grid kpis" style="margin-top:.55rem">
  {_kpi('监控', f"E{(status_obj.get('monitor') or {}).get('alert_error_n',0)}/W{(status_obj.get('monitor') or {}).get('alert_warn_n',0)}", '')}
  {_kpi('pipeline', _esc((status_obj.get('pipeline_last') or {}).get('ok')), _esc((status_obj.get('pipeline_last') or {}).get('stamp')))}
</div>
<div class="grid two" style="margin-top:.75rem">
  <div class="card"><h2>配置</h2>
    <table><thead><tr><th>名</th><th>frozen</th><th>research</th><th>vt</th><th>top_n</th></tr></thead>
    <tbody>{cfg_rows or '<tr><td colspan=5 class=muted>无</td></tr>'}</tbody></table>
  </div>
  <div class="card"><h2>影子</h2>
    <p class="muted">live%/xs% 为暖机后有效收益 · THIN=样本&lt;5日</p>
    <table><thead><tr><th>名</th><th>资产</th><th>live%</th><th>xs%</th><th>全样本</th><th>Lrets</th><th>持仓</th></tr></thead>
    <tbody>{sh_rows or '<tr><td colspan=7 class=muted>无</td></tr>'}</tbody></table>
  </div>
</div>
"""
        if status_txt:
            st_body += f"<details><summary>状态原文</summary><pre class='raw'>{_esc(status_txt)}</pre></details>"
    elif status_txt:
        st_body = f"<p>{ab}</p><h2>研究状态</h2><pre class='raw'>{_esc(status_txt)}</pre>"
    elif status_json:
        st_body = f"<p>{ab}</p><h2>研究状态 (JSON)</h2><pre class='raw'>{_esc(status_json)}</pre>"
    else:
        st_body = f"<p>{ab}</p><p>暂无 status (先跑 research_status / pipeline status 步骤)</p>"
    # today 一页速览
    today_txt = _read(OUTPUT_DIR / "today.txt")
    today_obj = _load_json(OUTPUT_DIR / "risk_audit" / "today.json")
    if today_txt:
        tbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>今日速览</h2><pre class='raw'>{_esc(today_txt)}</pre></div>"
            f"<p class='muted'>有效收益=live%+xs% · THIN=样本&lt;5日 · DATA_LAG=行情未到 · 生产 c01 冻结</p>"
        )
    elif isinstance(today_obj, dict) and today_obj.get("text"):
        tbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>今日速览</h2><pre class='raw'>{_esc(today_obj.get('text'))}</pre></div>"
        )
    else:
        tbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 today → <code>./etf today</code></p></div>"
        )
    (out_dir / "today.html").write_text(_page("今日速览", tbody, "今日"), encoding="utf-8")
    if today_txt:
        (out_dir / "today.txt").write_text(today_txt, encoding="utf-8")
    asof_txt = _read(OUTPUT_DIR / "asof.txt")
    asof_obj = _load_json(OUTPUT_DIR / "risk_audit" / "asof.json")
    if asof_txt:
        (out_dir / "asof.txt").write_text(asof_txt, encoding="utf-8")
    if isinstance(asof_obj, dict):
        (out_dir / "asof.json").write_text(
            json.dumps(asof_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    # asof 取证页
    if asof_txt:
        abody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>行情/收益取证</h2><pre class='raw'>{_esc(asof_txt)}</pre></div>"
            f"<p class='muted'>DATA_LAG 等行情 · 过旧用 refresh · <code>./etf asof</code></p>"
        )
    elif isinstance(asof_obj, dict) and asof_obj.get("text"):
        abody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>行情/收益取证</h2><pre class='raw'>{_esc(asof_obj.get('text'))}</pre></div>"
        )
    else:
        abody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 asof → <code>./etf asof</code></p></div>"
        )
    (out_dir / "asof.html").write_text(_page("行情取证", abody, "取证"), encoding="utf-8")

    # yield 有效收益页
    yield_txt = _read(OUTPUT_DIR / "yield.txt")
    yield_obj = _load_json(OUTPUT_DIR / "risk_audit" / "yield.json")
    if yield_txt:
        (out_dir / "yield.txt").write_text(yield_txt, encoding="utf-8")
    if isinstance(yield_obj, dict):
        (out_dir / "yield.json").write_text(
            json.dumps(yield_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if yield_txt:
        ybody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>有效收益 (live段)</h2><pre class='raw'>{_esc(yield_txt)}</pre></div>"
            f"<p class='muted'>有效收益=live%+xs% · 非全样本 · dtr={_esc((yield_obj or {}).get('days_to_ready') if isinstance(yield_obj, dict) else None)} · ETA {_esc(((yield_obj or {}).get('eta_note') if isinstance(yield_obj, dict) else None) or '—')} · <code>./etf yield</code> · <code>./etf progress</code></p>"
        )
    elif isinstance(yield_obj, dict) and yield_obj.get("text"):
        ybody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>有效收益 (live段)</h2><pre class='raw'>{_esc(yield_obj.get('text'))}</pre></div>"
        )
    else:
        ybody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 yield → <code>./etf yield</code> · <code>./etf progress</code></p></div>"
        )
    (out_dir / "yield.html").write_text(_page("有效收益", ybody, "收益"), encoding="utf-8")

    # brief 三合一页
    brief_txt = _read(OUTPUT_DIR / "brief.txt")
    brief_obj = _load_json(OUTPUT_DIR / "risk_audit" / "brief.json")
    if brief_txt:
        (out_dir / "brief.txt").write_text(brief_txt, encoding="utf-8")
    if isinstance(brief_obj, dict):
        (out_dir / "brief.json").write_text(
            json.dumps(brief_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if brief_txt:
        bbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>三合一速览</h2><pre class='raw'>{_esc(brief_txt)}</pre></div>"
            f"<p class='muted'>有效收益=live%+xs% · <code>./etf brief</code></p>"
        )
    elif isinstance(brief_obj, dict) and brief_obj.get("text"):
        bbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>三合一速览</h2><pre class='raw'>{_esc(brief_obj.get('text'))}</pre></div>"
        )
    else:
        bbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 brief → <code>./etf brief</code></p></div>"
        )
    (out_dir / "brief.html").write_text(_page("三合一速览", bbody, "速览"), encoding="utf-8")

    # data 行情状态页
    data_txt = _read(OUTPUT_DIR / "data_status.txt")
    data_obj = _load_json(OUTPUT_DIR / "risk_audit" / "data_status.json")
    if data_txt:
        (out_dir / "data_status.txt").write_text(data_txt, encoding="utf-8")
    if isinstance(data_obj, dict):
        (out_dir / "data_status.json").write_text(
            json.dumps(data_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if data_txt:
        dbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>行情状态</h2><pre class='raw'>{_esc(data_txt)}</pre></div>"
            f"<p class='muted'>DATA_LAG→等行情 · STALE→refresh · <code>./etf data</code></p>"
        )
    elif isinstance(data_obj, dict):
        # structured fallback
        dlines = [
            f"asof={data_obj.get('market_asof')} lag={data_obj.get('data_lag')}",
            f"stale={data_obj.get('latest_stale')} decision={data_obj.get('decision')}",
            f"action={data_obj.get('action')}",
        ]
        dbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>行情状态</h2><pre class='raw'>{_esc(chr(10).join(dlines))}</pre></div>"
        )
    else:
        dbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 data → <code>./etf data</code></p></div>"
        )
    (out_dir / "data.html").write_text(_page("行情状态", dbody, "行情"), encoding="utf-8")

    # next 决策页
    next_txt = _read(OUTPUT_DIR / "next.txt")
    next_obj = _load_json(OUTPUT_DIR / "risk_audit" / "next.json")
    if next_txt:
        (out_dir / "next.txt").write_text(next_txt, encoding="utf-8")
    if isinstance(next_obj, dict):
        (out_dir / "next.json").write_text(
            json.dumps(next_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if next_txt:
        nbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>一键下一步</h2><pre class='raw'>{_esc(next_txt)}</pre></div>"
            f"<p class='muted'>WAIT_DATA=等行情 · STALE=refresh · <code>./etf next</code></p>"
        )
    elif isinstance(next_obj, dict):
        nlines = [
            f"decision={next_obj.get('decision')}",
            f"asof={next_obj.get('market_asof')} lag={next_obj.get('data_lag')}",
            f"推荐: {next_obj.get('recommend')}",
            f"原因: {next_obj.get('why')}",
        ]
        nbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>一键下一步</h2><pre class='raw'>{_esc(chr(10).join(str(x) for x in nlines))}</pre></div>"
        )
    else:
        nbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 next → <code>./etf next</code></p></div>"
        )
    (out_dir / "next.html").write_text(_page("一键下一步", nbody, "下一步"), encoding="utf-8")

    # pull 强刷页
    pull_txt = _read(OUTPUT_DIR / "pull.txt")
    pull_obj = _load_json(OUTPUT_DIR / "risk_audit" / "pull.json")
    if pull_txt:
        (out_dir / "pull.txt").write_text(pull_txt, encoding="utf-8")
    if isinstance(pull_obj, dict):
        (out_dir / "pull.json").write_text(
            json.dumps(pull_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if pull_txt:
        pbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>行情强刷</h2><pre class='raw'>{_esc(pull_txt)}</pre></div>"
            f"<p class='muted'>asof 未推进=源站未出K · <code>./etf pull</code></p>"
        )
    elif isinstance(pull_obj, dict):
        after = pull_obj.get("after") if isinstance(pull_obj.get("after"), dict) else {}
        before = pull_obj.get("before") if isinstance(pull_obj.get("before"), dict) else {}
        plines = [
            f"before asof={before.get('data_asof')} lag={before.get('data_lag')}",
            f"after  asof={after.get('data_asof')} lag={after.get('data_lag')}",
            f"advanced={pull_obj.get('advanced')} cleared_lag={pull_obj.get('cleared_lag')}",
            f"bench_last={pull_obj.get('bench_last')}",
        ]
        pbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>行情强刷</h2><pre class='raw'>{_esc(chr(10).join(str(x) for x in plines))}</pre></div>"
        )
    else:
        pbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 pull → <code>./etf pull --bench-only</code></p></div>"
        )
    (out_dir / "pull.html").write_text(_page("行情强刷", pbody, "拉取"), encoding="utf-8")

    # go 一键闭环页
    go_txt = _read(OUTPUT_DIR / "go.txt")
    go_obj = _load_json(OUTPUT_DIR / "risk_audit" / "go.json")
    if go_txt:
        (out_dir / "go.txt").write_text(go_txt, encoding="utf-8")
    if isinstance(go_obj, dict):
        (out_dir / "go.json").write_text(
            json.dumps(go_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if go_txt:
        gbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>一键闭环 GO</h2><pre class='raw'>{_esc(go_txt)}</pre></div>"
            f"<p class='muted'><code>./etf go</code></p>"
        )
    elif isinstance(go_obj, dict):
        glines = [
            f"decision={go_obj.get('decision')}",
            f"asof={go_obj.get('market_asof')} lag={go_obj.get('data_lag')}",
            f"did_wait={go_obj.get('did_wait')} wait_code={go_obj.get('wait_code')}",
            f"推荐: {go_obj.get('recommend')}",
        ]
        gbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>一键闭环 GO</h2><pre class='raw'>{_esc(chr(10).join(str(x) for x in glines))}</pre></div>"
        )
    else:
        gbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 go → <code>./etf go --no-wait</code></p></div>"
        )
    (out_dir / "go.html").write_text(_page("一键闭环", gbody, "GO"), encoding="utf-8")

    # ready 可判性页
    ready_txt = _read(OUTPUT_DIR / "ready.txt")
    ready_obj = _load_json(OUTPUT_DIR / "risk_audit" / "ready.json")
    if ready_txt:
        (out_dir / "ready.txt").write_text(ready_txt, encoding="utf-8")
    if isinstance(ready_obj, dict):
        (out_dir / "ready.json").write_text(
            json.dumps(ready_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if ready_txt:
        rbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>有效收益可判性</h2><pre class='raw'>{_esc(ready_txt)}</pre></div>"
            f"<p class='muted'><code>./etf ready</code> · dtr={_esc((ready_obj or {}).get('days_to_ready') if isinstance(ready_obj, dict) else None)} · ETA {_esc(((ready_obj or {}).get('eta_note') if isinstance(ready_obj, dict) else None) or '—')} · READY才可强解读 live%/xs% · <code>./etf progress</code></p>"
        )
    elif isinstance(ready_obj, dict):
        rlines = [
            f"level={ready_obj.get('level')}",
            f"asof={ready_obj.get('market_asof')} lag={ready_obj.get('data_lag')}",
            f"live={ready_obj.get('live_return_pct')} xs={ready_obj.get('live_excess_pct')} Lrets={ready_obj.get('days_live')}",
            f"说明: {ready_obj.get('note')}",
        ]
        rbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>有效收益可判性</h2><pre class='raw'>{_esc(chr(10).join(str(x) for x in rlines))}</pre></div>"
        )
    else:
        rbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 ready → <code>./etf ready</code></p></div>"
        )
    (out_dir / "ready.html").write_text(_page("可判性", rbody, "可判"), encoding="utf-8")

    # digest 人读一页
    digest_txt = _read(OUTPUT_DIR / "digest.txt")
    digest_obj = _load_json(OUTPUT_DIR / "risk_audit" / "digest.json")
    if digest_txt:
        (out_dir / "digest.txt").write_text(digest_txt, encoding="utf-8")
    if isinstance(digest_obj, dict):
        (out_dir / "digest.json").write_text(
            json.dumps(digest_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if digest_txt:
        dgbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>DIGEST</h2><pre class='raw'>{_esc(digest_txt)}</pre></div>"
            f"<p class='muted'><code>./etf digest</code></p>"
        )
    elif isinstance(digest_obj, dict) and digest_obj.get("text"):
        dgbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>DIGEST</h2><pre class='raw'>{_esc(digest_obj.get('text'))}</pre></div>"
        )
    else:
        dgbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 digest → <code>./etf digest</code></p></div>"
        )
    (out_dir / "digest.html").write_text(_page("DIGEST", dgbody, "可判"), encoding="utf-8")

    # eod 收盘闭环页
    eod_txt = _read(OUTPUT_DIR / "eod.txt")
    eod_obj = _load_json(OUTPUT_DIR / "risk_audit" / "eod.json")
    if eod_txt:
        (out_dir / "eod.txt").write_text(eod_txt, encoding="utf-8")
    if isinstance(eod_obj, dict):
        (out_dir / "eod.json").write_text(
            json.dumps(eod_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if eod_txt:
        ebody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>EOD 收盘闭环</h2><pre class='raw'>{_esc(eod_txt)}</pre></div>"
            f"<p class='muted'><code>./etf eod --timeout 1800</code> · 推进 asof 后积累 live 样本</p>"
        )
    elif isinstance(eod_obj, dict):
        elines = [
            f"level={eod_obj.get('level')}",
            f"asof={eod_obj.get('market_asof')} lag={eod_obj.get('data_lag')}",
            f"live={eod_obj.get('live_return_pct')} xs={eod_obj.get('live_excess_pct')} "
            f"Lrets={eod_obj.get('days_live')} days_to_ready={eod_obj.get('days_to_ready')}",
            f"推荐: {eod_obj.get('recommend')}",
        ]
        ebody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>EOD 收盘闭环</h2><pre class='raw'>{_esc(chr(10).join(str(x) for x in elines))}</pre></div>"
        )
    else:
        ebody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 eod → <code>./etf eod --no-wait</code></p></div>"
        )
    (out_dir / "eod.html").write_text(_page("EOD", ebody, "EOD"), encoding="utf-8")

    # progress 轨迹页
    prog_txt = _read(OUTPUT_DIR / "progress.txt")
    if prog_txt:
        (out_dir / "progress.txt").write_text(prog_txt, encoding="utf-8")
        pbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>可判性轨迹</h2><pre class='raw'>{_esc(prog_txt)}</pre></div>"
            f"<p class='muted'><code>./etf progress</code> · 观察 Lrets→READY</p>"
        )
    else:
        pbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 progress → <code>./etf ready</code> 或 <code>./etf progress</code></p></div>"
        )
    (out_dir / "progress.html").write_text(_page("轨迹", pbody, "轨迹"), encoding="utf-8")

    # pulse 脉搏页
    pulse_txt = _read(OUTPUT_DIR / "pulse.txt")
    if pulse_txt:
        (out_dir / "pulse.txt").write_text(pulse_txt, encoding="utf-8")
        ubody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>脉搏 PULSE</h2><pre class='raw'>{_esc(pulse_txt)}</pre></div>"
            f"<p class='muted'><code>./etf</code>=pulse · <code>./etf pulse</code> · data+ready+ETA+progress</p>"
        )
    else:
        ubody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 pulse → <code>./etf pulse</code></p></div>"
        )
    (out_dir / "pulse.html").write_text(_page("脉搏", ubody, "脉搏"), encoding="utf-8")

    # wait-asof 页
    wait_txt = _read(OUTPUT_DIR / "wait_asof.txt")
    wait_obj = _load_json(OUTPUT_DIR / "risk_audit" / "wait_asof.json")
    if wait_txt:
        (out_dir / "wait_asof.txt").write_text(wait_txt, encoding="utf-8")
    if isinstance(wait_obj, dict):
        (out_dir / "wait_asof.json").write_text(
            json.dumps(wait_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if wait_txt:
        wbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>WAIT-ASOF</h2><pre class='raw'>{_esc(wait_txt)}</pre></div>"
            f"<p class='muted'><code>./etf wait-asof</code></p>"
        )
    elif isinstance(wait_obj, dict):
        wlines = [
            f"ok={wait_obj.get('ok')} advanced={wait_obj.get('advanced')}",
            f"start={(wait_obj.get('start') or {}).get('data_asof')} end={(wait_obj.get('end') or {}).get('data_asof')}",
            f"attempts={wait_obj.get('attempts')}",
        ]
        wbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><h2>WAIT-ASOF</h2><pre class='raw'>{_esc(chr(10).join(str(x) for x in wlines))}</pre></div>"
        )
    else:
        wbody = (
            f"<p>{ab}</p>{_lag_banner_html(latest_obj)}"
            f"<div class='card'><p>暂无 wait-asof → <code>./etf wait-asof --timeout 120 --no-follow</code></p></div>"
        )
    (out_dir / "wait_asof.html").write_text(_page("WAIT-ASOF", wbody, "拉取"), encoding="utf-8")

    (out_dir / "status.html").write_text(_page("研究状态", st_body, "状态"), encoding="utf-8")
    if status_txt:
        (out_dir / "status.txt").write_text(status_txt, encoding="utf-8")
    if status_json:
        (out_dir / "research_status.json").write_text(status_json, encoding="utf-8")

    if latest_json:
        (out_dir / "latest.json").write_text(latest_json, encoding="utf-8")
    if pipeline:
        (out_dir / "pipeline_last.json").write_text(pipeline, encoding="utf-8")
    if mon_obj is not None:
        (out_dir / "shadow_monitor.json").write_text(
            json.dumps(mon_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # asof / lag for site_meta
    meta_asof = None
    meta_lag = False
    meta_pipe_asof = None
    meta_pipe_lag = None
    try:
        if isinstance(latest_obj, dict):
            meta_asof = latest_obj.get("market_asof")
            slm = latest_obj.get("signal_live") if isinstance(latest_obj.get("signal_live"), dict) else {}
            if slm.get("market_asof"):
                meta_asof = slm.get("market_asof")
            if slm.get("data_lag") is not None:
                meta_lag = bool(slm.get("data_lag"))
        if isinstance(status_obj, dict):
            td_m = status_obj.get("trading_day") or {}
            meta_asof = meta_asof or td_m.get("data_asof")
            meta_lag = meta_lag or bool(td_m.get("data_lag"))
        # pipeline_last 取证
        pipe_meta = _load_json(OUTPUT_DIR / "risk_audit" / "pipeline_last.json")
        if isinstance(pipe_meta, dict):
            meta_pipe_asof = pipe_meta.get("data_asof")
            meta_pipe_lag = pipe_meta.get("data_lag")
            meta_asof = meta_asof or meta_pipe_asof
            if meta_pipe_lag is not None:
                meta_lag = meta_lag or bool(meta_pipe_lag)
        if meta_asof is None or not meta_lag:
            from etf_rotation.calendar_util import resolve_trading_day as _rtd
            _td = _rtd()
            meta_asof = meta_asof or _td.get("data_asof")
            meta_lag = meta_lag or bool(_td.get("data_lag"))
    except Exception:
        pass

    _meta_ready = _load_json(OUTPUT_DIR / "risk_audit" / "ready.json")
    _meta_digest = _load_json(OUTPUT_DIR / "risk_audit" / "digest.json")
    _meta_prog = _load_json(OUTPUT_DIR / "risk_audit" / "progress_latest.json")
    _meta_yield = _load_json(OUTPUT_DIR / "risk_audit" / "yield.json")
    _meta_pulse = _load_json(OUTPUT_DIR / "risk_audit" / "pulse.json")
    _meta_dtr = None
    _meta_eta = None
    _meta_lvl = None
    for _mo in (_meta_ready, _meta_digest, _meta_prog, _meta_yield):
        if not isinstance(_mo, dict):
            continue
        if _meta_dtr is None and _mo.get("days_to_ready") is not None:
            _meta_dtr = _mo.get("days_to_ready")
        if not _meta_eta and _mo.get("eta_note"):
            _meta_eta = _mo.get("eta_note")
        if not _meta_lvl and _mo.get("level"):
            _meta_lvl = _mo.get("level")
        if _meta_dtr is not None and _meta_eta and _meta_lvl:
            break

    meta = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "has_latest": bool(latest_txt or latest_obj),
        "has_monitor": bool(monitor_txt or mon_obj),
        "has_summary": bool(summary),
        "has_status": bool(status_txt or status_json or status_obj),
        "has_today": bool(today_txt or today_obj or (out_dir / "today.html").exists()),
        "has_asof": bool(
            (OUTPUT_DIR / "asof.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "asof.json").exists()
            or (out_dir / "asof.txt").exists()
            or (out_dir / "asof.json").exists()
        ),
        "has_yield": bool(
            (OUTPUT_DIR / "yield.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "yield.json").exists()
            or (out_dir / "yield.txt").exists()
            or (out_dir / "yield.html").exists()
        ),
        "has_brief": bool(
            (OUTPUT_DIR / "brief.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "brief.json").exists()
            or (out_dir / "brief.txt").exists()
            or (out_dir / "brief.html").exists()
        ),
        "has_data": bool(
            (OUTPUT_DIR / "data_status.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "data_status.json").exists()
            or (out_dir / "data.html").exists()
        ),
        "has_next": bool(
            (OUTPUT_DIR / "next.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "next.json").exists()
            or (out_dir / "next.html").exists()
        ),
        "has_pull": bool(
            (OUTPUT_DIR / "pull.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "pull.json").exists()
            or (out_dir / "pull.html").exists()
        ),
        "has_go": bool(
            (OUTPUT_DIR / "go.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "go.json").exists()
            or (out_dir / "go.html").exists()
        ),
        "has_ready": bool(
            (OUTPUT_DIR / "ready.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "ready.json").exists()
            or (out_dir / "ready.html").exists()
        ),
        "has_digest": bool(
            (OUTPUT_DIR / "digest.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "digest.json").exists()
            or (out_dir / "digest.html").exists()
        ),
        "has_eod": bool(
            (OUTPUT_DIR / "eod.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "eod.json").exists()
            or (out_dir / "eod.html").exists()
        ),
        "has_pulse": bool(
            (OUTPUT_DIR / "pulse.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "pulse.json").exists()
            or (out_dir / "pulse.html").exists()
        ),
        "has_progress": bool(
            (OUTPUT_DIR / "progress.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "progress.jsonl").exists()
            or (out_dir / "progress.html").exists()
        ),
        "days_to_ready": _meta_dtr,
        "eta_note": _meta_eta,
        "ready_level": _meta_lvl,
        "next_action": (_meta_pulse or {}).get("next_action") if isinstance(_meta_pulse, dict) else None,
        "readable_yield": (_meta_pulse or {}).get("readable_yield") if isinstance(_meta_pulse, dict) else None,
        "has_wait_asof": bool(
            (OUTPUT_DIR / "wait_asof.txt").exists()
            or (OUTPUT_DIR / "risk_audit" / "wait_asof.json").exists()
            or (out_dir / "wait_asof.html").exists()
        ),
        "has_dashboard": True,
        "has_live": bool(
            live_txt
            or (isinstance(live_json, list) and live_json)
            or (out_dir / "live.html").exists()
        ),
        "has_compare": bool(cmp_txt or (isinstance(cmp_json, list) and cmp_json)),
        "market_asof": meta_asof,
        "data_lag": bool(meta_lag),
        "pipeline_asof": meta_pipe_asof,
        "pipeline_lag": meta_pipe_lag,
        "alert_error_n": alerts["error_n"],
        "alert_warn_n": alerts["warn_n"],
        "out": str(out_dir),
    }
    (out_dir / "site_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="构建 Pages 静态站 / 日更可视化面板")
    ap.add_argument("--out", default=str(OUTPUT_DIR / "site"))
    args = ap.parse_args()
    meta = build(Path(args.out))
    print(f"WROTE site → {meta['out']}")
    print(
        f"  latest={meta['has_latest']} monitor={meta['has_monitor']} "
        f"summary={meta['has_summary']} status={meta['has_status']} "
        f"today={meta.get('has_today')} asof_file={meta.get('has_asof')} "
        f"dashboard={meta['has_dashboard']} "
        f"asof={meta.get('market_asof')} lag={meta.get('data_lag')}"
    )


if __name__ == "__main__":
    main()
