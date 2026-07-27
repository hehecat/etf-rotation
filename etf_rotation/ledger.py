"""多年可回溯日更账本.

设计原则:
  - 以 market_asof (交易日) 为主键, 主账本按交易日唯一一行覆盖; progress 仍按 source 去重
  - 有效收益只认 live% + xs%; lag/thin 时标记不可读
  - 文件全部落在 output/, 由 Daily GHA commit 入库
  - 窗口默认 ~10 年交易日, 避免再丢样本

主文件:
  output/action_history.jsonl          生产动作时间线
  output/risk_audit/progress.jsonl     可判性推进
  output/risk_audit/daily_scorecard.jsonl  一日一行记分卡 (多年主账本)
  output/risk_audit/daily_scorecard_latest.json
  output/ledger_summary.txt            history 命令人读摘要
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import OUTPUT_DIR, ensure_dirs

# ~10 年交易日; 覆盖多年观察仍可单文件 grep
DEFAULT_MAX_LINES = 2800
SCORECARD_PATH = "risk_audit/daily_scorecard.jsonl"
SCORECARD_LATEST = "risk_audit/daily_scorecard_latest.json"
PROGRESS_PATH = "risk_audit/progress.jsonl"
ACTION_PATH = "action_history.jsonl"


def _day_key(value: Any, fallback: str | None = None) -> str:
    s = str(value or "")[:10]
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s
    if fallback:
        fb = str(fallback)[:10]
        if len(fb) >= 10:
            return fb
    return datetime.now().strftime("%Y-%m-%d")


def _read_json(path: Path) -> dict[str, Any] | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def append_jsonl(
    path: Path,
    rec: dict[str, Any],
    *,
    day_field: str = "date",
    source_field: str | None = "source",
    max_lines: int = DEFAULT_MAX_LINES,
) -> dict[str, Any]:
    """按日(+可选 source) 覆盖写入 jsonl, 返回最终 rec."""
    path.parent.mkdir(parents=True, exist_ok=True)
    day = _day_key(rec.get(day_field) or rec.get("market_asof") or rec.get("asof"))
    rec = dict(rec)
    rec[day_field] = day
    if "stamp" not in rec:
        rec["stamp"] = datetime.now().isoformat(timespec="seconds")

    src = rec.get(source_field) if source_field else None
    kept: list[dict[str, Any]] = []
    for row in _load_jsonl(path):
        rday = _day_key(row.get(day_field) or row.get("market_asof") or row.get("date"))
        if rday == day:
            if source_field and src is not None:
                if row.get(source_field) == src:
                    continue
            else:
                continue
        kept.append(row)
    kept.append(rec)
    kept.sort(key=lambda r: str(r.get(day_field) or r.get("market_asof") or r.get("date") or ""))
    kept = kept[-max_lines:]
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
        encoding="utf-8",
    )
    return rec


def _pct(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _truthy_lag(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return bool(v)


def build_scorecard_row(
    *,
    source: str = "pipeline",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从当日产物拼一行记分卡 (生产 + 信号影子)."""
    ensure_dirs()
    risk = OUTPUT_DIR / "risk_audit"

    latest = _read_json(OUTPUT_DIR / "latest.json")
    if not isinstance(latest, dict):
        latest = {}
    pulse = _read_json(risk / "pulse.json")
    if not isinstance(pulse, dict):
        pulse = {}
    ready = _read_json(risk / "ready.json")
    if not isinstance(ready, dict):
        ready = {}
    yj = _read_json(risk / "yield.json")
    if not isinstance(yj, dict):
        yj = {}
    dig = _read_json(risk / "digest.json")
    if not isinstance(dig, dict):
        dig = {}
    data = _read_json(risk / "data_status.json")
    if not isinstance(data, dict):
        data = {}
    live_list = _read_json(risk / "shadow_live.json")
    mon = _read_json(risk / "shadow_monitor.json")
    pipe = _read_json(risk / "pipeline_last.json")
    if not isinstance(pipe, dict):
        pipe = {}

    sl = latest.get("signal_live") if isinstance(latest.get("signal_live"), dict) else {}
    if not sl and isinstance(yj.get("signal_live"), dict):
        sl = yj.get("signal_live") or {}

    asof = (
        pulse.get("market_asof")
        or ready.get("market_asof")
        or yj.get("market_asof")
        or data.get("market_asof")
        or latest.get("market_asof")
        or sl.get("market_asof")
    )
    lag = pulse.get("data_lag")
    if lag is None:
        lag = ready.get("data_lag")
    if lag is None:
        lag = data.get("data_lag")
    if lag is None:
        lag = sl.get("data_lag")
    lag = _truthy_lag(lag)

    level = pulse.get("level") or ready.get("level") or dig.get("level")
    dtr = pulse.get("days_to_ready")
    if dtr is None:
        dtr = ready.get("days_to_ready")
    dl = pulse.get("days_live")
    if dl is None:
        dl = ready.get("days_live")
    if dl is None:
        dl = yj.get("days_live")
    if dl is None:
        dl = sl.get("days_live")

    lr = pulse.get("live_return_pct")
    if lr is None:
        lr = ready.get("live_return_pct")
    if lr is None:
        lr = yj.get("live_return_pct")
    if lr is None:
        lr = sl.get("live_return_pct")

    xs = pulse.get("live_excess_pct")
    if xs is None:
        xs = ready.get("live_excess_pct")
    if xs is None:
        xs = yj.get("live_excess_pct")
    if xs is None:
        xs = sl.get("live_excess_pct")

    thin = pulse.get("thin_live")
    if thin is None:
        thin = ready.get("thin_live")
    if thin is None:
        thin = sl.get("thin_live")
    if thin is None and dl is not None:
        try:
            thin = int(dl) < 5
        except Exception:
            thin = None

    readable = pulse.get("readable_yield")
    if readable is None:
        readable = bool(level == "READY" and not lag and not thin)

    # 影子对照: 信号默认 + 监控列表摘要
    shadows: list[dict[str, Any]] = []
    if isinstance(live_list, list):
        for row in live_list:
            if not isinstance(row, dict) or not row.get("exists", True):
                continue
            shadows.append(
                {
                    "name": row.get("name"),
                    "live_return_pct": _pct(row.get("live_return_pct")),
                    "live_excess_pct": _pct(row.get("live_excess_pct")),
                    "days_live": row.get("days_live")
                    if row.get("days_live") is not None
                    else row.get("live_n_rets"),
                    "thin_live": row.get("thin_live"),
                    "holdings": row.get("holdings") or row.get("holdings_str"),
                    "total_value": row.get("total_value"),
                    "signal": bool(row.get("signal") or row.get("is_signal_default")),
                }
            )

    mon_err = mon_warn = None
    if isinstance(mon, dict):
        mon_err = mon.get("alert_error_n")
        mon_warn = mon.get("alert_warn_n")

    holding = None
    h = latest.get("holding")
    if isinstance(h, dict):
        holding = h.get("name") or h.get("code")
    elif h:
        holding = str(h)

    rec: dict[str, Any] = {
        "schema": 1,
        "source": source,
        "date": _day_key(asof),
        "market_asof": asof,
        "data_lag": lag,
        "latest_stale": bool(data.get("latest_stale")),
        "trading_day": None,
        "level": level,
        "decision": pulse.get("decision")
        or data.get("decision")
        or ready.get("decision")
        or dig.get("decision"),
        "next_action": pulse.get("next_action"),
        "readable_yield": readable,
        "days_live": dl,
        "days_to_ready": dtr,
        "thin_live": thin,
        "live_return_pct": _pct(lr),
        "live_excess_pct": _pct(xs),
        "eta_note": pulse.get("eta_note") or ready.get("eta_note") or yj.get("eta_note"),
        "prod": {
            "config": latest.get("config"),
            "action": latest.get("action"),
            "market_ok": latest.get("market_ok"),
            "holding": holding,
            "total_value": latest.get("total_value"),
            "return_pct": latest.get("return_pct"),
            "time": latest.get("time"),
        },
        "signal_live": {
            "name": sl.get("name"),
            "live_return_pct": _pct(sl.get("live_return_pct")),
            "live_excess_pct": _pct(sl.get("live_excess_pct")),
            "days_live": sl.get("days_live"),
            "thin_live": sl.get("thin_live"),
            "holdings": sl.get("holdings"),
            "market_asof": sl.get("market_asof"),
        }
        if sl
        else None,
        "shadows": shadows,
        "alerts": {"error_n": mon_err, "warn_n": mon_warn},
        "pipeline_ok": pipe.get("ok"),
        "pipeline_stamp": pipe.get("stamp"),
    }
    # trading_day from data/status if present
    td = data.get("trading_day") if isinstance(data.get("trading_day"), dict) else None
    if td is None and isinstance(pipe.get("trading_day"), dict):
        td = pipe.get("trading_day")
    if isinstance(td, dict):
        rec["trading_day"] = {
            "date": td.get("date"),
            "is_trading_day": td.get("is_trading_day"),
            "data_asof": td.get("data_asof"),
            "data_lag": td.get("data_lag"),
        }
    if extra:
        rec.update(extra)
    return rec


def append_daily_scorecard(
    *,
    source: str = "pipeline",
    extra: dict[str, Any] | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
) -> dict[str, Any]:
    """写 daily_scorecard.jsonl + latest; 返回行."""
    ensure_dirs()
    rec = build_scorecard_row(source=source, extra=extra)
    path = OUTPUT_DIR / SCORECARD_PATH
    # 主账本按交易日唯一一行 (后写覆盖); source 仅记录写入方
    rec = append_jsonl(path, rec, day_field="date", source_field=None, max_lines=max_lines)
    latest = OUTPUT_DIR / SCORECARD_LATEST
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rec


def summarize_scorecard(
    *,
    limit: int | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """汇总记分卡: 可读日数、空仓日、xs 分布、趋势开关."""
    p = path or (OUTPUT_DIR / SCORECARD_PATH)
    rows = _load_jsonl(p)
    if limit:
        rows = rows[-limit:]
    n = len(rows)
    if not n:
        return {
            "n": 0,
            "path": str(p),
            "note": "尚无 daily_scorecard → 跑日更或 ./etf history --append",
        }

    readable_rows = [r for r in rows if r.get("readable_yield") is True]
    lag_rows = [r for r in rows if r.get("data_lag")]
    thin_rows = [r for r in rows if r.get("thin_live")]
    prod_empty = 0
    prod_open = 0
    trend_on = 0
    trend_off = 0
    xs_vals: list[float] = []
    live_vals: list[float] = []
    for r in rows:
        prod = r.get("prod") if isinstance(r.get("prod"), dict) else {}
        mk = prod.get("market_ok")
        if mk is True:
            trend_on += 1
        elif mk is False:
            trend_off += 1
        act = str(prod.get("action") or "")
        hold = prod.get("holding")
        if (not hold) or ("空仓" in act):
            prod_empty += 1
        else:
            prod_open += 1
    for r in readable_rows:
        xs = _pct(r.get("live_excess_pct"))
        lr = _pct(r.get("live_return_pct"))
        if xs is not None:
            xs_vals.append(xs)
        if lr is not None:
            live_vals.append(lr)

    def _stats(vals: list[float]) -> dict[str, Any]:
        if not vals:
            return {"n": 0}
        s = sorted(vals)
        mid = s[len(s) // 2]
        return {
            "n": len(vals),
            "min": round(s[0], 4),
            "max": round(s[-1], 4),
            "median": round(mid, 4),
            "last": round(vals[-1], 4),
            "pos": sum(1 for x in vals if x > 0),
            "neg": sum(1 for x in vals if x < 0),
        }

    first = rows[0]
    last = rows[-1]
    return {
        "n": n,
        "path": str(p),
        "first_date": first.get("date") or first.get("market_asof"),
        "last_date": last.get("date") or last.get("market_asof"),
        "readable_n": len(readable_rows),
        "lag_n": len(lag_rows),
        "thin_n": len(thin_rows),
        "trend_on_n": trend_on,
        "trend_off_n": trend_off,
        "prod_empty_n": prod_empty,
        "prod_open_n": prod_open,
        "xs_readable": _stats(xs_vals),
        "live_readable": _stats(live_vals),
        "last_row": {
            "date": last.get("date"),
            "level": last.get("level"),
            "readable_yield": last.get("readable_yield"),
            "live_return_pct": last.get("live_return_pct"),
            "live_excess_pct": last.get("live_excess_pct"),
            "data_lag": last.get("data_lag"),
            "next_action": last.get("next_action"),
            "action": (last.get("prod") or {}).get("action")
            if isinstance(last.get("prod"), dict)
            else None,
        },
    }


def render_summary_text(summary: dict[str, Any]) -> str:
    lines = ["======== LEDGER 日更账本 ========"]
    if not summary.get("n"):
        lines.append(summary.get("note") or "空账本")
        lines.append("========")
        return "\n".join(lines) + "\n"
    lines.append(f"样本日: {summary.get('n')}  ({summary.get('first_date')} → {summary.get('last_date')})")
    lines.append(
        f"可读收益日: {summary.get('readable_n')}  "
        f"THIN: {summary.get('thin_n')}  DATA_LAG: {summary.get('lag_n')}"
    )
    lines.append(
        f"趋势开/关: {summary.get('trend_on_n')}/{summary.get('trend_off_n')}  "
        f"生产空仓/有仓: {summary.get('prod_empty_n')}/{summary.get('prod_open_n')}"
    )
    xs = summary.get("xs_readable") or {}
    if xs.get("n"):
        lines.append(
            f"可读 xs%: n={xs['n']} 中位={xs['median']}  "
            f"min={xs['min']} max={xs['max']}  "
            f"+日{xs['pos']}/-日{xs['neg']}  last={xs['last']}"
        )
    else:
        lines.append("可读 xs%: 尚无 (需 READY 且无 lag)")
    live = summary.get("live_readable") or {}
    if live.get("n"):
        lines.append(
            f"可读 live%: n={live['n']} 中位={live['median']}  "
            f"min={live['min']} max={live['max']}  last={live['last']}"
        )
    last = summary.get("last_row") or {}
    lines.append(
        f"最新: {last.get('date')} level={last.get('level')} "
        f"readable={last.get('readable_yield')} "
        f"live={last.get('live_return_pct')} xs={last.get('live_excess_pct')} "
        f"action={last.get('action')} next={last.get('next_action')}"
    )
    lines.append("口径: 只认 live%+xs%; lag/THIN 日不参与改进判断")
    lines.append("改进门槛: 可读日≥60 且 dual-end 门控仍过, 才讨论结构变更")
    lines.append("========")
    return "\n".join(lines) + "\n"


def write_summary_files(summary: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_dirs()
    summary = summary or summarize_scorecard()
    text = render_summary_text(summary)
    (OUTPUT_DIR / "ledger_summary.txt").write_text(text, encoding="utf-8")
    (OUTPUT_DIR / "risk_audit" / "ledger_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary
