#!/usr/bin/env python3
"""一键体检: 环境 / 配置 / 产物 / 可用性 (只读, 不交易).

用法:
  python3 scripts/doctor.py
  python3 scripts/doctor.py --json-out output/risk_audit/doctor.json
  python3 scripts/doctor.py --strict   # 警告也失败
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod  # noqa: E402
from etf_rotation.calendar_util import resolve_trading_day  # noqa: E402
from etf_rotation.paths import (  # noqa: E402
    CONFIG_DIR,
    LATEST_JSON,
    LATEST_TXT,
    OUTPUT_DIR,
    STATE_FILE,
    ensure_dirs,
    shadow_state_file,
)
from etf_rotation.research_mainline import MONITOR_SHADOWS, SIGNAL_SHADOW  # noqa: E402

MAINLINE = list(MONITOR_SHADOWS)

def _ok(name: str, detail: str = "", **extra: Any) -> dict:
    return {"check": name, "level": "ok", "ok": True, "detail": detail, **extra}


def _warn(name: str, detail: str, **extra: Any) -> dict:
    return {"check": name, "level": "warn", "ok": True, "detail": detail, **extra}


def _fail(name: str, detail: str, **extra: Any) -> dict:
    return {"check": name, "level": "error", "ok": False, "detail": detail, **extra}


def run_checks() -> list[dict]:
    ensure_dirs()
    rows: list[dict] = []

    # python
    py = sys.version_info
    if py < (3, 10):
        rows.append(_fail("python", f"{py.major}.{py.minor} < 3.10"))
    else:
        rows.append(_ok("python", f"{py.major}.{py.minor}.{py.micro}"))

    # package import
    try:
        import etf_rotation  # noqa: F401
        from etf_rotation import factors, portfolio, signal  # noqa: F401

        rows.append(_ok("import", "etf_rotation + core modules"))
    except Exception as e:
        rows.append(_fail("import", str(e)))

    # configs
    for name in ["c01", *MAINLINE]:
        try:
            c = cfgmod.load_strategy(name)
            if name == "c01" and not c.get("frozen"):
                rows.append(_warn(f"config:{name}", "生产 c01 未标记 frozen"))
            else:
                rows.append(
                    _ok(
                        f"config:{name}",
                        f"frozen={c.get('frozen')} research={c.get('research')} vt={c.get('vol_target')}",
                    )
                )
        except Exception as e:
            rows.append(_fail(f"config:{name}", str(e)))

    # pools
    for pool in ["pool", "pool_long_proxy"]:
        try:
            p = cfgmod.load_pool(pool)
            n = len(p.get("etfs") or [])
            if n < 5:
                rows.append(_warn(f"pool:{pool}", f"etfs={n} 过少"))
            else:
                rows.append(_ok(f"pool:{pool}", f"etfs={n} bench={p.get('bench')}"))
        except Exception as e:
            rows.append(_fail(f"pool:{pool}", str(e)))

    # holidays
    hol = CONFIG_DIR / "cn_holidays.json"
    if hol.exists():
        try:
            h = json.loads(hol.read_text(encoding="utf-8"))
            closed = h.get("closed") or h.get("holidays") or []
            rows.append(_ok("holidays", f"file ok closed_n={len(closed) if hasattr(closed,'__len__') else '?'}"))
        except Exception as e:
            rows.append(_fail("holidays", f"parse: {e}"))
    else:
        rows.append(_warn("holidays", "config/cn_holidays.json 缺失 (日历回退可能不准)"))

    # trading day
    try:
        td = resolve_trading_day()
        detail = (
            f"date={td.get('date')} is_trading_day={td.get('is_trading_day')} "
            f"source={td.get('source')} asof={td.get('data_asof')}"
        )
        if td.get("data_lag"):
            detail += " DATA_LAG"
            rows.append(
                _warn(
                    "trading_day",
                    detail + " (行情未到 wall 日, nav/live 用 asof)",
                    **{k: td.get(k) for k in ("date", "is_trading_day", "source", "data_asof", "data_lag")},
                )
            )
        else:
            rows.append(
                _ok(
                    "trading_day",
                    detail,
                    **{k: td.get(k) for k in ("date", "is_trading_day", "source", "data_asof", "data_lag")},
                )
            )
    except Exception as e:
        rows.append(_fail("trading_day", str(e)))

    # runtime artifacts
    if STATE_FILE.exists():
        rows.append(_ok("prod_state", str(STATE_FILE)))
    else:
        rows.append(_warn("prod_state", f"缺失 {STATE_FILE} (首次信号后生成)"))

    if LATEST_TXT.exists():
        age_h = (datetime.now().timestamp() - LATEST_TXT.stat().st_mtime) / 3600
        detail = f"{LATEST_TXT} age={age_h:.1f}h"
        if age_h > 72:
            rows.append(_warn("latest_txt", detail + " (超过3天未更新)"))
        else:
            rows.append(_ok("latest_txt", detail))
    else:
        rows.append(_warn("latest_txt", "尚无 latest.txt"))

    if LATEST_JSON.exists():
        rows.append(_ok("latest_json", str(LATEST_JSON)))
    else:
        rows.append(_warn("latest_json", "尚无 latest.json"))

    # shadows
    for name in MAINLINE:
        p = shadow_state_file(name)
        if not p.exists():
            rows.append(_warn(f"shadow:{name}", "缺 state (可 warmup/pipeline)"))
            continue
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            n = len(st.get("port_rets") or [])
            warm = bool(st.get("warmup"))
            cash = float(st.get("cash") or 0)
            tv = float(st.get("total_value") or 0)
            aligned = abs(cash - tv) < max(1.0, abs(tv) * 1e-6) if tv else True
            live = st.get("live") if isinstance(st.get("live"), dict) else {}
            if not live:
                try:
                    from etf_rotation.portfolio import apply_live_metrics

                    apply_live_metrics(st)
                    live = st.get("live") or {}
                except Exception:
                    live = {}
            lr = live.get("return_pct")
            try:
                lr_s = f"{float(lr):+.2f}%" if lr is not None else "—"
            except Exception:
                lr_s = "—"
            detail = (
                f"rets={n} warmup={warm} nav={len(st.get('nav_history') or [])} "
                f"tv={tv:.0f} live={lr_s}"
            )
            if n < 20:
                rows.append(_warn(f"shadow:{name}", detail + " (rets 偏少)"))
            elif not aligned:
                rows.append(
                    _warn(
                        f"shadow:{name}",
                        detail + f" CASH≠NAV cash={cash:.0f} → warmup --reset",
                    )
                )
            elif warm and st.get("live_anchor_nav") is None:
                rows.append(
                    _warn(
                        f"shadow:{name}",
                        detail + " 缺 live_anchor → etf live --write-states",
                    )
                )
            else:
                rows.append(_ok(f"shadow:{name}", detail))
        except Exception as e:
            rows.append(_fail(f"shadow:{name}", str(e)))

    # site + live/today/status pages + site_meta
    site = OUTPUT_DIR / "site" / "index.html"
    if site.exists():
        rows.append(_ok("site", str(site)))
        live_page = OUTPUT_DIR / "site" / "live.html"
        if live_page.exists():
            rows.append(_ok("site_live", str(live_page)))
        else:
            rows.append(_warn("site_live", "无 live.html → etf preview"))
        today_page = OUTPUT_DIR / "site" / "today.html"
        if today_page.exists():
            rows.append(_ok("site_today", str(today_page)))
        else:
            rows.append(_warn("site_today", "无 today.html → etf today && etf pages"))
        status_page = OUTPUT_DIR / "site" / "status.html"
        if status_page.exists():
            rows.append(_ok("site_status", str(status_page)))
        else:
            rows.append(_warn("site_status", "无 status.html → etf status && etf pages"))
        meta_path = OUTPUT_DIR / "site" / "site_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                meta = {}
                rows.append(_warn("site_meta", f"parse: {e}"))
            else:
                if meta.get("has_today"):
                    detail = (
                        f"has_today=True has_live={meta.get('has_live')} "
                        f"has_status={meta.get('has_status')} "
                        f"has_asof={meta.get('has_asof')} has_yield={meta.get('has_yield')} has_brief={meta.get('has_brief')} has_data={meta.get('has_data')} has_next={meta.get('has_next')} has_pull={meta.get('has_pull')} has_go={meta.get('has_go')} has_ready={meta.get('has_ready')} has_digest={meta.get('has_digest')} has_eod={meta.get('has_eod')} "
                        f"asof={meta.get('market_asof')} lag={meta.get('data_lag')}"
                    )
                    if meta.get("data_lag"):
                        rows.append(_warn("site_meta", detail + " DATA_LAG"))
                    else:
                        rows.append(_ok("site_meta", detail))
                else:
                    rows.append(
                        _warn(
                            "site_meta",
                            "has_today=False → etf today && etf pages",
                        )
                    )
        else:
            rows.append(_warn("site_meta", "无 site_meta.json → etf pages"))
    else:
        rows.append(_warn("site", "无 output/site (python3 scripts/build_pages.py)"))

    # live artifacts + SIGNAL excess
    live_txt = OUTPUT_DIR / "shadow_live.txt"
    live_json = OUTPUT_DIR / "risk_audit" / "shadow_live.json"
    if live_txt.exists() or live_json.exists():
        detail = f"txt={live_txt.exists()} json={live_json.exists()}"
        thin = False
        try:
            if live_json.exists():
                for rr in json.loads(live_json.read_text(encoding="utf-8")) or []:
                    if isinstance(rr, dict) and (
                        rr.get("signal") or rr.get("name") == SIGNAL_SHADOW
                    ):
                        xs = rr.get("live_excess_pct")
                        lr = rr.get("live_return_pct")
                        br = rr.get("bench_return_pct")
                        dl = rr.get("days_live") or rr.get("live_n_rets")
                        detail += f" SIGNAL live={lr} xs={xs} bench={br} days={dl}"
                        if rr.get("thin_live") or (
                            dl is not None and int(dl) < 5
                        ):
                            thin = True
                        break
        except Exception:
            pass
        if thin:
            rows.append(
                _warn(
                    "live_artifacts",
                    detail + " (live 样本偏薄, 等下一交易日)",
                )
            )
        else:
            rows.append(_ok("live_artifacts", detail))
    else:
        rows.append(_warn("live_artifacts", "无 shadow_live → ./etf live"))

    # latest.signal_live (live 回写后的顶层快读)
    latest_path = OUTPUT_DIR / "latest.json"
    if latest_path.exists():
        try:
            lj = json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception as e:
            rows.append(_warn("latest_signal_live", f"parse: {e}"))
        else:
            sl = lj.get("signal_live") if isinstance(lj, dict) else None
            if isinstance(sl, dict) and (
                "live_return_pct" in sl or "live_excess_pct" in sl
            ):
                dl = sl.get("days_live")
                thin = sl.get("thin_live")
                if thin is None and dl is not None:
                    try:
                        thin = int(dl) < 5
                    except Exception:
                        thin = None
                detail = (
                    f"name={sl.get('name')} live={sl.get('live_return_pct')} "
                    f"xs={sl.get('live_excess_pct')} days={dl}"
                )
                asof = sl.get("market_asof")
                if asof:
                    detail += f" asof={asof}"
                if sl.get("data_lag"):
                    detail += " DATA_LAG"
                if thin:
                    detail += " THIN"
                    note = " (样本偏薄"
                    if dl is not None:
                        try:
                            if int(dl) == 0:
                                note += "; Lrets=0仅锚日"
                        except Exception:
                            pass
                    if sl.get("data_lag"):
                        note += "; 行情滞后"
                    note += ")"
                    rows.append(_warn("latest_signal_live", detail + note))
                else:
                    rows.append(_ok("latest_signal_live", detail))
            else:
                rows.append(
                    _warn(
                        "latest_signal_live",
                        "latest.json 无 signal_live → ./etf live",
                    )
                )
    else:
        rows.append(_warn("latest_signal_live", "无 latest.json"))

    # latest 时间是否落后于当前交易日
    try:
        lj2 = None
        if latest_path.exists():
            try:
                lj2 = json.loads(latest_path.read_text(encoding="utf-8"))
            except Exception:
                lj2 = None
        td = resolve_trading_day()
        td_date = str((td or {}).get("date") or "")[:10]
        lt = ""
        if isinstance(lj2, dict):
            lt = str(lj2.get("time") or "")[:10]
        if (td or {}).get("is_trading_day") and td_date and lt and lt < td_date:
            rows.append(
                _warn(
                    "latest_freshness",
                    f"信号日 {lt} < 交易日 {td_date} → ./etf refresh",
                )
            )
        elif lt:
            rows.append(_ok("latest_freshness", f"信号日 {lt} 交易日 {td_date or '—'}"))
        elif not latest_path.exists():
            rows.append(_warn("latest_freshness", "无 latest.json"))
    except Exception as e:
        rows.append(_warn("latest_freshness", f"check: {e}"))

    # latest.txt 有效收益块 (live 回写)
    latest_txt = OUTPUT_DIR / "latest.txt"
    if latest_txt.exists():
        try:
            raw = latest_txt.read_text(encoding="utf-8")
        except Exception as e:
            rows.append(_warn("latest_txt_live", f"read: {e}"))
        else:
            if "有效收益 (SIGNAL live)" in raw and "live=" in raw:
                thin_txt = "THIN" in raw.split("有效收益 (SIGNAL live)", 1)[-1][:400]
                detail = "latest.txt 含 SIGNAL live 块"
                if thin_txt:
                    rows.append(_warn("latest_txt_live", detail + " · THIN"))
                else:
                    rows.append(_ok("latest_txt_live", detail))
            else:
                rows.append(
                    _warn(
                        "latest_txt_live",
                        "latest.txt 无有效收益块 → ./etf live",
                    )
                )
    else:
        rows.append(_warn("latest_txt_live", "无 latest.txt"))

    # summary artifacts
    sum_txt = OUTPUT_DIR / "shadow_summary.txt"
    sum_json = OUTPUT_DIR / "risk_audit" / "shadow_summary.json"
    if sum_txt.exists() or sum_json.exists():
        rows.append(
            _ok(
                "summary_artifacts",
                f"txt={sum_txt.exists()} json={sum_json.exists()}",
            )
        )
    else:
        rows.append(
            _warn("summary_artifacts", "无 shadow_summary → ./etf summary")
        )

    # monitor artifacts
    mon_txt = OUTPUT_DIR / "shadow_monitor.txt"
    mon_json = OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"
    if mon_txt.exists() or mon_json.exists():
        rows.append(
            _ok(
                "monitor_artifacts",
                f"txt={mon_txt.exists()} json={mon_json.exists()}",
            )
        )
    else:
        rows.append(
            _warn("monitor_artifacts", "无 shadow_monitor → ./etf monitor")
        )

    # today brief artifacts (可选)
    today_txt = OUTPUT_DIR / "today.txt"
    today_json = OUTPUT_DIR / "risk_audit" / "today.json"
    if today_txt.exists() or today_json.exists():
        rows.append(
            _ok(
                "today_artifacts",
                f"txt={today_txt.exists()} json={today_json.exists()}",
            )
        )
    else:
        rows.append(
            _warn("today_artifacts", "无 today → ./etf today")
        )

    # asof 取证产物 (可选)
    asof_txt = OUTPUT_DIR / "asof.txt"
    asof_json = OUTPUT_DIR / "risk_audit" / "asof.json"
    if asof_txt.exists() or asof_json.exists():
        detail = f"txt={asof_txt.exists()} json={asof_json.exists()}"
        try:
            if asof_json.exists():
                aj = json.loads(asof_json.read_text(encoding="utf-8"))
                if isinstance(aj, dict):
                    detail += f" asof={aj.get('market_asof')} lag={aj.get('data_lag')}"
                    if aj.get("data_lag"):
                        rows.append(_warn("asof_artifacts", detail + " DATA_LAG"))
                    else:
                        rows.append(_ok("asof_artifacts", detail))
                else:
                    rows.append(_ok("asof_artifacts", detail))
            else:
                rows.append(_ok("asof_artifacts", detail))
        except Exception as e:
            rows.append(_warn("asof_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("asof_artifacts", "无 asof → ./etf asof")
        )

    
    # yield 有效收益产物 (可选)
    yield_txt = OUTPUT_DIR / "yield.txt"
    yield_json = OUTPUT_DIR / "risk_audit" / "yield.json"
    if yield_txt.exists() or yield_json.exists():
        detail = f"txt={yield_txt.exists()} json={yield_json.exists()}"
        try:
            if yield_json.exists():
                yj = json.loads(yield_json.read_text(encoding="utf-8"))
                if isinstance(yj, dict):
                    detail += (
                        f" live={yj.get('live_return_pct')} xs={yj.get('live_excess_pct')} "
                        f"Lrets={yj.get('days_live')} asof={yj.get('market_asof')}"
                    )
                    thin = yj.get("thin_live")
                    lag = yj.get("data_lag")
                    if thin or lag:
                        tag = []
                        if thin:
                            tag.append("THIN")
                        if lag:
                            tag.append("DATA_LAG")
                        rows.append(_warn("yield_artifacts", detail + " " + " ".join(tag)))
                    else:
                        rows.append(_ok("yield_artifacts", detail))
                else:
                    rows.append(_ok("yield_artifacts", detail))
            else:
                rows.append(_ok("yield_artifacts", detail))
        except Exception as e:
            rows.append(_warn("yield_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("yield_artifacts", "无 yield → ./etf yield")
        )

# warmup artifacts (可选; 有则 OK)
    warm_json = OUTPUT_DIR / "risk_audit" / "shadow_warmup.json"
    if warm_json.exists():
        try:
            wj = json.loads(warm_json.read_text(encoding="utf-8"))
            n_ok = sum(1 for r in (wj.get("rows") or []) if r.get("ok"))
            rows.append(
                _ok(
                    "warmup_artifacts",
                    f"json=True ok_rows={n_ok} prod_untouched={wj.get('prod_untouched')}",
                )
            )
        except Exception as e:
            rows.append(_warn("warmup_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn(
                "warmup_artifacts",
                "无 shadow_warmup.json → ./etf warmup --tail 120",
            )
        )
    # action history (+ 最近带 signal_live 的一日)
    ah = OUTPUT_DIR / "action_history.jsonl"
    if ah.exists():
        raw_lines = [ln for ln in ah.read_text(encoding="utf-8").splitlines() if ln.strip()]
        n = len(raw_lines)
        last_sl = None
        last_day = None
        try:
            ah_rows = []
            for ln in raw_lines:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if isinstance(r, dict):
                    ah_rows.append(r)
            ah_rows.sort(key=lambda r: str(r.get("date") or ""))
            for r in reversed(ah_rows):
                sl = r.get("signal_live")
                if isinstance(sl, dict) and (
                    sl.get("live_return_pct") is not None
                    or sl.get("live_excess_pct") is not None
                ):
                    last_sl = sl
                    last_day = r.get("date")
                    break
            if last_day is None and ah_rows:
                last_day = ah_rows[-1].get("date")
        except Exception:
            last_sl = None
        if isinstance(last_sl, dict):
            thin = last_sl.get("thin_live")
            detail = (
                f"lines={n} last={last_day} live={last_sl.get('live_return_pct')} "
                f"xs={last_sl.get('live_excess_pct')}"
            )
            if thin or (
                last_sl.get("days_live") is not None
                and int(last_sl.get("days_live") or 0) < 5
            ):
                rows.append(_warn("action_history", detail + " THIN"))
            else:
                rows.append(_ok("action_history", detail))
        else:
            rows.append(
                _warn(
                    "action_history",
                    f"lines={n} 无 signal_live → ./etf live",
                )
            )
    else:
        rows.append(_warn("action_history", "尚无 (下次信号写入 latest 后生成)"))
    for rel in [
        "scripts/run_pipeline.py",
        "scripts/run_daily.sh",
        "scripts/build_pages.py",
        "scripts/send_email.py",
        "scripts/research_status.py",
        "scripts/research_healthcheck.py",
        "scripts/shadow_compare.py",
        "scripts/shadow_live.py",
        "scripts/shadow_summary.py",
        "scripts/shadow_monitor.py",
        "scripts/shadow_warmup.py",
        "scripts/etf.py",
    ]:
        p = ROOT / rel
        if p.exists():
            rows.append(_ok(f"script:{rel}", "present"))
        else:
            rows.append(_fail(f"script:{rel}", "missing"))


    # brief 三合一产物 (可选)
    brief_txt = OUTPUT_DIR / "brief.txt"
    brief_json = OUTPUT_DIR / "risk_audit" / "brief.json"
    if brief_txt.exists() or brief_json.exists():
        detail = f"txt={brief_txt.exists()} json={brief_json.exists()}"
        try:
            if brief_json.exists():
                bj = json.loads(brief_json.read_text(encoding="utf-8"))
                if isinstance(bj, dict):
                    detail += f" asof={bj.get('market_asof')} lag={bj.get('data_lag')}"
                    slb = bj.get("signal_live") if isinstance(bj.get("signal_live"), dict) else {}
                    if slb:
                        detail += (
                            f" live={slb.get('live_return_pct')} xs={slb.get('live_excess_pct')} "
                            f"Lrets={slb.get('days_live')}"
                        )
                    if bj.get("data_lag") or (slb and slb.get("thin_live")):
                        tag = []
                        if bj.get("data_lag"):
                            tag.append("DATA_LAG")
                        if slb and slb.get("thin_live"):
                            tag.append("THIN")
                        rows.append(_warn("brief_artifacts", detail + " " + " ".join(tag)))
                    else:
                        rows.append(_ok("brief_artifacts", detail))
                else:
                    rows.append(_ok("brief_artifacts", detail))
            else:
                rows.append(_ok("brief_artifacts", detail))
        except Exception as e:
            rows.append(_warn("brief_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("brief_artifacts", "无 brief → ./etf brief")
        )


    # data_status 行情决策产物 (可选)
    data_txt = OUTPUT_DIR / "data_status.txt"
    data_json = OUTPUT_DIR / "risk_audit" / "data_status.json"
    if data_txt.exists() or data_json.exists():
        detail = f"txt={data_txt.exists()} json={data_json.exists()}"
        try:
            if data_json.exists():
                dj = json.loads(data_json.read_text(encoding="utf-8"))
                if isinstance(dj, dict):
                    detail += (
                        f" asof={dj.get('market_asof')} lag={dj.get('data_lag')} "
                        f"stale={dj.get('latest_stale')} decision={dj.get('decision')}"
                    )
                    if dj.get("data_lag") or dj.get("latest_stale"):
                        tag = []
                        if dj.get("data_lag"):
                            tag.append("DATA_LAG")
                        if dj.get("latest_stale"):
                            tag.append("STALE")
                        rows.append(_warn("data_status_artifacts", detail + " " + " ".join(tag)))
                    else:
                        rows.append(_ok("data_status_artifacts", detail))
                else:
                    rows.append(_ok("data_status_artifacts", detail))
            else:
                rows.append(_ok("data_status_artifacts", detail))
        except Exception as e:
            rows.append(_warn("data_status_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("data_status_artifacts", "无 data_status → ./etf data")
        )


    # next 决策产物 (可选)
    next_txt = OUTPUT_DIR / "next.txt"
    next_json = OUTPUT_DIR / "risk_audit" / "next.json"
    if next_txt.exists() or next_json.exists():
        detail = f"txt={next_txt.exists()} json={next_json.exists()}"
        try:
            if next_json.exists():
                nj = json.loads(next_json.read_text(encoding="utf-8"))
                if isinstance(nj, dict):
                    detail += (
                        f" decision={nj.get('decision')} asof={nj.get('market_asof')} "
                        f"lag={nj.get('data_lag')}"
                    )
                    if nj.get("decision") in ("wait_data", "refresh") or nj.get("data_lag") or nj.get("latest_stale"):
                        tag = []
                        if nj.get("decision") == "wait_data" or nj.get("data_lag"):
                            tag.append("WAIT_DATA" if nj.get("decision") == "wait_data" else "DATA_LAG")
                        if nj.get("decision") == "refresh" or nj.get("latest_stale"):
                            tag.append("STALE")
                        rows.append(_warn("next_artifacts", detail + " " + " ".join(tag)))
                    else:
                        rows.append(_ok("next_artifacts", detail))
                else:
                    rows.append(_ok("next_artifacts", detail))
            else:
                rows.append(_ok("next_artifacts", detail))
        except Exception as e:
            rows.append(_warn("next_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("next_artifacts", "无 next → ./etf next")
        )


    # pull 强刷产物 (可选)
    pull_txt = OUTPUT_DIR / "pull.txt"
    pull_json = OUTPUT_DIR / "risk_audit" / "pull.json"
    if pull_txt.exists() or pull_json.exists():
        detail = f"txt={pull_txt.exists()} json={pull_json.exists()}"
        try:
            if pull_json.exists():
                pj = json.loads(pull_json.read_text(encoding="utf-8"))
                if isinstance(pj, dict):
                    after = pj.get("after") if isinstance(pj.get("after"), dict) else {}
                    detail += (
                        f" asof={after.get('data_asof')} lag={after.get('data_lag')} "
                        f"advanced={pj.get('advanced')}"
                    )
                    if after.get("data_lag") or not pj.get("advanced"):
                        tag = []
                        if after.get("data_lag"):
                            tag.append("DATA_LAG")
                        if not pj.get("advanced"):
                            tag.append("NO_ADVANCE")
                        rows.append(_warn("pull_artifacts", detail + " " + " ".join(tag)))
                    else:
                        rows.append(_ok("pull_artifacts", detail))
                else:
                    rows.append(_ok("pull_artifacts", detail))
            else:
                rows.append(_ok("pull_artifacts", detail))
        except Exception as e:
            rows.append(_warn("pull_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("pull_artifacts", "无 pull → ./etf pull --bench-only")
        )


    # go 一键闭环产物 (可选)
    go_txt = OUTPUT_DIR / "go.txt"
    go_json = OUTPUT_DIR / "risk_audit" / "go.json"
    if go_txt.exists() or go_json.exists():
        detail = f"txt={go_txt.exists()} json={go_json.exists()}"
        try:
            if go_json.exists():
                gj = json.loads(go_json.read_text(encoding="utf-8"))
                if isinstance(gj, dict):
                    detail += (
                        f" decision={gj.get('decision')} asof={gj.get('market_asof')} "
                        f"did_wait={gj.get('did_wait')}"
                    )
                    if gj.get("data_lag") or gj.get("decision") == "wait_data":
                        rows.append(_warn("go_artifacts", detail + " WAIT_DATA"))
                    else:
                        rows.append(_ok("go_artifacts", detail))
                else:
                    rows.append(_ok("go_artifacts", detail))
            else:
                rows.append(_ok("go_artifacts", detail))
        except Exception as e:
            rows.append(_warn("go_artifacts", f"parse: {e}"))


    # ready 可判性产物
    ready_txt = OUTPUT_DIR / "ready.txt"
    ready_json = OUTPUT_DIR / "risk_audit" / "ready.json"
    if ready_txt.exists() or ready_json.exists():
        detail = f"txt={ready_txt.exists()} json={ready_json.exists()}"
        try:
            if ready_json.exists():
                rj = json.loads(ready_json.read_text(encoding="utf-8"))
                if isinstance(rj, dict):
                    detail += (
                        f" level={rj.get('level')} asof={rj.get('market_asof')} "
                        f"Lrets={rj.get('days_live')} live={rj.get('live_return_pct')} xs={rj.get('live_excess_pct')}"
                    )
                    if not rj.get("ready"):
                        rows.append(_warn("ready_artifacts", detail + f" {rj.get('level') or 'NOT_READY'}"))
                    else:
                        rows.append(_ok("ready_artifacts", detail))
                else:
                    rows.append(_ok("ready_artifacts", detail))
            else:
                rows.append(_ok("ready_artifacts", detail))
        except Exception as e:
            rows.append(_warn("ready_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("ready_artifacts", "无 ready → ./etf ready")
        )

    # digest 人读摘要产物
    digest_txt = OUTPUT_DIR / "digest.txt"
    digest_json = OUTPUT_DIR / "risk_audit" / "digest.json"
    if digest_txt.exists() or digest_json.exists():
        detail = f"txt={digest_txt.exists()} json={digest_json.exists()}"
        try:
            if digest_json.exists():
                dj = json.loads(digest_json.read_text(encoding="utf-8"))
                if isinstance(dj, dict):
                    detail += (
                        f" level={dj.get('level')} decision={dj.get('decision')} "
                        f"asof={dj.get('market_asof')} live={dj.get('live_return_pct')} xs={dj.get('live_excess_pct')}"
                    )
                    if dj.get("level") and dj.get("level") != "READY":
                        rows.append(_warn("digest_artifacts", detail + f" {dj.get('level')}"))
                    else:
                        rows.append(_ok("digest_artifacts", detail))
                else:
                    rows.append(_ok("digest_artifacts", detail))
            else:
                rows.append(_ok("digest_artifacts", detail))
        except Exception as e:
            rows.append(_warn("digest_artifacts", f"parse: {e}"))
    else:
        rows.append(
            _warn("digest_artifacts", "无 digest → ./etf digest")
        )

    # eod 收盘闭环产物 (可选)
    eod_txt = OUTPUT_DIR / "eod.txt"
    eod_json = OUTPUT_DIR / "risk_audit" / "eod.json"
    if eod_txt.exists() or eod_json.exists():
        detail = f"txt={eod_txt.exists()} json={eod_json.exists()}"
        try:
            if eod_json.exists():
                ej = json.loads(eod_json.read_text(encoding="utf-8"))
                if isinstance(ej, dict):
                    detail += (
                        f" level={ej.get('level')} asof={ej.get('market_asof')} "
                        f"Lrets={ej.get('days_live')} days_to_ready={ej.get('days_to_ready')}"
                    )
                    if ej.get("level") and ej.get("level") != "READY":
                        rows.append(_warn("eod_artifacts", detail + f" {ej.get('level')}"))
                    else:
                        rows.append(_ok("eod_artifacts", detail))
                else:
                    rows.append(_ok("eod_artifacts", detail))
            else:
                rows.append(_ok("eod_artifacts", detail))
        except Exception as e:
            rows.append(_warn("eod_artifacts", f"parse: {e}"))
    # 无 eod 不强制告警 (按需收盘命令)

    # progress 可判性轨迹 (可选)
    prog_txt = OUTPUT_DIR / "progress.txt"
    prog_jsonl = OUTPUT_DIR / "risk_audit" / "progress.jsonl"
    prog_latest = OUTPUT_DIR / "risk_audit" / "progress_latest.json"
    if prog_txt.exists() or prog_jsonl.exists() or prog_latest.exists():
        detail = f"txt={prog_txt.exists()} jsonl={prog_jsonl.exists()} latest={prog_latest.exists()}"
        try:
            if prog_latest.exists():
                pj = json.loads(prog_latest.read_text(encoding="utf-8"))
                if isinstance(pj, dict):
                    detail += (
                        f" level={pj.get('level')} Lrets={pj.get('days_live')} "
                        f"dtr={pj.get('days_to_ready')} asof={pj.get('market_asof')}"
                    )
                    if pj.get("level") and pj.get("level") != "READY":
                        rows.append(_warn("progress_artifacts", detail + f" {pj.get('level')}"))
                    else:
                        rows.append(_ok("progress_artifacts", detail))
                else:
                    rows.append(_ok("progress_artifacts", detail))
            else:
                rows.append(_ok("progress_artifacts", detail))
        except Exception as e:
            rows.append(_warn("progress_artifacts", f"parse: {e}"))
    # pulse 脉搏 (可选)
    pulse_txt = OUTPUT_DIR / "pulse.txt"
    pulse_json = OUTPUT_DIR / "risk_audit" / "pulse.json"
    if pulse_txt.exists() or pulse_json.exists():
        detail = f"txt={pulse_txt.exists()} json={pulse_json.exists()}"
        try:
            if pulse_json.exists():
                pj = json.loads(pulse_json.read_text(encoding="utf-8"))
                if isinstance(pj, dict):
                    detail += (
                        f" level={pj.get('level')} dtr={pj.get('days_to_ready')} "
                        f"asof={pj.get('market_asof')}"
                    )
                    if pj.get("level") and pj.get("level") != "READY":
                        rows.append(_warn("pulse_artifacts", detail + f" {pj.get('level')}"))
                    else:
                        rows.append(_ok("pulse_artifacts", detail))
                else:
                    rows.append(_ok("pulse_artifacts", detail))
            else:
                rows.append(_ok("pulse_artifacts", detail))
        except Exception as e:
            rows.append(_warn("pulse_artifacts", f"parse: {e}"))







    # wait-asof 轮询产物 (可选)
    wait_txt = OUTPUT_DIR / "wait_asof.txt"
    wait_json = OUTPUT_DIR / "risk_audit" / "wait_asof.json"
    if wait_txt.exists() or wait_json.exists():
        detail = f"txt={wait_txt.exists()} json={wait_json.exists()}"
        try:
            if wait_json.exists():
                wj = json.loads(wait_json.read_text(encoding="utf-8"))
                if isinstance(wj, dict):
                    detail += (
                        f" ok={wj.get('ok')} advanced={wj.get('advanced')} "
                        f"attempts={wj.get('attempts')}"
                    )
                    if not wj.get("ok"):
                        rows.append(_warn("wait_asof_artifacts", detail + " NO_ADVANCE"))
                    else:
                        rows.append(_ok("wait_asof_artifacts", detail))
                else:
                    rows.append(_ok("wait_asof_artifacts", detail))
            else:
                rows.append(_ok("wait_asof_artifacts", detail))
        except Exception as e:
            rows.append(_warn("wait_asof_artifacts", f"parse: {e}"))
    # 无 wait-asof 产物不告警 (按需命令)


    # pipeline_last asof/lag (forensics)
    pipe_last = OUTPUT_DIR / "risk_audit" / "pipeline_last.json"
    if pipe_last.exists():
        try:
            pj = json.loads(pipe_last.read_text(encoding="utf-8"))
            if isinstance(pj, dict):
                detail = (
                    f"ok={pj.get('ok')} stamp={pj.get('stamp')} "
                    f"asof={pj.get('data_asof')} lag={pj.get('data_lag')}"
                )
                yv = pj.get("yield") if isinstance(pj.get("yield"), dict) else None
                if yv:
                    detail += (
                        f" live={yv.get('live_return_pct')} xs={yv.get('live_excess_pct')} "
                        f"Lrets={yv.get('days_live')}"
                    )
                bv = pj.get("brief") if isinstance(pj.get("brief"), dict) else None
                if bv:
                    detail += f" brief={bv.get('market_asof') or 'yes'}"
                nv = pj.get("next") if isinstance(pj.get("next"), dict) else None
                if nv:
                    detail += f" next={nv.get('decision') or 'yes'}"
                pv = pj.get("pull") if isinstance(pj.get("pull"), dict) else None
                if pv:
                    detail += f" pull_adv={pv.get('advanced')}"
                gv = pj.get("go") if isinstance(pj.get("go"), dict) else None
                if gv:
                    detail += f" go={gv.get('decision') or 'yes'}"
                rv = pj.get("ready") if isinstance(pj.get("ready"), dict) else None
                if rv:
                    detail += f" ready={rv.get('level') or 'yes'}"
                dg = pj.get("digest") if isinstance(pj.get("digest"), dict) else None
                if dg:
                    detail += f" digest={dg.get('level') or 'yes'}"
                pr = pj.get("progress") if isinstance(pj.get("progress"), dict) else None
                if pr:
                    detail += f" progress_dtr={pr.get('days_to_ready')}"
                if pj.get("data_lag"):
                    rows.append(_warn("pipeline_last", detail + " DATA_LAG"))
                else:
                    rows.append(_ok("pipeline_last", detail))
            else:
                rows.append(_warn("pipeline_last", "bad json"))
        except Exception as e:
            rows.append(_warn("pipeline_last", f"parse: {e}"))
    else:
        rows.append(_warn("pipeline_last", "无 pipeline_last.json → etf refresh/daily"))

    # SMTP optional
    smtp_keys = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"]
    missing = [k for k in smtp_keys if not os.environ.get(k, "").strip()]
    if not missing:
        rows.append(_ok("smtp_env", "完整 (可发信)"))
    else:
        rows.append(_warn("smtp_env", f"缺 {missing} (本地可 dry-print, 云端需 Secrets)"))

    pages_url = os.environ.get("MAIL_PAGES_URL", "").strip()
    if pages_url:
        rows.append(_ok("pages_url", pages_url))
    else:
        rows.append(_warn("pages_url", "未设 MAIL_PAGES_URL (邮件无面板链接, 可选)"))

    # disk cache hint
    cache = Path.home() / ".cache" / "etf-rotation" / "klines"
    if cache.exists():
        rows.append(_ok("kline_cache", str(cache)))
    else:
        rows.append(_warn("kline_cache", f"未找到 {cache} (首次取数会建)"))

    return rows




def format_text(rows: list[dict], *, ok_all: bool) -> str:
    lines = [
        "======== ETF Doctor ========",
        f"时间: {datetime.now().isoformat(timespec='seconds')}",
        f"ROOT: {ROOT}",
        f"结果: {'PASS' if ok_all else 'FAIL'}",
        "----------------------------",
    ]
    for r in rows:
        mark = {"ok": "OK  ", "warn": "WARN", "error": "ERR "}.get(r["level"], "????")
        lines.append(f"  [{mark}] {r['check']}: {r.get('detail', '')}")
    lines.append("----------------------------")
    stale_w = any(r.get("check") == "latest_freshness" and r.get("level") == "warn" for r in rows)
    lag_w = any(
        r.get("check") in ("trading_day", "latest_signal_live", "site_meta", "pipeline_last", "data_asof")
        and r.get("level") == "warn"
        and "DATA_LAG" in str(r.get("detail") or "")
        for r in rows
    )
    if stale_w or lag_w:
        lines.append("优先:")
        if stale_w:
            lines.append("  ./etf refresh   # latest 过旧, 刷信号")
        if lag_w and not stale_w:
            lines.append("  ./etf          # 裸命令=pulse")
            lines.append("  ./etf pulse    # 可判/ETA 最短读口")
            lines.append("  ./etf pulse --quiet")
            lines.append("  ./etf digest   # 人读结论")
            lines.append("  ./etf go --timeout 600  # 等 asof 推进")
            lines.append("  ./etf eod --timeout 1800  # 收盘后一键")
            lines.append("  ./etf wait-asof  # 轮询直到 asof 推进")
            lines.append("  ./etf ready    # 有效收益可判性")
            lines.append("  ./etf next     # 只看决策")
            lines.append("  ./etf pull     # 单次强刷")
            lines.append("  ./etf data     # 行情状态明细")
            lines.append("  ./etf asof     # 明细取证")
        elif lag_w:
            lines.append("  ./etf          # 裸命令=pulse")
            lines.append("  ./etf pulse --quiet")
            lines.append("  ./etf data     # refresh 后仍 DATA_LAG 时看决策")
            lines.append("  ./etf asof")
    lines.append("常用:")
    lines.append("  ./etf           # = pulse")
    lines.append("  ./etf pulse")
    lines.append("  ./etf pulse --quiet")
    lines.append("  ./etf today")
    lines.append("  ./etf refresh")
    lines.append("  ./etf status")
    lines.append("  ./etf doctor")
    lines.append("  ./etf check")
    lines.append("  ./etf check --checks live")
    lines.append("  ./etf check --checks today")
    lines.append("  ./etf check --checks data_asof")
    lines.append("  ./etf asof")
    lines.append("  ./etf yield")
    lines.append("  ./etf brief")
    lines.append("  ./etf data")
    lines.append("  ./etf next")
    lines.append("  ./etf pull")
    lines.append("  ./etf open --launch site")
    lines.append("  ./etf monitor")
    lines.append("  ./etf live")
    lines.append("  ./etf summary")
    lines.append("  ./etf compare")
    lines.append("  ./etf warmup --tail 120")
    lines.append("  ./etf daily --dry-run")
    lines.append(f"  ./etf daily --dry-run --shadow {SIGNAL_SHADOW}")
    lines.append("  ./etf preview")
    lines.append("========")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="ETF 轮动一键体检")
    ap.add_argument("--json-out", default="output/risk_audit/doctor.json")
    ap.add_argument("--strict", action="store_true", help="WARN 也视为失败")
    args = ap.parse_args()

    rows = run_checks()
    hard_ok = all(r["ok"] for r in rows)
    if args.strict:
        ok_all = all(r["level"] == "ok" for r in rows)
    else:
        ok_all = hard_ok

    text = format_text(rows, ok_all=ok_all)
    print(text)

    payload = {
        "stamp": datetime.now().isoformat(timespec="seconds"),
        "ok": ok_all,
        "strict": bool(args.strict),
        "error_n": sum(1 for r in rows if r["level"] == "error"),
        "warn_n": sum(1 for r in rows if r["level"] == "warn"),
        "results": rows,
        "root": str(ROOT),
    }
    if args.json_out:
        p = Path(args.json_out)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"WROTE {p}")

    raise SystemExit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
