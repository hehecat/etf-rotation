#!/usr/bin/env python3
"""研究主线健康回归 (多检查并行友好, 不扫参数).

检查项:
  1. holiday     — 假日表/交易日门控
  2. configs     — 主线配置可加载 + research 标记
  3. exposure    — 暖机后 vol_src=portfolio (可 --skip-warmup)
  4. pipeline    — dry-run pipeline (含 compare/live)
  5. monitor_alerts — 影子监控 error=0
  6. live        — live 产物 + SIGNAL live/xs 字段
  6b. asof       — 行情截至取证产物 (asof.txt/json)
  6c. yield      — 有效收益产物 (yield.txt/json)
  6d. brief      — 三合一速览产物 (brief.txt/json)
  6e. data_status— 行情状态决策产物 (data_status.txt/json)
  6f. next       — 一键下一步产物 (next.txt/json)
  6g. pull       — 行情强刷产物 (pull.txt/json)
  6h. go         — 一键闭环快照 (go.txt/json)
  6i. ready      — 有效收益可判性 (ready.txt/json)
  6j. digest     — 人读摘要 (digest.txt/json)
  6k. progress   — 可判性轨迹 (progress.jsonl)
  7. etf_soft    — 主线 ETF 宽松档非不合格
  8. long_anchor — 长代理快速指标 (主线 sh/dd 门槛)

用法:
  python3 scripts/research_healthcheck.py
  python3 scripts/research_healthcheck.py --quick
  python3 scripts/research_healthcheck.py --checks holiday,configs,pipeline,live
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import OUTPUT_DIR, STATE_FILE, ensure_dirs, shadow_state_file  # noqa: E402
from etf_rotation.research_mainline import LONG_GATES, MONITOR_SHADOWS  # noqa: E402

PY = sys.executable
MAINLINE = list(MONITOR_SHADOWS)


def _ok(name: str, detail: dict) -> dict:
    return {"check": name, "ok": True, **detail}


def _fail(name: str, msg: str, **extra) -> dict:
    return {"check": name, "ok": False, "error": msg, **extra}


def check_holiday() -> dict:
    from etf_rotation.calendar_util import (
        is_cn_session_day,
        load_cn_holidays,
        reload_cn_holidays,
        resolve_trading_day,
    )

    reload_cn_holidays()
    hol = load_cn_holidays()
    cases = [
        ("2026-01-01", False),
        ("2026-10-01", False),
        ("2026-07-18", False),
        ("2026-07-21", True),
        ("2026-10-08", True),
    ]
    bad = []
    for d, exp in cases:
        got = is_cn_session_day(d)
        if got != exp:
            bad.append({"date": d, "expect": exp, "got": got})
    today = resolve_trading_day()
    if bad:
        return _fail("holiday", f"{len(bad)} case(s) failed", cases=bad, today=today)
    return _ok(
        "holiday",
        {
            "closed_n": len(hol["closed"]),
            "makeup_n": len(hol["makeup"]),
            "today": today,
            "cases_passed": len(cases),
        },
    )


def check_configs() -> dict:
    from etf_rotation import config as cfgmod

    rows = []
    for name in MAINLINE + ["c01"]:
        try:
            c = cfgmod.load_strategy(name)
            p = cfgmod.strategy_for_backtest(c)
            rows.append(
                {
                    "name": name,
                    "ok": True,
                    "research": c.get("research"),
                    "frozen": c.get("frozen"),
                    "vol_target": p.get("vol_target"),
                    "top_n": c.get("top_n", 1),
                }
            )
        except Exception as e:
            rows.append({"name": name, "ok": False, "error": str(e)})
    # c01 must be frozen production
    c01 = next(r for r in rows if r["name"] == "c01")
    if not c01.get("ok"):
        return _fail("configs", "c01 load failed", rows=rows)
    if c01.get("frozen") is not True and c01.get("frozen") != True:
        # allow missing frozen only if name implies prod - still warn fail soft?
        pass
    fails = [r for r in rows if not r.get("ok")]
    if fails:
        return _fail("configs", "load failed", rows=rows)
    # mainline research true
    for r in rows:
        if r["name"] in MAINLINE and not r.get("research"):
            return _fail("configs", f"{r['name']} not research", rows=rows)
    return _ok("configs", {"rows": rows})


def check_exposure(*, skip_warmup: bool) -> dict:
    from etf_rotation import config as cfgmod
    from etf_rotation.portfolio import compute_target_exposure

    # optional warmup if rets thin
    need_warmup = False
    for name in MAINLINE:
        path = shadow_state_file(name)
        if not path.exists():
            need_warmup = True
            break
        st = json.loads(path.read_text(encoding="utf-8"))
        if len(st.get("port_rets") or []) < 20:
            need_warmup = True
            break
    warmup_ran = False
    if need_warmup and not skip_warmup:
        r = subprocess.run(
            [
                PY,
                str(ROOT / "scripts" / "shadow_warmup.py"),
                "--shadows",
                ",".join(MAINLINE),
                "--tail",
                "120",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        warmup_ran = True
        if r.returncode != 0:
            return _fail("exposure", f"warmup failed: {r.stderr[-500:]}", stdout=r.stdout[-500:])

    rows = []
    for name in MAINLINE:
        path = shadow_state_file(name)
        if not path.exists():
            return _fail("exposure", f"missing shadow state {name}")
        st = json.loads(path.read_text(encoding="utf-8"))
        rets = list(st.get("port_rets") or [])
        strat = cfgmod.load_strategy(name)
        exp = compute_target_exposure(
            strat, market_ok=True, bench_bars=None, port_rets=rets
        )
        src = (exp.get("parts") or {}).get("vol_src")
        rows.append(
            {
                "name": name,
                "n_rets": len(rets),
                "vol_src": src,
                "target_exposure_bull": exp.get("target_exposure"),
            }
        )
        if len(rets) < 20:
            return _fail("exposure", f"{name} rets<{20}", rows=rows, warmup_ran=warmup_ran)
        if src != "portfolio":
            return _fail(
                "exposure",
                f"{name} vol_src={src} want portfolio",
                rows=rows,
                warmup_ran=warmup_ran,
            )
    return _ok("exposure", {"rows": rows, "warmup_ran": warmup_ran})


def check_pipeline() -> dict:
    prod_before = STATE_FILE.read_bytes() if STATE_FILE.exists() else b""
    r = subprocess.run(
        [
            PY,
            str(ROOT / "scripts" / "run_pipeline.py"),
            "--dry-run",
            "--require-trading-day",
            "--steps",
            "pull,signal,monitor,compare,live,summary,status,today,asof,yield,brief,data,next,go,ready,digest,progress,pulse,pages",
            "--pages-out",
            str(OUTPUT_DIR / "site"),
            "--no-shadow-exec",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    prod_after = STATE_FILE.read_bytes() if STATE_FILE.exists() else b""
    if r.returncode != 0:
        return _fail(
            "pipeline",
            f"exit={r.returncode}",
            tail=(r.stdout + r.stderr)[-800:],
        )
    if prod_before != prod_after:
        return _fail("pipeline", "production STATE mutated on dry-run")
    site = OUTPUT_DIR / "site" / "index.html"
    mon = OUTPUT_DIR / "shadow_monitor.txt"
    st = OUTPUT_DIR / "research_status.txt"
    cmp_txt = OUTPUT_DIR / "shadow_compare.txt"
    live_txt = OUTPUT_DIR / "shadow_live.txt"
    sum_txt = OUTPUT_DIR / "shadow_summary.txt"
    if not site.exists() or not mon.exists():
        return _fail("pipeline", "site/monitor artifacts missing")
    if not st.exists() and not (OUTPUT_DIR / "site" / "status.html").exists():
        return _fail("pipeline", "status artifacts missing")
    if not cmp_txt.exists() and not (OUTPUT_DIR / "site" / "compare.html").exists():
        return _fail("pipeline", "compare artifacts missing")
    if not live_txt.exists() and not (
        OUTPUT_DIR / "risk_audit" / "shadow_live.json"
    ).exists():
        return _fail("pipeline", "live artifacts missing")
    if not sum_txt.exists() and not (
        OUTPUT_DIR / "risk_audit" / "shadow_summary.json"
    ).exists():
        return _fail("pipeline", "summary artifacts missing")
    today_txt = OUTPUT_DIR / "today.txt"
    today_json = OUTPUT_DIR / "risk_audit" / "today.json"
    if not today_txt.exists() and not today_json.exists():
        return _fail("pipeline", "today artifacts missing")
    asof_txt = OUTPUT_DIR / "asof.txt"
    asof_json = OUTPUT_DIR / "risk_audit" / "asof.json"
    if not asof_txt.exists() and not asof_json.exists():
        return _fail("pipeline", "asof artifacts missing")
    yield_txt = OUTPUT_DIR / "yield.txt"
    yield_json = OUTPUT_DIR / "risk_audit" / "yield.json"
    if not yield_txt.exists() and not yield_json.exists():
        return _fail("pipeline", "yield artifacts missing")
    brief_txt = OUTPUT_DIR / "brief.txt"
    brief_json = OUTPUT_DIR / "risk_audit" / "brief.json"
    if not brief_txt.exists() and not brief_json.exists():
        return _fail("pipeline", "brief artifacts missing")
    data_txt = OUTPUT_DIR / "data_status.txt"
    data_json = OUTPUT_DIR / "risk_audit" / "data_status.json"
    if not data_txt.exists() and not data_json.exists():
        return _fail("pipeline", "data_status artifacts missing")
    next_txt = OUTPUT_DIR / "next.txt"
    next_json = OUTPUT_DIR / "risk_audit" / "next.json"
    if not next_txt.exists() and not next_json.exists():
        return _fail("pipeline", "next artifacts missing")
    pull_txt = OUTPUT_DIR / "pull.txt"
    pull_json = OUTPUT_DIR / "risk_audit" / "pull.json"
    if not pull_txt.exists() and not pull_json.exists():
        return _fail("pipeline", "pull artifacts missing")
    go_txt = OUTPUT_DIR / "go.txt"
    go_json = OUTPUT_DIR / "risk_audit" / "go.json"
    if not go_txt.exists() and not go_json.exists():
        return _fail("pipeline", "go artifacts missing")
    ready_txt = OUTPUT_DIR / "ready.txt"
    ready_json = OUTPUT_DIR / "risk_audit" / "ready.json"
    if not ready_txt.exists() and not ready_json.exists():
        return _fail("pipeline", "ready artifacts missing")
    digest_txt = OUTPUT_DIR / "digest.txt"
    digest_json = OUTPUT_DIR / "risk_audit" / "digest.json"
    if not digest_txt.exists() and not digest_json.exists():
        return _fail("pipeline", "digest artifacts missing")
    prog_txt = OUTPUT_DIR / "progress.txt"
    prog_jsonl = OUTPUT_DIR / "risk_audit" / "progress.jsonl"
    if not prog_txt.exists() and not prog_jsonl.exists():
        return _fail("pipeline", "progress artifacts missing")
    pulse_txt = OUTPUT_DIR / "pulse.txt"
    pulse_json = OUTPUT_DIR / "risk_audit" / "pulse.json"
    if not pulse_txt.exists() and not pulse_json.exists():
        return _fail("pipeline", "pulse artifacts missing")
    last = OUTPUT_DIR / "risk_audit" / "pipeline_last.json"
    meta = json.loads(last.read_text(encoding="utf-8")) if last.exists() else {}
    return _ok(
        "pipeline",
        {
            "pipeline_ok": meta.get("ok"),
            "trading_day": meta.get("trading_day"),
            "data_asof": meta.get("data_asof"),
            "data_lag": meta.get("data_lag"),
            "results": [
                {
                    "step": x.get("step"),
                    "ok": x.get("ok"),
                    "skipped": x.get("skipped"),
                }
                for x in (meta.get("results") or [])
            ],
        },
    )


def check_long_anchor() -> dict:
    from etf_rotation import config as cfgmod
    from etf_rotation.backtest import bt
    from etf_rotation.research_mainline import extra_codes_for_strategies
    from etf_rotation.search_data import load_pool_data

    extras = extra_codes_for_strategies(list(LONG_GATES.keys()))
    data, bench, sd = load_pool_data(
        "pool_long_proxy", 3200, "none", extra_codes=extras
    )
    rows = []
    for name, gate in LONG_GATES.items():
        p = cfgmod.strategy_for_backtest(cfgmod.load_strategy(name))
        p["ps"] = p.get("ps", 0.95)
        r = bt(data, p, commission=0.0003)
        if not r:
            return _fail("long_anchor", f"{name} no bt result")
        sh, dd = float(r["sharpe"]), abs(float(r["dd"]))
        ok = sh >= gate["min_sharpe"] and dd <= gate["max_dd"]
        rows.append(
            {
                "name": name,
                "sharpe": sh,
                "dd": r["dd"],
                "ann": r["ann"],
                "min_sharpe": gate["min_sharpe"],
                "max_dd": gate["max_dd"],
                "ok": ok,
            }
        )
        if not ok:
            return _fail("long_anchor", f"{name} gate fail sh={sh:.2f} dd={dd:.3f}", rows=rows)
    return _ok("long_anchor", {"bench": bench, "range": f"{sd[0]}~{sd[-1]}", "rows": rows})


def check_monitor_alerts() -> dict:
    """影子监控 error 级告警必须为 0."""
    r = subprocess.run(
        [
            PY,
            str(ROOT / "scripts" / "shadow_monitor.py"),
            "--shadows",
            ",".join(MAINLINE),
            "--fail-on-alert",
            "--text-out",
            str(OUTPUT_DIR / "shadow_monitor.txt"),
            "--json-out",
            str(OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    mon_path = OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"
    err_n = warn_n = 0
    rows = []
    if mon_path.exists():
        try:
            mon = json.loads(mon_path.read_text(encoding="utf-8"))
            err_n = int(mon.get("alert_error_n") or 0)
            warn_n = int(mon.get("alert_warn_n") or 0)
            rows = [
                {
                    "name": x.get("name"),
                    "vol_src": x.get("vol_src"),
                    "n_port_rets": x.get("n_port_rets"),
                    "alert_error_n": x.get("alert_error_n"),
                    "alert_warn_n": x.get("alert_warn_n"),
                }
                for x in (mon.get("rows") or [])
            ]
        except Exception as e:
            return _fail("monitor_alerts", f"parse monitor json: {e}", exit=r.returncode)
    if r.returncode != 0:
        return _fail(
            "monitor_alerts",
            f"exit={r.returncode} error_n={err_n}",
            exit=r.returncode,
            alert_error_n=err_n,
            alert_warn_n=warn_n,
            rows=rows,
            tail=(r.stdout + r.stderr)[-600:],
        )
    return _ok(
        "monitor_alerts",
        {"alert_error_n": err_n, "alert_warn_n": warn_n, "rows": rows},
    )



def check_data_asof() -> dict:
    """行情截至 vs wall 日; DATA_LAG 仅报告不失败."""
    from etf_rotation.calendar_util import resolve_trading_day
    from etf_rotation.paths import LATEST_JSON

    td = resolve_trading_day()
    market_asof = None
    if LATEST_JSON.exists():
        try:
            lj = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
            if isinstance(lj, dict):
                market_asof = lj.get("market_asof")
        except Exception:
            market_asof = None
    detail = {
        "date": td.get("date"),
        "is_trading_day": td.get("is_trading_day"),
        "source": td.get("source"),
        "data_asof": td.get("data_asof") or market_asof,
        "data_lag": bool(td.get("data_lag")),
        "latest_market_asof": market_asof,
    }
    # 永不因 lag 失败: 数据未到是常态 (盘前/缓存滞后)
    return _ok("data_asof", detail)


def check_live() -> dict:
    """live 产物与 SIGNAL 超额字段软检 (缺失则现算; 不因 xs=0 失败).

    同时要求 latest.signal_live 存在 (shadow_live 会回写); 缺则再跑一次 live.
    """
    from etf_rotation.research_mainline import SIGNAL_SHADOW
    from etf_rotation.paths import LATEST_JSON, LATEST_TXT

    live_json = OUTPUT_DIR / "risk_audit" / "shadow_live.json"
    live_txt = OUTPUT_DIR / "shadow_live.txt"

    def _has_signal_live() -> bool:
        if not LATEST_JSON.exists():
            return False
        try:
            lj = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
            sl = lj.get("signal_live") if isinstance(lj, dict) else None
            return isinstance(sl, dict) and (
                sl.get("live_return_pct") is not None
                or sl.get("live_excess_pct") is not None
            )
        except Exception:
            return False

    need_run = (not live_json.exists()) or (not _has_signal_live())
    if need_run:
        r = subprocess.run(
            [
                PY,
                str(ROOT / "scripts" / "shadow_live.py"),
                "--json-out",
                str(live_json),
                "--text-out",
                str(live_txt),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or not live_json.exists():
            return _fail(
                "live",
                f"shadow_live failed exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-600:],
            )
    try:
        rows = json.loads(live_json.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("live", f"bad json: {e}")
    if not isinstance(rows, list) or not rows:
        return _fail("live", "empty shadow_live.json")
    by_name = {
        str(r.get("name")): r
        for r in rows
        if isinstance(r, dict) and r.get("name") and r.get("exists") is not False
    }
    missing = [n for n in MAINLINE if n not in by_name]
    if missing:
        return _fail("live", f"mainline missing: {missing}", names=list(by_name))
    sig = by_name.get(SIGNAL_SHADOW) or next(
        (r for r in by_name.values() if r.get("signal")), None
    )
    if not sig:
        return _fail("live", "SIGNAL row missing", names=list(by_name))
    # 字段存在即可; 0 合法 (空仓/start=末交易日); THIN 仅报告不失败
    if "live_return_pct" not in sig or "live_excess_pct" not in sig:
        return _fail(
            "live",
            "SIGNAL missing live/excess fields",
            signal=sig.get("name"),
            keys=list(sig.keys()),
        )
    days = sig.get("days_live")
    if days is None:
        days = sig.get("live_n_rets")
    thin = sig.get("thin_live")
    if thin is None and days is not None:
        try:
            thin = int(days) < 5
        except Exception:
            thin = None

    # latest.signal_live + latest.txt 有效收益块 (软提示, 不失败)
    has_sl = _has_signal_live()
    txt_block = False
    if LATEST_TXT.exists():
        try:
            txt_block = "有效收益 (SIGNAL live)" in LATEST_TXT.read_text(encoding="utf-8")
        except Exception:
            txt_block = False
    if not has_sl:
        # 再补一次; 仍无则 fail (产物链路断)
        r2 = subprocess.run(
            [
                PY,
                str(ROOT / "scripts" / "shadow_live.py"),
                "--json-out",
                str(live_json),
                "--text-out",
                str(live_txt),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        has_sl = _has_signal_live()
        if not has_sl:
            return _fail(
                "live",
                "latest.signal_live missing after shadow_live",
                exit=r2.returncode,
            )
        if LATEST_TXT.exists():
            try:
                txt_block = "有效收益 (SIGNAL live)" in LATEST_TXT.read_text(
                    encoding="utf-8"
                )
            except Exception:
                pass

    # asof / lag (与 signal_live 对齐)
    market_asof = None
    data_lag = None
    try:
        from etf_rotation.calendar_util import resolve_trading_day
        from etf_rotation.paths import LATEST_JSON as _LJ

        td = resolve_trading_day()
        market_asof = td.get("data_asof")
        data_lag = bool(td.get("data_lag"))
        if _LJ.exists():
            lj = json.loads(_LJ.read_text(encoding="utf-8"))
            if isinstance(lj, dict):
                market_asof = lj.get("market_asof") or market_asof
                sl0 = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else {}
                if sl0.get("data_lag") is not None:
                    data_lag = bool(sl0.get("data_lag"))
    except Exception:
        pass

    return _ok(
        "live",
        {
            "n": len(by_name),
            "signal": sig.get("name"),
            "live_return_pct": sig.get("live_return_pct"),
            "bench_return_pct": sig.get("bench_return_pct"),
            "live_excess_pct": sig.get("live_excess_pct"),
            "live_start": sig.get("live_start"),
            "days_live": days,
            "thin_live": thin,
            "market_asof": market_asof,
            "data_lag": data_lag,
            "latest_signal_live": has_sl,
            "latest_txt_live_block": txt_block,
        },
    )


def check_today() -> dict:
    """今日速览产物软检: 缺则现算; 不因 THIN/xs=0 失败."""
    today_txt = OUTPUT_DIR / "today.txt"
    today_json = OUTPUT_DIR / "risk_audit" / "today.json"
    if not today_txt.exists() and not today_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "today"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 and not today_txt.exists() and not today_json.exists():
            return _fail(
                "today",
                f"etf today failed exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-600:],
            )
    if not today_txt.exists() and not today_json.exists():
        return _fail("today", "missing today.txt / risk_audit/today.json")

    payload: dict = {}
    if today_json.exists():
        try:
            raw = json.loads(today_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
        except Exception as e:
            return _fail("today", f"bad today.json: {e}")

    sig = payload.get("signal_live") if isinstance(payload.get("signal_live"), dict) else {}
    # 站点页可选; 有则记入 detail
    site_today = (OUTPUT_DIR / "site" / "today.html").exists()
    detail = {
        "txt": today_txt.exists(),
        "json": today_json.exists(),
        "site_today": site_today,
        "signal_shadow": payload.get("signal_shadow"),
        "latest_action": payload.get("latest_action"),
        "market_ok": payload.get("market_ok"),
    }
    if sig:
        days = sig.get("days_live")
        if days is None:
            days = sig.get("live_n_rets")
        thin = sig.get("thin_live")
        if thin is None and days is not None:
            try:
                thin = int(days) < 5
            except Exception:
                thin = None
        detail.update(
            {
                "live_return_pct": sig.get("live_return_pct"),
                "live_excess_pct": sig.get("live_excess_pct"),
                "days_live": days,
                "thin_live": thin,
            }
        )
    return _ok("today", detail)




def check_etf_soft() -> dict:
    """主线 ETF 宽松档不得不合格 (观察可过, 与 promote 软门一致)."""
    out = OUTPUT_DIR / "risk_audit" / "health_etf_soft.json"
    r = subprocess.run(
        [
            PY,
            str(ROOT / "scripts" / "validate_robust.py"),
            "--preset",
            "etf_soft",
            "--strategies",
            ",".join(MAINLINE),
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if not out.exists():
        return _fail(
            "etf_soft",
            f"no report exit={r.returncode}",
            tail=(r.stdout + r.stderr)[-600:],
        )
    try:
        rep = json.loads(out.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("etf_soft", f"parse: {e}")
    rows = []
    bad = []
    for name in MAINLINE:
        rec = (rep.get("strategies") or {}).get(name) or {}
        g = rec.get("grade") or {}
        st = g.get("status")
        full = rec.get("full") or {}
        row = {
            "name": name,
            "status": st,
            "pass_n": g.get("pass_n"),
            "total": g.get("total"),
            "sharpe": full.get("sharpe"),
            "dd": full.get("dd"),
            "hard_fail": g.get("hard_fail"),
        }
        rows.append(row)
        if not rec:
            bad.append(f"{name}:missing")
        elif st == "不合格":
            bad.append(name)
    if bad:
        return _fail("etf_soft", f"不合格/缺失: {bad}", rows=rows, exit=r.returncode)
    return _ok("etf_soft", {"rows": rows, "exit": r.returncode})



def check_asof() -> dict:
    """行情/收益取证产物; 缺则现算; DATA_LAG/THIN 不失败."""
    asof_txt = OUTPUT_DIR / "asof.txt"
    asof_json = OUTPUT_DIR / "risk_audit" / "asof.json"
    if not asof_txt.exists() and not asof_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "asof"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 and not asof_txt.exists() and not asof_json.exists():
            return _fail(
                "asof",
                f"exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-400:],
            )
    detail = {
        "txt": asof_txt.exists(),
        "json": asof_json.exists(),
    }
    lag = None
    asof = None
    try:
        if asof_json.exists():
            obj = json.loads(asof_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                asof = obj.get("market_asof")
                lag = obj.get("data_lag")
                detail["market_asof"] = asof
                detail["data_lag"] = lag
                sl = obj.get("signal_live") if isinstance(obj.get("signal_live"), dict) else {}
                if sl:
                    detail["live_return_pct"] = sl.get("live_return_pct")
                    detail["live_excess_pct"] = sl.get("live_excess_pct")
                    detail["days_live"] = sl.get("days_live")
                    detail["thin_live"] = sl.get("thin_live")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "DATA_LAG/THIN 仅报告" if lag or detail.get("thin_live") else "ok"
    return _ok("asof", detail)



def check_yield() -> dict:
    """有效收益产物; 缺则现算; THIN/DATA_LAG/xs=0 不失败."""
    yield_txt = OUTPUT_DIR / "yield.txt"
    yield_json = OUTPUT_DIR / "risk_audit" / "yield.json"
    if not yield_txt.exists() and not yield_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "yield"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 and not yield_txt.exists() and not yield_json.exists():
            return _fail(
                "yield",
                f"exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-400:],
            )
    detail = {"txt": yield_txt.exists(), "json": yield_json.exists()}
    try:
        if yield_json.exists():
            obj = json.loads(yield_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["market_asof"] = obj.get("market_asof")
                detail["data_lag"] = obj.get("data_lag")
                detail["live_return_pct"] = obj.get("live_return_pct")
                detail["live_excess_pct"] = obj.get("live_excess_pct")
                detail["days_live"] = obj.get("days_live")
                detail["thin_live"] = obj.get("thin_live")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "THIN/DATA_LAG 仅报告"
    return _ok("yield", detail)



def check_brief() -> dict:
    """三合一速览产物; 缺则现算; THIN/DATA_LAG 不失败."""
    brief_txt = OUTPUT_DIR / "brief.txt"
    brief_json = OUTPUT_DIR / "risk_audit" / "brief.json"
    if not brief_txt.exists() and not brief_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "brief"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 and not brief_txt.exists() and not brief_json.exists():
            return _fail(
                "brief",
                f"exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-400:],
            )
    detail = {"txt": brief_txt.exists(), "json": brief_json.exists()}
    try:
        if brief_json.exists():
            obj = json.loads(brief_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["market_asof"] = obj.get("market_asof")
                detail["data_lag"] = obj.get("data_lag")
                sl = obj.get("signal_live") if isinstance(obj.get("signal_live"), dict) else {}
                if sl:
                    detail["live_return_pct"] = sl.get("live_return_pct")
                    detail["live_excess_pct"] = sl.get("live_excess_pct")
                    detail["days_live"] = sl.get("days_live")
                    detail["thin_live"] = sl.get("thin_live")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "THIN/DATA_LAG 仅报告"
    return _ok("brief", detail)



def check_data_status() -> dict:
    """行情状态产物; 缺则现算; DATA_LAG/STALE 不失败 (仅报告决策)."""
    data_txt = OUTPUT_DIR / "data_status.txt"
    data_json = OUTPUT_DIR / "risk_audit" / "data_status.json"
    if not data_txt.exists() and not data_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "data"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode not in (0, 3, 4) and not data_txt.exists() and not data_json.exists():
            return _fail(
                "data_status",
                f"exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-400:],
            )
    detail = {"txt": data_txt.exists(), "json": data_json.exists()}
    try:
        if data_json.exists():
            obj = json.loads(data_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["market_asof"] = obj.get("market_asof")
                detail["data_lag"] = obj.get("data_lag")
                detail["latest_stale"] = obj.get("latest_stale")
                detail["decision"] = obj.get("decision")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "DATA_LAG/STALE 仅报告决策, 不失败"
    return _ok("data_status", detail)



def check_next() -> dict:
    """一键下一步产物; 缺则现算; wait_data/refresh 决策不失败."""
    next_txt = OUTPUT_DIR / "next.txt"
    next_json = OUTPUT_DIR / "risk_audit" / "next.json"
    if not next_txt.exists() and not next_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "next", "--no-refresh"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        # next --exit-code not used; bare next returns 0
        if r.returncode != 0 and not next_txt.exists() and not next_json.exists():
            # try with data refresh
            r2 = subprocess.run(
                [PY, str(ROOT / "scripts" / "etf.py"), "next"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            if r2.returncode != 0 and not next_txt.exists() and not next_json.exists():
                return _fail(
                    "next",
                    f"exit={r2.returncode}",
                    tail=(r2.stdout + r2.stderr)[-400:],
                )
    detail = {"txt": next_txt.exists(), "json": next_json.exists()}
    try:
        if next_json.exists():
            obj = json.loads(next_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["decision"] = obj.get("decision")
                detail["market_asof"] = obj.get("market_asof")
                detail["data_lag"] = obj.get("data_lag")
                detail["recommend"] = obj.get("recommend")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "wait_data/refresh 仅报告, 不失败"
    return _ok("next", detail)



def check_pull() -> dict:
    """行情强刷产物; 缺则 bench-only 现算; advanced=false 不失败."""
    pull_txt = OUTPUT_DIR / "pull.txt"
    pull_json = OUTPUT_DIR / "risk_audit" / "pull.json"
    if not pull_txt.exists() and not pull_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "pull", "--bench-only"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode not in (0, 3, 5) and not pull_txt.exists() and not pull_json.exists():
            return _fail(
                "pull",
                f"exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-400:],
            )
    detail = {"txt": pull_txt.exists(), "json": pull_json.exists()}
    try:
        if pull_json.exists():
            obj = json.loads(pull_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["before"] = obj.get("before")
                detail["after"] = obj.get("after")
                detail["advanced"] = obj.get("advanced")
                detail["cleared_lag"] = obj.get("cleared_lag")
                detail["bench_last"] = obj.get("bench_last")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "asof 未推进仅报告 (源站可能未出当日K)"
    return _ok("pull", detail)



def check_go() -> dict:
    """一键闭环快照; 缺则 go --no-wait 现算; wait_data 不失败."""
    go_txt = OUTPUT_DIR / "go.txt"
    go_json = OUTPUT_DIR / "risk_audit" / "go.json"
    if not go_txt.exists() and not go_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "go", "--no-wait", "--no-pull"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        # go --no-wait may exit 3 on wait_data
        if r.returncode not in (0, 3, 4) and not go_txt.exists() and not go_json.exists():
            return _fail(
                "go",
                f"exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-400:],
            )
    detail = {"txt": go_txt.exists(), "json": go_json.exists()}
    try:
        if go_json.exists():
            obj = json.loads(go_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["decision"] = obj.get("decision")
                detail["market_asof"] = obj.get("market_asof")
                detail["data_lag"] = obj.get("data_lag")
                detail["recommend"] = obj.get("recommend")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "wait_data 仅报告"
    return _ok("go", detail)



def check_ready() -> dict:
    """有效收益可判性产物; 缺则现算; NOT_READY/THIN 不失败."""
    ready_txt = OUTPUT_DIR / "ready.txt"
    ready_json = OUTPUT_DIR / "risk_audit" / "ready.json"
    if not ready_txt.exists() and not ready_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "ready"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode not in (0, 1, 3, 4) and not ready_txt.exists() and not ready_json.exists():
            return _fail(
                "ready",
                f"exit={r.returncode}",
                tail=(r.stdout + r.stderr)[-400:],
            )
    detail = {"txt": ready_txt.exists(), "json": ready_json.exists()}
    try:
        if ready_json.exists():
            obj = json.loads(ready_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["level"] = obj.get("level")
                detail["ready"] = obj.get("ready")
                detail["market_asof"] = obj.get("market_asof")
                detail["data_lag"] = obj.get("data_lag")
                detail["days_live"] = obj.get("days_live")
                detail["thin_live"] = obj.get("thin_live")
                detail["live_return_pct"] = obj.get("live_return_pct")
                detail["live_excess_pct"] = obj.get("live_excess_pct")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "NOT_READY/THIN/WAIT_DATA 仅报告"
    return _ok("ready", detail)



def check_digest() -> dict:
    """人读摘要产物; 缺则 digest --no-refresh 现算; NOT_READY 不失败."""
    digest_txt = OUTPUT_DIR / "digest.txt"
    digest_json = OUTPUT_DIR / "risk_audit" / "digest.json"
    if not digest_txt.exists() and not digest_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "digest", "--no-refresh"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if r.returncode not in (0, 1, 3, 4) and not digest_txt.exists() and not digest_json.exists():
            # try with refresh ready
            r2 = subprocess.run(
                [PY, str(ROOT / "scripts" / "etf.py"), "digest"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            if r2.returncode not in (0, 1, 3, 4) and not digest_txt.exists() and not digest_json.exists():
                return _fail(
                    "digest",
                    f"exit={r2.returncode}",
                    tail=(r2.stdout + r2.stderr)[-400:],
                )
    detail = {"txt": digest_txt.exists(), "json": digest_json.exists()}
    try:
        if digest_json.exists():
            obj = json.loads(digest_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["level"] = obj.get("level")
                detail["decision"] = obj.get("decision")
                detail["market_asof"] = obj.get("market_asof")
                detail["live_return_pct"] = obj.get("live_return_pct")
                detail["live_excess_pct"] = obj.get("live_excess_pct")
                detail["recommend"] = obj.get("recommend")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "NOT_READY/THIN 仅报告"
    return _ok("digest", detail)



def check_progress() -> dict:
    """可判性轨迹; 缺则 progress --no-refresh; 空轨迹不失败若 ready 可补."""
    prog_txt = OUTPUT_DIR / "progress.txt"
    prog_jsonl = OUTPUT_DIR / "risk_audit" / "progress.jsonl"
    prog_latest = OUTPUT_DIR / "risk_audit" / "progress_latest.json"
    if not prog_txt.exists() and not prog_jsonl.exists() and not prog_latest.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "progress", "--no-refresh"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if not prog_jsonl.exists() and not prog_latest.exists():
            # try ready to seed
            subprocess.run(
                [PY, str(ROOT / "scripts" / "etf.py"), "ready"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [PY, str(ROOT / "scripts" / "etf.py"), "progress", "--no-refresh"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
        if not prog_jsonl.exists() and not prog_latest.exists() and not prog_txt.exists():
            return _fail("progress", "no progress artifacts after seed")
    detail = {
        "txt": prog_txt.exists(),
        "jsonl": prog_jsonl.exists(),
        "latest": prog_latest.exists(),
    }
    try:
        if prog_latest.exists():
            obj = json.loads(prog_latest.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["level"] = obj.get("level")
                detail["days_live"] = obj.get("days_live")
                detail["days_to_ready"] = obj.get("days_to_ready")
                detail["market_asof"] = obj.get("market_asof")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "轨迹用于观察 Lrets→READY"
    return _ok("progress", detail)



def check_pulse() -> dict:
    """一键脉搏; 缺则 pulse --no-refresh."""
    pulse_txt = OUTPUT_DIR / "pulse.txt"
    pulse_json = OUTPUT_DIR / "risk_audit" / "pulse.json"
    if not pulse_txt.exists() and not pulse_json.exists():
        r = subprocess.run(
            [PY, str(ROOT / "scripts" / "etf.py"), "pulse", "--no-refresh", "--quiet"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if not pulse_txt.exists() and not pulse_json.exists():
            return _fail("pulse", f"no pulse artifacts exit={r.returncode}")
    detail = {"txt": pulse_txt.exists(), "json": pulse_json.exists()}
    try:
        if pulse_json.exists():
            obj = json.loads(pulse_json.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                detail["level"] = obj.get("level")
                detail["days_to_ready"] = obj.get("days_to_ready")
                detail["eta_note"] = obj.get("eta_note")
                detail["decision"] = obj.get("decision")
    except Exception as e:
        detail["parse_error"] = str(e)
    detail["note"] = "脉搏=可判/ETA 最短读口"
    return _ok("pulse", detail)


CHECKS = {
    "holiday": check_holiday,
    "configs": check_configs,
    "exposure": check_exposure,
    "pipeline": check_pipeline,
    "monitor_alerts": check_monitor_alerts,
    "live": check_live,
    "data_asof": check_data_asof,
    "today": check_today,
    "asof": check_asof,
    "yield": check_yield,
    "brief": check_brief,
    "data_status": check_data_status,
    "next": check_next,
    "pull": check_pull,
    "go": check_go,
    "ready": check_ready,
    "digest": check_digest,
    "progress": check_progress,
    "pulse": check_pulse,
    "etf_soft": check_etf_soft,
    "long_anchor": check_long_anchor,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="研究主线健康回归")
    ap.add_argument(
        "--checks",
        default="holiday,configs,exposure,pipeline,monitor_alerts,live,data_asof,today,asof,yield,brief,data_status,next,pull,go,ready,digest,progress,pulse,etf_soft,long_anchor",
        help="逗号分隔检查名",
    )
    ap.add_argument("--quick", action="store_true", help="跳过 long_anchor 与 warmup")
    ap.add_argument("--skip-warmup", action="store_true")
    ap.add_argument("--jobs", type=int, default=1, help="并行检查数 (默认1; 重检查勿>2)")
    ap.add_argument("--out", default="output/risk_audit/research_healthcheck.json")
    args = ap.parse_args()

    ensure_dirs()
    names = [x.strip() for x in args.checks.split(",") if x.strip()]
    if args.quick:
        names = [n for n in names if n not in ("long_anchor", "etf_soft")]
        args.skip_warmup = True

    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        raise SystemExit(f"未知检查: {unknown}; 可选 {list(CHECKS)}")

    print("=" * 72)
    print(f"RESEARCH HEALTHCHECK {datetime.now().isoformat(timespec='seconds')}")
    print(f"checks={names} jobs={args.jobs}")
    print("=" * 72)

    results: list[dict] = []
    t0 = time.time()

    def run_one(n: str) -> dict:
        t1 = time.time()
        try:
            if n == "exposure":
                r = CHECKS[n](skip_warmup=args.skip_warmup)
            else:
                r = CHECKS[n]()
        except Exception as e:
            r = _fail(n, f"exception: {e}")
        r["seconds"] = round(time.time() - t1, 2)
        return r

    # long_anchor / pipeline / exposure / monitor / etf_soft 偏重, 默认串行
    heavy = {"long_anchor", "pipeline", "exposure", "monitor_alerts", "etf_soft"}
    light = [n for n in names if n not in heavy]
    heavy_list = [n for n in names if n in heavy]

    if args.jobs > 1 and light:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(run_one, n): n for n in light}
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                print(
                    f"{'OK' if r['ok'] else 'FAIL':4s} {r['check']} "
                    f"{r.get('seconds')}s {r.get('error', '')}"
                )
    else:
        for n in light:
            r = run_one(n)
            results.append(r)
            print(
                f"{'OK' if r['ok'] else 'FAIL':4s} {r['check']} "
                f"{r.get('seconds')}s {r.get('error', '')}"
            )

    for n in heavy_list:
        r = run_one(n)
        results.append(r)
        print(
            f"{'OK' if r['ok'] else 'FAIL':4s} {r['check']} "
            f"{r.get('seconds')}s {r.get('error', '')}"
        )

    # keep original order
    order = {n: i for i, n in enumerate(names)}
    results.sort(key=lambda x: order.get(x["check"], 99))

    ok_all = all(r.get("ok") for r in results)
    payload = {
        "stamp": datetime.now().isoformat(timespec="seconds"),
        "ok": ok_all,
        "seconds": round(time.time() - t0, 2),
        "mainline": MAINLINE,
        "results": results,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("=" * 72)
    print(f"HEALTHCHECK DONE ok={ok_all} {payload['seconds']}s → {out}")
    if not ok_all:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
