#!/usr/bin/env python3
"""研究状态一键面板 (只读聚合, 不交易).

汇总: 交易日门控 / 主线配置 / 影子告警 / pipeline 上次结果 / 站点.

用法:
  python3 scripts/research_status.py
  python3 scripts/research_status.py --json-out output/risk_audit/research_status.json
  python3 scripts/research_status.py --text-out output/research_status.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod  # noqa: E402
from etf_rotation.calendar_util import resolve_trading_day  # noqa: E402
from etf_rotation.paths import (  # noqa: E402
    LATEST_TXT,
    OUTPUT_DIR,
    STATE_FILE,
    ensure_dirs,
    shadow_state_file,
)
from etf_rotation.research_mainline import (  # noqa: E402
    MONITOR_SHADOWS,
    SIGNAL_SHADOW,
)

MAINLINE = list(MONITOR_SHADOWS)


def _ensure_live() -> None:
    """缺 latest.signal_live 时跑 shadow_live (回写 json/txt)."""
    latest_path = OUTPUT_DIR / "latest.json"
    has_sl = False
    if latest_path.exists():
        try:
            lj = json.loads(latest_path.read_text(encoding="utf-8"))
            sl = lj.get("signal_live") if isinstance(lj, dict) else None
            has_sl = isinstance(sl, dict) and (
                sl.get("live_return_pct") is not None
                or sl.get("live_excess_pct") is not None
            )
        except Exception:
            has_sl = False
    live_json = OUTPUT_DIR / "risk_audit" / "shadow_live.json"
    if has_sl and live_json.exists():
        return
    import subprocess

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "shadow_live.py"),
            "--json-out",
            str(live_json),
            "--text-out",
            str(OUTPUT_DIR / "shadow_live.txt"),
        ],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
    )


def _ensure_today() -> None:
    """缺 today 产物时现算 (只读聚合仍可带 live/xs/THIN)."""
    today_txt = OUTPUT_DIR / "today.txt"
    today_json = OUTPUT_DIR / "risk_audit" / "today.json"
    if today_txt.exists() or today_json.exists():
        return
    import subprocess

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "etf.py"), "today"],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
    )



def collect() -> dict:
    ensure_dirs()
    td = resolve_trading_day()
    configs = []
    for name in ["c01", *MAINLINE]:
        try:
            c = cfgmod.load_strategy(name)
            configs.append(
                {
                    "name": name,
                    "frozen": c.get("frozen"),
                    "research": c.get("research"),
                    "vol_target": c.get("vol_target"),
                    "top_n": c.get("top_n", 1),
                }
            )
        except Exception as e:
            configs.append({"name": name, "error": str(e)})

    # live 超额 map (若有日更 live 产物)
    live_map: dict = {}
    lp = OUTPUT_DIR / "risk_audit" / "shadow_live.json"
    if lp.exists():
        try:
            for rr in json.loads(lp.read_text(encoding="utf-8")) or []:
                if isinstance(rr, dict) and rr.get("name"):
                    live_map[str(rr["name"])] = rr
        except Exception:
            live_map = {}

    shadows = []
    for name in MAINLINE:
        p = shadow_state_file(name)
        if not p.exists():
            shadows.append({"name": name, "exists": False})
            continue
        st = json.loads(p.read_text(encoding="utf-8"))
        holds = st.get("holdings") or ([st["holding"]] if st.get("holding") else [])
        holds = [h for h in holds if h]
        hstr = ",".join(h.get("name", h.get("code", "?")) for h in holds) or "空仓"
        live = st.get("live") if isinstance(st.get("live"), dict) else {}
        if not live:
            try:
                from etf_rotation.portfolio import apply_live_metrics

                apply_live_metrics(st)
                live = st.get("live") or {}
            except Exception:
                live = {}
        lm = live_map.get(name) or {}
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
        shadows.append(
            {
                "name": name,
                "exists": True,
                "n_port_rets": len(st.get("port_rets") or []),
                "total_value": st.get("total_value"),
                "return_pct": st.get("return_pct"),
                "n_holdings": len(holds),
                "holdings": hstr,
                "last_update": st.get("last_update"),
                "warmup": bool(st.get("warmup")),
                "live_return_pct": live_ret,
                "live_start": live.get("start_date") or lm.get("live_start"),
                "live_anchor": live.get("anchor_nav") or lm.get("live_anchor"),
                "bench_return_pct": lm.get("bench_return_pct"),
                "live_excess_pct": lm.get("live_excess_pct"),
                "days_live": days_live,
                "thin_live": thin_live,
            }
        )
    mon = {}
    mon_path = OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"
    if mon_path.exists():
        try:
            mon = json.loads(mon_path.read_text(encoding="utf-8"))
        except Exception as e:
            mon = {"error": str(e)}

    pipe = {}
    pipe_path = OUTPUT_DIR / "risk_audit" / "pipeline_last.json"
    if pipe_path.exists():
        try:
            pipe = json.loads(pipe_path.read_text(encoding="utf-8"))
        except Exception as e:
            pipe = {"error": str(e)}

    health = {}
    health_path = OUTPUT_DIR / "risk_audit" / "research_healthcheck.json"
    if health_path.exists():
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
        except Exception as e:
            health = {"error": str(e)}

    weekly = {}
    weekly_path = OUTPUT_DIR / "risk_audit" / "weekly_last.json"
    if weekly_path.exists():
        try:
            weekly = json.loads(weekly_path.read_text(encoding="utf-8"))
        except Exception as e:
            weekly = {"error": str(e)}

    site_meta = {}
    sm = OUTPUT_DIR / "site" / "site_meta.json"
    if sm.exists():
        try:
            site_meta = json.loads(sm.read_text(encoding="utf-8"))
        except Exception as e:
            site_meta = {"error": str(e)}

    # 顶层有效收益快照 (status 首屏) + latest 新鲜度
    signal_live = None
    latest_time = None
    latest_stale = False
    latest_day = None
    try:
        lj_path = OUTPUT_DIR / "latest.json"
        if lj_path.exists():
            lj = json.loads(lj_path.read_text(encoding="utf-8"))
            if isinstance(lj, dict):
                if isinstance(lj.get("signal_live"), dict):
                    signal_live = lj.get("signal_live")
                latest_time = lj.get("time")
                latest_day = str(latest_time or "")[:10]
    except Exception:
        signal_live = None
    # 补齐 asof/lag (旧产物可能缺字段)
    if isinstance(signal_live, dict):
        if signal_live.get("market_asof") is None:
            signal_live["market_asof"] = (td or {}).get("data_asof")
        if signal_live.get("data_lag") is None:
            signal_live["data_lag"] = bool((td or {}).get("data_lag"))
    td_date = str((td or {}).get("date") or "")[:10]
    if (
        (td or {}).get("is_trading_day")
        and td_date
        and latest_day
        and len(latest_day) == 10
        and latest_day < td_date
    ):
        latest_stale = True
    if not latest_day and LATEST_TXT.exists():
        latest_stale = bool((td or {}).get("is_trading_day"))

    return {
        "stamp": datetime.now().isoformat(timespec="seconds"),
        "trading_day": td,
        "prod_state_exists": STATE_FILE.exists(),
        "latest_exists": LATEST_TXT.exists(),
        "latest_time": latest_time,
        "latest_stale": latest_stale,
        "signal_live": signal_live,
        "configs": configs,
        "shadows": shadows,
        "monitor": {
            "alert_error_n": mon.get("alert_error_n"),
            "alert_warn_n": mon.get("alert_warn_n"),
            "ok": mon.get("ok"),
        },
        "pipeline_last": {
            "stamp": pipe.get("stamp"),
            "ok": pipe.get("ok"),
            "alert_error_n": pipe.get("alert_error_n"),
            "alert_warn_n": pipe.get("alert_warn_n"),
            "trading_day": pipe.get("trading_day"),
            "data_asof": pipe.get("data_asof"),
            "data_lag": pipe.get("data_lag"),
        },
        "healthcheck_last": {
            "stamp": health.get("stamp"),
            "ok": health.get("ok"),
            "seconds": health.get("seconds"),
        },
        "weekly_last": {
            "stamp": weekly.get("stamp"),
            "ok": weekly.get("ok"),
            "alert_fail": weekly.get("alert_fail"),
        },
        "site": site_meta,
        "mainline": MAINLINE,
    }


def format_text(d: dict) -> str:
    td = d.get("trading_day") or {}
    lines = [
        "======== 研究状态面板 ========",
        f"时间: {d.get('stamp')}",
        f"交易日: {td.get('is_trading_day')} source={td.get('source')} date={td.get('date')}  "
        f"行情截至={td.get('data_asof') or '—'}"
        + ("  DATA_LAG" if td.get("data_lag") else ""),
        f"生产 state: {d.get('prod_state_exists')}  latest: {d.get('latest_exists')}  "
        f"信号时间: {d.get('latest_time') or '—'}",
    ]
    if td.get("data_lag"):
        lines.append(
            f"⚠ 行情滞后: wall/交易日 {td.get('date')} > 行情截至 {td.get('data_asof')} "
            f"(nav/live 以行情日为准, 等数据更新后再判 xs)"
        )
    if d.get("latest_stale"):
        lines.append(
            f"⚠ latest 过旧: 信号日 {str(d.get('latest_time') or '')[:10] or '—'} "
            f"< 交易日 {td.get('date')} → ./etf refresh"
        )
    # DIGEST/READY 首屏
    try:
        import json as _json
        _level = None
        _rec = None
        _dp = OUTPUT_DIR / "risk_audit" / "digest.json"
        _rp = OUTPUT_DIR / "risk_audit" / "ready.json"
        if _dp.exists():
            _dj = _json.loads(_dp.read_text(encoding="utf-8"))
            if isinstance(_dj, dict):
                _level = _dj.get("level")
                _rec = _dj.get("recommend")
        if _level is None and _rp.exists():
            _rj = _json.loads(_rp.read_text(encoding="utf-8"))
            if isinstance(_rj, dict):
                _level = _rj.get("level")
        if _level:
            lines.append("-------- 可判性首屏 --------")
            lines.append(f"  level={_level}  → ./etf pulse | ./etf digest")
            try:
                _dtr = None
                if _dp.exists():
                    _dtr = (_dj or {}).get("days_to_ready") if isinstance(_dj, dict) else None
                if _dtr is None and _rp.exists():
                    _rj2 = _json.loads(_rp.read_text(encoding="utf-8"))
                    if isinstance(_rj2, dict):
                        _dtr = _rj2.get("days_to_ready")
                if _dtr is not None:
                    lines.append(f"  days_to_ready={_dtr}  → ./etf pulse | ./etf progress")
                    try:
                        _di = int(_dtr)
                        _lag_s = False
                        if _rp.exists():
                            _rj3 = _json.loads(_rp.read_text(encoding="utf-8"))
                            if isinstance(_rj3, dict):
                                _lag_s = bool(_rj3.get("data_lag"))
                                _en = _rj3.get("eta_note")
                            else:
                                _en = None
                        else:
                            _en = None
                        if _en:
                            lines.append(f"  ETA: {_en}")
                        elif _di <= 0:
                            lines.append("  ETA: 样本已够 (若无 DATA_LAG → READY)")
                        else:
                            lag_note = "; 另需 asof 先推进" if _lag_s else ""
                            lines.append(f"  ETA: 约再 {_di} 个交易日可 READY (Lrets≥5){lag_note}")
                    except Exception:
                        pass
            except Exception:
                pass
            if _rec:
                _rec_s = str(_rec).replace("python3 scripts/etf.py ", "./etf ")
                lines.append(f"  推荐: {_rec_s}")
    except Exception:
        pass

    # 首屏有效收益 (真实日更段)
    sl = d.get("signal_live") if isinstance(d.get("signal_live"), dict) else None
    if sl and (
        sl.get("live_return_pct") is not None or sl.get("live_excess_pct") is not None
    ):
        lr = sl.get("live_return_pct")
        xs = sl.get("live_excess_pct")
        br = sl.get("bench_return_pct")
        dl = sl.get("days_live")
        thin = sl.get("thin_live")
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
        lines.append("-------- 有效收益 (SIGNAL) --------")
        lines.append(f"  策略: {sl.get('name') or SIGNAL_SHADOW}")
        lines.append(f"  live={lr_s}{tag}  xs={xs_s}  bench={br_s}")
        lines.append(
            f"  from={sl.get('live_start') or '—'}  Lrets={dl if dl is not None else '—'}  "
            f"持仓={sl.get('holdings') or '—'}"
        )
        asof = sl.get("market_asof") or (d.get("trading_day") or {}).get("data_asof")
        lag = sl.get("data_lag")
        if lag is None:
            lag = (d.get("trading_day") or {}).get("data_lag")
        if asof or lag:
            lines.append(
                f"  行情截至: {asof or '—'}"
                + ("  DATA_LAG" if lag else "")
            )
        if thin:
            if dl is not None:
                try:
                    if int(dl) == 0:
                        lines.append("  注: Lrets=0=仅锚日, 尚无 live 样本日")
                except Exception:
                    pass
            lines.append("  注: THIN=样本<5日; DATA_LAG 时等行情更新后再判 xs")
    else:
        lines.append("-------- 有效收益 (SIGNAL) --------")
        lines.append("  · 未生成 → ./etf live")
    # 行情取证 asof (只读摘要)
    asof_path = OUTPUT_DIR / "asof.txt"
    asof_json = OUTPUT_DIR / "risk_audit" / "asof.json"
    if asof_path.exists() or asof_json.exists():
        lines.append("-------- 行情取证 (asof) --------")
        try:
            if asof_json.exists():
                aj = json.loads(asof_json.read_text(encoding="utf-8"))
                if isinstance(aj, dict):
                    lines.append(
                        f"  行情截至={aj.get('market_asof') or '—'}  "
                        f"lag={aj.get('data_lag')}  动作={aj.get('action') or '—'}"
                    )
                    sl_a = aj.get("signal_live") if isinstance(aj.get("signal_live"), dict) else {}
                    if sl_a:
                        lines.append(
                            f"  live={sl_a.get('live_return_pct')} xs={sl_a.get('live_excess_pct')} "
                            f"Lrets={sl_a.get('days_live')} thin={sl_a.get('thin_live')}"
                        )
                    lines.append("  · 全文 → ./etf asof")
                else:
                    lines.append("  · 见 output/asof.txt")
            else:
                # txt only: first non-empty lines
                raw = asof_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:6]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · asof 读失败: {e}")
    else:
        lines.append("-------- 行情取证 (asof) --------")
        lines.append("  · 未生成 → ./etf asof")
    # yield 有效收益落盘 (与 SIGNAL 段互补, 便于一键产物对齐)
    yield_path = OUTPUT_DIR / "yield.txt"
    yield_json = OUTPUT_DIR / "risk_audit" / "yield.json"
    if yield_path.exists() or yield_json.exists():
        lines.append("-------- 有效收益产物 (yield) --------")
        try:
            if yield_json.exists():
                yj = json.loads(yield_json.read_text(encoding="utf-8"))
                if isinstance(yj, dict):
                    lines.append(
                        f"  live={yj.get('live_return_pct')} xs={yj.get('live_excess_pct')} "
                        f"Lrets={yj.get('days_live')} thin={yj.get('thin_live')} "
                        f"asof={yj.get('market_asof')}"
                    )
                    lines.append("  · 全文 → ./etf yield")
                else:
                    lines.append("  · 见 output/yield.txt")
            else:
                raw = yield_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:6]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · yield 读失败: {e}")
    else:
        lines.append("-------- 有效收益产物 (yield) --------")
        lines.append("  · 未生成 → ./etf yield")
    brief_path = OUTPUT_DIR / "brief.txt"
    brief_json = OUTPUT_DIR / "risk_audit" / "brief.json"
    if brief_path.exists() or brief_json.exists():
        lines.append("-------- 三合一 (brief) --------")
        try:
            if brief_json.exists():
                bj = json.loads(brief_json.read_text(encoding="utf-8"))
                if isinstance(bj, dict):
                    lines.append(
                        f"  asof={bj.get('market_asof') or '—'} lag={bj.get('data_lag')} "
                        f"动作={bj.get('action') or '—'}"
                    )
                    slb = bj.get("signal_live") if isinstance(bj.get("signal_live"), dict) else {}
                    if slb:
                        lines.append(
                            f"  live={slb.get('live_return_pct')} xs={slb.get('live_excess_pct')} "
                            f"Lrets={slb.get('days_live')}"
                        )
                    lines.append("  · 全文 → ./etf brief")
            else:
                raw = brief_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:6]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · brief 读失败: {e}")
    else:
        lines.append("-------- 三合一 (brief) --------")
        lines.append("  · 未生成 → ./etf brief")
    data_path = OUTPUT_DIR / "data_status.txt"
    data_json = OUTPUT_DIR / "risk_audit" / "data_status.json"
    if data_path.exists() or data_json.exists():
        lines.append("-------- 行情状态 (data) --------")
        try:
            if data_json.exists():
                dj = json.loads(data_json.read_text(encoding="utf-8"))
                if isinstance(dj, dict):
                    lines.append(
                        f"  asof={dj.get('market_asof') or '—'} lag={dj.get('data_lag')} "
                        f"stale={dj.get('latest_stale')} decision={dj.get('decision') or '—'}"
                    )
                    lines.append("  · 全文 → ./etf data")
            else:
                raw = data_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:5]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · data 读失败: {e}")
    else:
        lines.append("-------- 行情状态 (data) --------")
        lines.append("  · 未生成 → ./etf data")
    next_path = OUTPUT_DIR / "next.txt"
    next_json = OUTPUT_DIR / "risk_audit" / "next.json"
    if next_path.exists() or next_json.exists():
        lines.append("-------- 下一步 (next) --------")
        try:
            if next_json.exists():
                nj = json.loads(next_json.read_text(encoding="utf-8"))
                if isinstance(nj, dict):
                    lines.append(
                        f"  decision={nj.get('decision') or '—'} asof={nj.get('market_asof') or '—'} "
                        f"lag={nj.get('data_lag')}"
                    )
                    if nj.get("recommend"):
                        lines.append(f"  推荐: {nj.get('recommend')}")
                    lines.append("  · 全文 → ./etf next")
            else:
                raw = next_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:5]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · next 读失败: {e}")
    else:
        lines.append("-------- 下一步 (next) --------")
        lines.append("  · 未生成 → ./etf next")
    pull_path = OUTPUT_DIR / "pull.txt"
    pull_json = OUTPUT_DIR / "risk_audit" / "pull.json"
    if pull_path.exists() or pull_json.exists():
        lines.append("-------- 行情强刷 (pull) --------")
        try:
            if pull_json.exists():
                pj = json.loads(pull_json.read_text(encoding="utf-8"))
                if isinstance(pj, dict):
                    after = pj.get("after") if isinstance(pj.get("after"), dict) else {}
                    before = pj.get("before") if isinstance(pj.get("before"), dict) else {}
                    lines.append(
                        f"  before={before.get('data_asof')}→after={after.get('data_asof')} "
                        f"advanced={pj.get('advanced')} lag={after.get('data_lag')}"
                    )
                    lines.append("  · 全文 → ./etf pull --bench-only")
            else:
                raw = pull_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:5]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · pull 读失败: {e}")
    else:
        lines.append("-------- 行情强刷 (pull) --------")
        lines.append("  · 未生成 → ./etf pull --bench-only")
    go_path = OUTPUT_DIR / "go.txt"
    go_json = OUTPUT_DIR / "risk_audit" / "go.json"
    if go_path.exists() or go_json.exists():
        lines.append("-------- 一键闭环 (go) --------")
        try:
            if go_json.exists():
                gj = json.loads(go_json.read_text(encoding="utf-8"))
                if isinstance(gj, dict):
                    lines.append(
                        f"  decision={gj.get('decision')} asof={gj.get('market_asof')} "
                        f"lag={gj.get('data_lag')} did_wait={gj.get('did_wait')}"
                    )
                    lines.append("  · 全文 → ./etf go --no-wait")
            else:
                raw = go_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:5]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · go 读失败: {e}")
    else:
        lines.append("-------- 一键闭环 (go) --------")
        lines.append("  · 未生成 → ./etf go --no-wait")
    ready_path = OUTPUT_DIR / "ready.txt"
    ready_json = OUTPUT_DIR / "risk_audit" / "ready.json"
    if ready_path.exists() or ready_json.exists():
        lines.append("-------- 可判性 (ready) --------")
        try:
            if ready_json.exists():
                rj = json.loads(ready_json.read_text(encoding="utf-8"))
                if isinstance(rj, dict):
                    lines.append(
                        f"  level={rj.get('level')} asof={rj.get('market_asof')} "
                        f"Lrets={rj.get('days_live')} thin={rj.get('thin_live')} "
                        f"days_to_ready={rj.get('days_to_ready')}"
                    )
                    if rj.get("note"):
                        lines.append(f"  说明: {rj.get('note')}")
                    lines.append("  · 全文 → ./etf ready")
            else:
                raw = ready_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:6]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · ready 读失败: {e}")
    else:
        lines.append("-------- 可判性 (ready) --------")
        lines.append("  · 未生成 → ./etf ready")
    digest_path = OUTPUT_DIR / "digest.txt"
    digest_json = OUTPUT_DIR / "risk_audit" / "digest.json"
    if digest_path.exists() or digest_json.exists():
        lines.append("-------- 摘要 (digest) --------")
        try:
            if digest_json.exists():
                dj = json.loads(digest_json.read_text(encoding="utf-8"))
                if isinstance(dj, dict):
                    lines.append(
                        f"  level={dj.get('level')} decision={dj.get('decision')} "
                        f"live={dj.get('live_return_pct')} xs={dj.get('live_excess_pct')}"
                    )
                    if dj.get("recommend"):
                        lines.append(f"  推荐: {dj.get('recommend')}")
                    lines.append("  · 全文 → ./etf digest")
            else:
                raw = digest_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:6]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · digest 读失败: {e}")
    else:
        lines.append("-------- 摘要 (digest) --------")
        lines.append("  · 未生成 → ./etf digest")
    eod_path = OUTPUT_DIR / "eod.txt"
    eod_json = OUTPUT_DIR / "risk_audit" / "eod.json"
    if eod_path.exists() or eod_json.exists():
        lines.append("-------- 收盘闭环 (eod) --------")
        try:
            if eod_json.exists():
                ej = json.loads(eod_json.read_text(encoding="utf-8"))
                if isinstance(ej, dict):
                    lines.append(
                        f"  level={ej.get('level')} asof={ej.get('market_asof')} "
                        f"Lrets={ej.get('days_live')} days_to_ready={ej.get('days_to_ready')}"
                    )
                    lines.append("  · 全文 → ./etf eod --no-wait")
            else:
                raw = eod_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][:6]
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body)
        except Exception as e:
            lines.append(f"  · eod 读失败: {e}")
    else:
        lines.append("-------- 收盘闭环 (eod) --------")
        lines.append("  · 未生成 → ./etf eod --timeout 1800")
    prog_path = OUTPUT_DIR / "progress.txt"
    prog_latest = OUTPUT_DIR / "risk_audit" / "progress_latest.json"
    prog_jsonl = OUTPUT_DIR / "risk_audit" / "progress.jsonl"
    if prog_path.exists() or prog_latest.exists() or prog_jsonl.exists():
        lines.append("-------- 可判轨迹 (progress) --------")
        try:
            if prog_latest.exists():
                pj = json.loads(prog_latest.read_text(encoding="utf-8"))
                if isinstance(pj, dict):
                    lines.append(
                        f"  latest: {pj.get('date')} level={pj.get('level')} "
                        f"Lrets={pj.get('days_live')} dtr={pj.get('days_to_ready')} "
                        f"live={pj.get('live_return_pct')} xs={pj.get('live_excess_pct')} "
                        f"src={pj.get('source')}"
                    )
                    try:
                        dtr = pj.get("days_to_ready")
                        lag = bool(pj.get("data_lag"))
                        if dtr is not None:
                            dtr_i = int(dtr)
                            if dtr_i <= 0:
                                lines.append("  ETA: 样本已够 (若无 DATA_LAG → READY)")
                            else:
                                lag_s = "; 另需 asof 先推进" if lag else ""
                                lines.append(
                                    f"  ETA: 约再 {dtr_i} 个交易日可 READY (Lrets≥5){lag_s}"
                                )
                    except Exception:
                        pass
            # Δ from jsonl first/last of day trail
            prog_jsonl = OUTPUT_DIR / "risk_audit" / "progress.jsonl"
            if prog_jsonl.exists():
                rows = []
                for ln in prog_jsonl.read_text(encoding="utf-8").splitlines():
                    if not ln.strip():
                        continue
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
                if len(rows) >= 2:
                    a, b = rows[0], rows[-1]
                    try:
                        d0 = int(a.get("days_live") or 0)
                        d1 = int(b.get("days_live") or 0)
                        lines.append(
                            f"  Δ: Lrets {d0}→{d1} ({d1-d0:+d})  "
                            f"level {a.get('level')}→{b.get('level')}"
                        )
                    except Exception:
                        pass
            if prog_path.exists():
                raw = prog_path.read_text(encoding="utf-8")
                body = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("====")][-4:]
                for ln in body:
                    lines.append("  " + ln if not ln.startswith(" ") else ln)
            lines.append("  · 全文 → ./etf pulse | ./etf progress | ./etf progress --json")
        except Exception as e:
            lines.append(f"  · progress 读失败: {e}")
    else:
        lines.append("-------- 可判轨迹 (progress) --------")
        lines.append("  · 未生成 → ./etf ready 或 ./etf progress")
    pulse_path = OUTPUT_DIR / "pulse.txt"
    pulse_json = OUTPUT_DIR / "risk_audit" / "pulse.json"
    if pulse_path.exists() or pulse_json.exists():
        lines.append("-------- 脉搏 (pulse) --------")
        try:
            if pulse_json.exists():
                pu = json.loads(pulse_json.read_text(encoding="utf-8"))
                if isinstance(pu, dict):
                    lines.append(
                        f"  level={pu.get('level')} dtr={pu.get('days_to_ready')} "
                        f"live={pu.get('live_return_pct')} xs={pu.get('live_excess_pct')} "
                        f"asof={pu.get('market_asof')}"
                    )
                    if pu.get("next_action"):
                        lines.append(
                            f"  next_action={pu.get('next_action')} "
                            f"readable={pu.get('readable_yield')} → ./etf do"
                        )
                    if pu.get("eta_note"):
                        lines.append(f"  ETA: {pu.get('eta_note')}")
                    if pu.get("recommend"):
                        lines.append(f"  推荐: {pu.get('recommend')}")
            lines.append("  · 重跑 → ./etf  |  执行 → ./etf do")
        except Exception as e:
            lines.append(f"  · pulse 读失败: {e}")
    else:
        lines.append("-------- 脉搏 (pulse) --------")
        lines.append("  · 未生成 → ./etf pulse")
    lines.append("-------- 配置 --------")
    for c in d.get("configs") or []:
        if c.get("error"):
            lines.append(f"  {c['name']}: ERROR {c['error']}")
        else:
            lines.append(
                f"  {c['name']}: frozen={c.get('frozen')} research={c.get('research')} "
                f"vt={c.get('vol_target')} top_n={c.get('top_n')}"
            )
    lines.append("-------- 影子 --------")
    for s in d.get("shadows") or []:
        if not s.get("exists"):
            lines.append(f"  {s['name']}: 缺 state")
        else:
            lr = s.get("live_return_pct")
            xs = s.get("live_excess_pct")
            lr_s = f"{float(lr):+.2f}%" if lr is not None else "—"
            try:
                xs_s = f"{float(xs):+.2f}%" if xs is not None else "—"
            except Exception:
                xs_s = "—"
            thin = ""
            if s.get("thin_live") or (
                s.get("days_live") is not None and int(s.get("days_live") or 0) < 5
            ):
                thin = " THIN"
            dl = s.get("days_live")
            lines.append(
                f"  {s['name']}: rets={s.get('n_port_rets')} "
                f"资产={s.get('total_value')} live={lr_s}{thin} xs={xs_s} "
                f"持仓={s.get('holdings') or s.get('n_holdings')} "
                f"warmup={s.get('warmup')}"
                f"{'' if dl is None else f' Lrets={dl}'}"
            )
    m = d.get("monitor") or {}
    lines.append(
        f"-------- 监控告警 --------\n"
        f"  error={m.get('alert_error_n')} warn={m.get('alert_warn_n')} ok={m.get('ok')}"
    )
    p = d.get("pipeline_last") or {}
    lines.append(
        f"-------- pipeline 上次 --------\n"
        f"  stamp={p.get('stamp')} ok={p.get('ok')} "
        f"alerts={p.get('alert_error_n')}/{p.get('alert_warn_n')} td={p.get('trading_day')} "
        f"asof={p.get('data_asof') or '—'} lag={p.get('data_lag')}"
    )
    h = d.get("healthcheck_last") or {}
    lines.append(
        f"-------- healthcheck 上次 --------\n"
        f"  stamp={h.get('stamp')} ok={h.get('ok')} secs={h.get('seconds')}"
    )
    w = d.get("weekly_last") or {}
    lines.append(
        f"-------- weekly 上次 --------\n"
        f"  stamp={w.get('stamp')} ok={w.get('ok')} alert_fail={w.get('alert_fail')}"
    )
    s = d.get("site") or {}
    lines.append(
        f"-------- site --------\n"
        f"  built={s.get('built_at')} alerts={s.get('alert_error_n')}/{s.get('alert_warn_n')} "
        f"today={s.get('has_today')} live={s.get('has_live')} compare={s.get('has_compare')}"
    )
    # 主线对照 (若有 compare 产物则摘要, 否则提示)
    cmp_path = OUTPUT_DIR / "shadow_compare.txt"
    if not cmp_path.exists():
        cmp_path = OUTPUT_DIR / "risk_audit" / "shadow_compare.json"
    if (OUTPUT_DIR / "shadow_compare.txt").exists():
        try:
            raw = (OUTPUT_DIR / "shadow_compare.txt").read_text(encoding="utf-8")
            # 只摘表格行
            body = [
                ln
                for ln in raw.splitlines()
                if ln.strip().startswith("c01_")
                or "SIGNAL" in ln
                or ln.strip().startswith("========")
                or "信号默认" in ln
            ]
            if body:
                lines.append("-------- 主线对照 --------")
                lines.extend(
                    "  " + ln if not ln.startswith(" ") else ln for ln in body[:12]
                )
        except Exception:
            pass
    else:
        lines.append("-------- 主线对照 --------")
        lines.append("  · 未生成 → ./etf compare")

    # LIVE 摘要 (优先 latest.signal_live, 再 shadow_live.txt)
    live_block_added = False
    try:
        latest_path = OUTPUT_DIR / "latest.json"
        if latest_path.exists():
            lj = json.loads(latest_path.read_text(encoding="utf-8"))
            sl = lj.get("signal_live") if isinstance(lj, dict) else None
            if isinstance(sl, dict) and (
                sl.get("live_return_pct") is not None or sl.get("live_excess_pct") is not None
            ):
                lr = sl.get("live_return_pct")
                xs = sl.get("live_excess_pct")
                br = sl.get("bench_return_pct")
                dl = sl.get("days_live")
                thin = sl.get("thin_live")
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
                lines.append("-------- 主线 LIVE --------")
                lines.append(f"  策略: {sl.get('name') or SIGNAL_SHADOW}")
                lines.append(f"  live={lr_s}{tag}  xs={xs_s}  bench={br_s}")
                lines.append(
                    f"  from={sl.get('live_start') or '—'}  Lrets={dl if dl is not None else '—'}  "
                    f"持仓={sl.get('holdings') or '—'}"
                )
                if thin:
                    lines.append("  注: THIN=样本<5日, 等下一交易日再判 xs")
                live_block_added = True
    except Exception:
        pass
    if not live_block_added:
        live_path = OUTPUT_DIR / "shadow_live.txt"
        if live_path.exists():
            try:
                raw = live_path.read_text(encoding="utf-8")
                body = [
                    ln
                    for ln in raw.splitlines()
                    if ln.strip().startswith("c01_")
                    or "SIGNAL" in ln
                    or "信号默认" in ln
                    or ln.strip().startswith("========")
                    or "口径" in ln
                ]
                if body:
                    lines.append("-------- 主线 LIVE --------")
                    lines.extend(
                        "  " + ln if not ln.startswith(" ") else ln for ln in body[:14]
                    )
                    live_block_added = True
            except Exception:
                pass
    if not live_block_added:
        lines.append("-------- 主线 LIVE --------")
        lines.append("  · 未生成 → ./etf live")

    # 影子仓位摘要 (live/xs)
    sum_path = OUTPUT_DIR / "shadow_summary.txt"
    if sum_path.exists():
        try:
            raw = sum_path.read_text(encoding="utf-8")
            body = [
                ln
                for ln in raw.splitlines()
                if ln.strip().startswith("c01_")
                or "live=" in ln
                or "影子仓位" in ln
            ]
            if body:
                lines.append("-------- 影子摘要 --------")
                lines.extend(
                    "  " + ln if not ln.startswith(" ") else ln for ln in body[:12]
                )
        except Exception:
            pass
    else:
        lines.append("-------- 影子摘要 --------")
        lines.append("  · 未生成 → ./etf summary")

    # 今日速览 (动作+live/xs/THIN)
    today_path = OUTPUT_DIR / "today.txt"
    if today_path.exists():
        try:
            raw = today_path.read_text(encoding="utf-8")
            body = [
                ln
                for ln in raw.splitlines()
                if ln.strip()
                and not ln.startswith("====")
                and "下一步" not in ln
                and "python3 scripts" not in ln
            ]
            if body:
                lines.append("-------- 今日速览 --------")
                lines.extend("  " + ln if not ln.startswith(" ") else ln for ln in body[:16])
        except Exception:
            pass
    else:
        lines.append("-------- 今日速览 --------")
        lines.append("  · 未生成 → ./etf today")


    lines.append("-------- 下一步 --------")
    td0 = d.get("trading_day") or {}
    # 决策树: 过旧 → refresh; 仅 DATA_LAG → asof(等行情); 否则正常查看
    if d.get("latest_stale"):
        lines.append(
            "  · ⚠ latest 过旧 → ./etf refresh  (优先)"
        )
        lines.append("  · 或 → ./etf daily --dry-run")
    if td0.get("data_lag"):
        if d.get("latest_stale"):
            lines.append(
                f"  · DATA_LAG: 行情截至 {td0.get('data_asof')} · refresh 后仍需等数据再判 xs"
            )
        else:
            lines.append(
                f"  · ⚠ DATA_LAG: 行情截至 {td0.get('data_asof')} → ./etf next|wait-asof  (优先)"
            )
            lines.append("  · 明细取证 → ./etf asof")
            lines.append("  · 等行情更新后再 refresh/判 xs (勿反复 refresh 空转)")
    if not d.get("latest_exists"):
        lines.append("  · 无 latest → ./etf daily --dry-run")
    else:
        lines.append("  · 看信号: output/latest.txt · 面板: output/site/index.html")
    site = d.get("site") or {}
    site_ok = bool(site.get("built_at") or site.get("has_dashboard") or site.get("exists"))
    if not site_ok and not (OUTPUT_DIR / "site" / "index.html").exists():
        lines.append("  · 无站点 → ./etf pages")
    lines.append("  · 今日速览 → ./etf today")
    lines.append("  · 刷新面板 → ./etf preview")
    lines.append("  · 体检 → ./etf doctor")
    lines.append("  · 健康 → ./etf check")
    lines.append("  · 健康 live → ./etf check --checks live")
    lines.append("  · 健康 today → ./etf check --checks today")
    lines.append("  · 健康 asof → ./etf check --checks data_asof")
    lines.append("  · 取证 asof → ./etf asof")
    lines.append("  · 有效收益 → ./etf yield")
    lines.append("  · 三合一 → ./etf brief")
    lines.append("  · 行情状态 → ./etf data")
    lines.append("  · 下一步 → ./etf next")
    lines.append("  · 强刷行情 → ./etf pull")
    lines.append("  · 轮询 asof → ./etf wait-asof")
    lines.append("  · 打开面板 → ./etf open --launch site")
    lines.append("  · 监控 → ./etf monitor")
    lines.append("  · LIVE → ./etf live")
    lines.append("  · 摘要 → ./etf summary")
    lines.append("  · 暖机 → ./etf warmup --tail 120")
    lines.append("  · 对照 → ./etf compare")
    lines.append(f"  · 研究影子默认 {SIGNAL_SHADOW} (生产 c01 冻结)")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="研究状态一键面板")
    ap.add_argument("--json-out", default="output/risk_audit/research_status.json")
    ap.add_argument("--text-out", default="")
    args = ap.parse_args()

    _ensure_live()
    _ensure_today()
    d = collect()
    text = format_text(d)
    print(text)

    if args.json_out:
        p = Path(args.json_out)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
