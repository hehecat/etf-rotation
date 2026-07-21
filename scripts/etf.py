#!/usr/bin/env python3
"""统一入口: 状态 / 体检 / 日更 / 预览 (提高可用性).

用法:
  ./etf status
  ./etf doctor
  ./etf daily --dry-run
  ./etf preview
  ./etf signal --dry-run
  ./etf pages
  ./etf help
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def _days_to_ready_from_live(days_live) -> int | None:
    try:
        if days_live is None:
            return None
        dl = int(days_live)
        return max(0, 5 - dl)
    except Exception:
        return None


def _eta_ready_note(days_to_ready, data_lag: bool = False) -> str:
    """样本 dtr → 人读 ETA (交易日, 非日历日精确)."""
    if days_to_ready is None:
        return ""
    try:
        d = int(days_to_ready)
    except Exception:
        return ""
    if d <= 0:
        return "样本已够, 若无 DATA_LAG 则应 READY"
    lag_note = "; 另需 asof 先推进" if data_lag else ""
    return f"约再 {d} 个交易日可 READY (Lrets≥5){lag_note}"


def _load_ready_eta() -> tuple:
    """(days_to_ready, eta_note, level) from ready.json if present."""
    import json
    try:
        rp = ROOT / "output" / "risk_audit" / "ready.json"
        if not rp.exists():
            return None, "", None
        obj = json.loads(rp.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return None, "", None
        dtr = obj.get("days_to_ready")
        note = obj.get("eta_note") or _eta_ready_note(dtr, bool(obj.get("data_lag")))
        return dtr, note or "", obj.get("level")
    except Exception:
        return None, "", None


def _snapshot_progress(source: str = "ready") -> dict | None:
    """从 ready.json 快照一条 progress (asof 推进后事件轨迹)."""
    import json

    try:
        rp = ROOT / "output" / "risk_audit" / "ready.json"
        if not rp.exists():
            return None
        ready = json.loads(rp.read_text(encoding="utf-8"))
        if not isinstance(ready, dict):
            return None
        row = {
            "level": ready.get("level"),
            "market_asof": ready.get("market_asof"),
            "data_lag": ready.get("data_lag"),
            "days_live": ready.get("days_live"),
            "days_to_ready": ready.get("days_to_ready"),
            "thin_live": ready.get("thin_live"),
            "live_return_pct": ready.get("live_return_pct"),
            "live_excess_pct": ready.get("live_excess_pct"),
            "decision": ready.get("decision"),
            "action": ready.get("action"),
            "source": source,
        }
        _append_progress(row)
        return row
    except Exception:
        return None


def _append_progress(row: dict) -> None:
    """追加可判性/有效收益轨迹 (jsonl), 便于观察 Lrets 向 READY 推进."""
    import json
    from datetime import datetime

    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        path = risk / "progress.jsonl"
        stamp = datetime.now().isoformat(timespec="seconds")
        day = str(row.get("market_asof") or row.get("date") or stamp[:10])
        rec = {
            "stamp": stamp,
            "date": day,
            "level": row.get("level"),
            "market_asof": row.get("market_asof"),
            "data_lag": row.get("data_lag"),
            "days_live": row.get("days_live"),
            "days_to_ready": row.get("days_to_ready"),
            "thin_live": row.get("thin_live"),
            "live_return_pct": row.get("live_return_pct"),
            "live_excess_pct": row.get("live_excess_pct"),
            "decision": row.get("decision"),
            "action": row.get("action"),
            "source": row.get("source") or "ready",
        }
        lines = []
        if path.exists():
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        kept = []
        for ln in lines:
            try:
                obj = json.loads(ln)
            except Exception:
                kept.append(ln)
                continue
            if not (
                isinstance(obj, dict)
                and obj.get("date") == day
                and obj.get("source") == rec["source"]
            ):
                kept.append(ln)
        kept.append(json.dumps(rec, ensure_ascii=False))
        kept = kept[-120:]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        (risk / "progress_latest.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        txt_lines = ["======== PROGRESS 可判性轨迹 ========"]
        for ln in kept[-12:]:
            try:
                o = json.loads(ln)
                txt_lines.append(
                    f"{o.get('date')} level={o.get('level')} asof={o.get('market_asof')} "
                    f"Lrets={o.get('days_live')} dtr={o.get('days_to_ready')} "
                    f"live={o.get('live_return_pct')} xs={o.get('live_excess_pct')} "
                    f"lag={o.get('data_lag')} src={o.get('source')}"
                )
            except Exception:
                txt_lines.append(ln[:120])
        txt_lines.append("========")
        (out / "progress.txt").write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    except Exception:
        pass


SCRIPTS = ROOT / "scripts"

try:
    from etf_rotation.research_mainline import SIGNAL_SHADOW as _DEFAULT_SHADOW
except Exception:
    _DEFAULT_SHADOW = "c01_q10_vt08_soft_oh38_xgn"


def _run(args: list[str], *, env: dict | None = None) -> int:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(ROOT) + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    if env:
        e.update(env)
    print("+", " ".join(args), flush=True)
    return subprocess.call(args, cwd=str(ROOT), env=e)


def cmd_status(_: argparse.Namespace) -> int:
    return _run(
        [
            PY,
            str(SCRIPTS / "research_status.py"),
            "--json-out",
            "output/risk_audit/research_status.json",
            "--text-out",
            "output/research_status.txt",
        ]
    )


def cmd_doctor(_: argparse.Namespace) -> int:
    return _run([PY, str(SCRIPTS / "doctor.py")])


def cmd_do(args: argparse.Namespace) -> int:
    """执行 pulse.next_action (真实有效收益决策闭环)."""
    import json

    if not getattr(args, "no_refresh", False):
        _run([PY, str(SCRIPTS / "etf.py"), "pulse", "--quiet"])
    else:
        pj = ROOT / "output" / "risk_audit" / "pulse.json"
        if not pj.exists():
            _run([PY, str(SCRIPTS / "etf.py"), "pulse", "--quiet", "--no-refresh"])

    pulse: dict = {}
    try:
        obj = json.loads((ROOT / "output" / "risk_audit" / "pulse.json").read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            pulse = obj
    except Exception:
        pulse = {}

    action = (getattr(args, "action", "") or "").strip() or str(pulse.get("next_action") or "")
    dry = bool(getattr(args, "dry_run", False))
    timeout = int(getattr(args, "timeout", 600) or 600)

    print("======== DO 执行 next_action ========")
    print(f"pulse.level={pulse.get('level')} readable_yield={pulse.get('readable_yield')}")
    print(f"next_action={action or '—'}  recommend={pulse.get('recommend') or '—'}")
    if pulse.get("why"):
        print(f"why: {pulse.get('why')}")
    if pulse.get("eta_note"):
        print(f"ETA: {pulse.get('eta_note')}")

    if not action:
        print("无 next_action → ./etf pulse --quiet --json")
        print("========")
        return 1

    if action == "wait_asof":
        cmd = [PY, str(SCRIPTS / "etf.py"), "wait-asof", "--timeout", str(timeout)]
    elif action == "refresh":
        cmd = [PY, str(SCRIPTS / "etf.py"), "refresh"]
    elif action == "accumulate":
        if getattr(args, "no_wait", False):
            cmd = [PY, str(SCRIPTS / "etf.py"), "eod", "--no-wait"]
        else:
            cmd = [PY, str(SCRIPTS / "etf.py"), "eod", "--timeout", str(max(timeout, 1800))]
    elif action == "read_yield":
        cmd = [PY, str(SCRIPTS / "etf.py"), "yield"]
    elif action == "doctor":
        cmd = [PY, str(SCRIPTS / "etf.py"), "doctor"]
    else:
        print(f"未知 next_action={action}")
        print("========")
        return 1

    # cmd = [PY, scripts/etf.py, sub, ...]
    if len(cmd) >= 3 and str(cmd[1]).endswith("etf.py"):
        shown = " ".join(str(x) for x in cmd[2:])
    else:
        shown = " ".join(str(x) for x in cmd)
    print(f"exec: ./etf {shown}")
    code = 0
    if dry:
        print("dry-run: 不执行")
    else:
        code = _run(cmd)
        if action in ("wait_asof", "refresh", "accumulate", "read_yield"):
            _run([PY, str(SCRIPTS / "etf.py"), "pulse", "--quiet", "--no-refresh"])
        print(f"do.exit={code}")
    print("========")

    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        payload = {
            "action": action,
            "exec": shown,
            "exit": code,
            "dry_run": dry,
            "timeout": timeout,
            "pulse_before": {
                "level": pulse.get("level"),
                "next_action": pulse.get("next_action"),
                "readable_yield": pulse.get("readable_yield"),
                "days_to_ready": pulse.get("days_to_ready"),
            },
        }
        try:
            pb = json.loads((risk / "pulse.json").read_text(encoding="utf-8"))
            if isinstance(pb, dict):
                payload["pulse_after"] = {
                    "level": pb.get("level"),
                    "next_action": pb.get("next_action"),
                    "readable_yield": pb.get("readable_yield"),
                    "days_to_ready": pb.get("days_to_ready"),
                    "live_return_pct": pb.get("live_return_pct"),
                    "live_excess_pct": pb.get("live_excess_pct"),
                }
        except Exception:
            pass
        text = "\n".join(
            [
                "======== DO ========",
                f"action={action}",
                f"exec=./etf {shown}",
                f"exit={code} dry={dry}",
                "========",
            ]
        ) + "\n"
        (out / "do.txt").write_text(text, encoding="utf-8")
        (risk / "do.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'do.txt'}")
        print(f"WROTE {risk / 'do.json'}")
    except Exception as ex:
        print(f"(do 落盘跳过: {ex})")
    return int(code)



def cmd_today(args: argparse.Namespace) -> int:
    """一页日报: 动作 + SIGNAL live/xs/THIN + 下一步 (只读汇总)."""
    import json
    from datetime import datetime

    out_dir = ROOT / "output"
    risk = out_dir / "risk_audit"
    latest_path = out_dir / "latest.json"
    live_path = risk / "shadow_live.json"
    mon_path = risk / "shadow_monitor.json"
    doc_path = risk / "doctor.json"

    lines: list[str] = []
    lines.append("======== ETF 今日速览 ========")
    lines.append(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"信号默认影子: {_DEFAULT_SHADOW}")
    lines.append("----------------------------")

    latest: dict = {}
    if latest_path.exists():
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception as e:
            lines.append(f"latest.json 解析失败: {e}")
    else:
        lines.append("缺 latest.json → ./etf refresh")

    def _sig_from_latest(obj: dict | None) -> dict | None:
        if isinstance(obj, dict) and isinstance(obj.get("signal_live"), dict):
            sl = obj.get("signal_live") or {}
            if sl.get("live_return_pct") is not None or sl.get("live_excess_pct") is not None:
                return sl
        return None

    def _sig_from_live_json() -> dict | None:
        if not live_path.exists():
            return None
        try:
            rows = json.loads(live_path.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                for rr in rows:
                    if isinstance(rr, dict) and (
                        rr.get("signal")
                        or rr.get("name") == _DEFAULT_SHADOW
                        or rr.get("is_signal_default")
                    ):
                        return rr
        except Exception as e:
            lines.append(f"shadow_live 解析失败: {e}")
        return None

    # 缺有效收益时现算 live (回写 latest.signal_live / latest.txt)
    if latest_path.exists() and _sig_from_latest(latest) is None and _sig_from_live_json() is None:
        _run(
            [
                PY,
                str(SCRIPTS / "shadow_live.py"),
                "--json-out",
                str(live_path),
                "--text-out",
                str(out_dir / "shadow_live.txt"),
            ]
        )
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if latest:
        lines.append(f"生产动作: {latest.get('action') or '—'}")
        lines.append(
            f"市场: {'开' if latest.get('market_ok') else '关'}  "
            f"宽度={latest.get('breadth')}  配置={latest.get('config')}  "
            f"信号时间: {latest.get('time') or '—'}  "
            f"行情截至: {latest.get('market_asof') or '—'}"
        )
        # latest 过旧 / 行情滞后
        try:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from etf_rotation.calendar_util import resolve_trading_day

            td = resolve_trading_day()
            td_date = str((td or {}).get("date") or "")[:10]
            ld = str(latest.get("time") or "")[:10]
            asof = str(latest.get("market_asof") or (td or {}).get("data_asof") or "")[:10]
            if (td or {}).get("data_lag") or (asof and td_date and asof < td_date):
                lines.append(
                    f"⚠ 行情滞后: wall/交易日 {td_date} > 行情截至 {asof or (td or {}).get('data_asof')} "
                    f"(nav/live 以行情日为准)"
                )
            if (td or {}).get("is_trading_day") and td_date and ld and ld < td_date:
                lines.append(
                    f"⚠ latest 过旧: 信号日 {ld} < 交易日 {td_date} → "
                    f"./etf refresh"
                )
        except Exception:
            pass
        reasons = latest.get("reasons") or []
        if reasons:
            lines.append("原因:")
            for r in reasons[:4]:
                lines.append(f"  · {r}")
        sh = latest.get("shadow") or {}
        lines.append(f"研究影子动作: {sh.get('action') or '—'}")
        te = None
        dec = sh.get("decision") or {}
        if isinstance(dec, dict):
            te = dec.get("target_exposure")
            if te is None and isinstance(dec.get("exposure"), dict):
                te = (dec.get("exposure") or {}).get("target_exposure")
        if te is not None:
            try:
                te_pct = float(te) * 100 if abs(float(te)) <= 1.5 else float(te)
                lines.append(f"研究目标暴露: {te_pct:.1f}%")
            except Exception:
                pass

    # SIGNAL live / xs / THIN (优先 latest.signal_live, 回退 shadow_live.json)
    lines.append("-------- 有效收益 (SIGNAL) --------")
    sig = _sig_from_latest(latest) or _sig_from_live_json()
    if not sig:
        lines.append("无 live 产物 → ./etf live")
    else:
        lr = sig.get("live_return_pct")
        xs = sig.get("live_excess_pct")
        br = sig.get("bench_return_pct")
        dl = sig.get("days_live")
        if dl is None:
            dl = sig.get("live_n_rets")
        thin = sig.get("thin_live")
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
        lines.append(f"  策略: {sig.get('name')}")
        lines.append(f"  live={lr_s}{tag}  xs={xs_s}  bench={br_s}")
        lines.append(
            f"  from={sig.get('live_start') or '—'}  Lrets={dl if dl is not None else '—'}  "
            f"持仓={sig.get('holdings') or '—'}"
        )
        _asof = sig.get("market_asof") or latest.get("market_asof")
        if _asof or sig.get("data_lag"):
            lines.append(
                f"  行情截至: {_asof or '—'}"
                + ("  DATA_LAG" if sig.get("data_lag") else "")
            )
        if thin:
            if dl is not None and int(dl) == 0:
                lines.append("  注: Lrets=0 表示仅有锚日、尚无 live 样本日 (常见于 DATA_LAG 或刚暖机)")
            lines.append("  注: THIN=样本<5日; DATA_LAG 时等行情更新后再判 xs")

    # monitor alerts
    lines.append("-------- 监控告警 --------")
    if mon_path.exists():
        try:
            mon = json.loads(mon_path.read_text(encoding="utf-8"))
            err_n = mon.get("alert_error_n")
            warn_n = mon.get("alert_warn_n")
            lines.append(f"  error={err_n} warn={warn_n} ok={mon.get('ok')}")
        except Exception as e:
            lines.append(f"  解析失败: {e}")
    else:
        lines.append("  无 monitor → ./etf monitor")

    # doctor
    if doc_path.exists():
        try:
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
            lines.append(
                f"-------- doctor --------\n"
                f"  ok={doc.get('ok')} err={doc.get('error_n')} warn={doc.get('warn_n')}"
            )
        except Exception:
            pass

    lines.append("-------- 下一步 --------")
    stale = any("latest 过旧" in ln for ln in lines)
    lag = any("行情滞后" in ln or "DATA_LAG" in ln for ln in lines)
    if stale:
        lines.append("  ./etf refresh   # 优先: 刷过旧信号")
    if lag and not stale:
        lines.append("  ./etf asof     # 优先: DATA_LAG 取证, 等行情")
        lines.append("  # 勿反复 refresh 空转; 行情更新后再判 xs")
    elif lag and stale:
        lines.append("  ./etf asof     # refresh 后仍 DATA_LAG 时取证")
    lines.append("  ./etf yield    # 有效收益 live%/xs%")
    lines.append("  ./etf brief    # 三合一速览")
    lines.append("  ./etf data     # 行情状态/决策")
    lines.append("  ./etf next     # 唯一推荐下一步")
    lines.append("  ./etf preview")
    lines.append("  ./etf live")
    lines.append("  ./etf check --checks data_asof,asof")
    if not stale and not lag:
        lines.append("  ./etf refresh   # 日常预演")
    # 距可判 / ETA (真实有效收益可读性)
    try:
        dtr_t, eta_t, lvl_t = _load_ready_eta()
        if dtr_t is None and isinstance(sig, dict):
            dtr_t = _days_to_ready_from_live(sig.get("days_live"))
            lag_t = bool((latest or {}).get("data_lag") or (sig or {}).get("data_lag"))
            eta_t = _eta_ready_note(dtr_t, lag_t)
        if dtr_t is not None:
            lines.append(f"距可判: {dtr_t} 交易日{('  '+str(lvl_t)) if lvl_t else ''}")
            if eta_t:
                lines.append(f"ETA: {eta_t}")
            lines.append("轨迹: ./etf progress")
    except Exception:
        pass
    lines.append("口径: 有效收益=live%+xs%; DATA_LAG 等行情; 生产 c01 冻结")
    lines.append("========")

    text = "\n".join(lines)
    print(text)

    # 可选落盘
    if not getattr(args, "no_write", False):
        out_txt = out_dir / "today.txt"
        out_json = risk / "today.json"
        risk.mkdir(parents=True, exist_ok=True)
        out_txt.write_text(text + "\n", encoding="utf-8")
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "signal_shadow": _DEFAULT_SHADOW,
            "latest_action": (latest or {}).get("action"),
            "market_ok": (latest or {}).get("market_ok"),
            "signal_live": sig,
            "days_to_ready": locals().get("dtr_t"),
            "eta_note": locals().get("eta_t"),
            "ready_level": locals().get("lvl_t"),
            "text": text,
        }
        out_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out_txt}")
        print(f"WROTE {out_json}")
    return 0



def cmd_daily(args: argparse.Namespace) -> int:
    steps = args.steps or "pull,signal,monitor,compare,live,summary,status,today,asof,yield,brief,data,next,go,ready,digest,progress,pulse,email,pages"
    cmd = [
        PY,
        str(SCRIPTS / "run_pipeline.py"),
        "--steps",
        steps,
        "--strategy",
        args.strategy,
        "--shadow",
        args.shadow,
        "--pages-out",
        str(ROOT / "output" / "site"),
    ]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.require_trading_day:
        cmd.append("--require-trading-day")
    if args.monitor_fail_on_alert:
        cmd.append("--monitor-fail-on-alert")
    if args.append_shadow_email:
        cmd.append("--append-shadow-email")
    if args.warmup:
        cmd.append("--warmup")
    code = _run(cmd)
    # 日更收口: 有效收益一行
    try:
        import json
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from etf_rotation.calendar_util import resolve_trading_day

        td = resolve_trading_day()
        latest_p = ROOT / "output" / "latest.json"
        ypath = ROOT / "output" / "risk_audit" / "yield.json"
        lj = json.loads(latest_p.read_text(encoding="utf-8")) if latest_p.exists() else {}
        yj = json.loads(ypath.read_text(encoding="utf-8")) if ypath.exists() else {}
        sl = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else None
        if not sl and isinstance(yj, dict):
            sl = yj.get("signal_live") if isinstance(yj.get("signal_live"), dict) else yj
        asof = (lj or {}).get("market_asof") or (yj or {}).get("market_asof") or td.get("data_asof")
        lag = bool(td.get("data_lag") or (yj or {}).get("data_lag"))
        print("-------- daily 摘要 --------", flush=True)
        print(
            f"  动作: {(lj or {}).get('action') or '—'}  行情截至: {asof or '—'}"
            + ("  DATA_LAG" if lag else ""),
            flush=True,
        )
        if isinstance(sl, dict) and (
            sl.get("live_return_pct") is not None or sl.get("live_excess_pct") is not None
        ):
            lr, xs, dl = sl.get("live_return_pct"), sl.get("live_excess_pct"), sl.get("days_live")
            thin = sl.get("thin_live")
            try:
                lr_s = f"{float(lr):+.3f}%" if lr is not None else "—"
            except Exception:
                lr_s = "—"
            try:
                xs_s = f"{float(xs):+.3f}%" if xs is not None else "—"
            except Exception:
                xs_s = "—"
            tag = " THIN" if thin or (dl is not None and int(dl) < 5) else ""
            print(f"  live={lr_s}{tag}  xs={xs_s}  Lrets={dl if dl is not None else '—'}", flush=True)
        else:
            print("  live=— (无 signal_live) → ./etf yield", flush=True)
        # data decision
        dpath = ROOT / "output" / "risk_audit" / "data_status.json"
        if dpath.exists():
            try:
                dd = json.loads(dpath.read_text(encoding="utf-8"))
                if isinstance(dd, dict) and dd.get("decision"):
                    print(
                        f"  decision={dd.get('decision')}  "
                        f"→ ./etf next",
                        flush=True,
                    )
            except Exception:
                pass
        print("  下一步: ./etf pulse | ./etf digest | ./etf go", flush=True)
        print("  打开: ./etf next | brief | open --launch site", flush=True)
        print("----------------------------", flush=True)
    except Exception as ex:
        print(f"(daily 摘要跳过: {ex})", flush=True)
    return code




def cmd_refresh(args: argparse.Namespace) -> int:
    """一键刷过旧信号: daily --dry-run (不改生产仓, 更新研究影子/live/status/pages)."""
    # DATA_LAG 且信号未过旧时默认拦截空转 (可用 --force)
    if not getattr(args, "force", False):
        try:
            import json
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from etf_rotation.calendar_util import resolve_trading_day

            td = resolve_trading_day()
            latest_p = ROOT / "output" / "latest.json"
            lj = json.loads(latest_p.read_text(encoding="utf-8")) if latest_p.exists() else {}
            latest_day = str((lj or {}).get("time") or "")[:10]
            td_date = str(td.get("date") or "")
            stale = bool(latest_day and td_date and latest_day < td_date)
            lag = bool(td.get("data_lag"))
            if lag and not stale:
                print("======== refresh 拦截 ========")
                print(f"DATA_LAG: 行情截至 {td.get('data_asof')} · 信号日 {latest_day or '—'} 未过旧")
                print("空转 refresh 不会增加 live 样本; 请:")
                print("  ./etf pulse")
                print("  ./etf next")
                print("  ./etf wait-asof  # 轮询直到 asof 推进")
                print("  ./etf pull")
                print("  ./etf data")
                print("  ./etf refresh --force   # 强制仍要刷")
                print("==============================")
                return 3
        except Exception as ex:
            print(f"(refresh 门控跳过: {ex})")
    # 复用 daily 默认参数
    ns = argparse.Namespace(
        dry_run=True,
        warmup=bool(getattr(args, "warmup", False)),
        strategy=getattr(args, "strategy", "c01") or "c01",
        shadow=getattr(args, "shadow", _DEFAULT_SHADOW) or _DEFAULT_SHADOW,
        steps=getattr(args, "steps", "") or "",
        require_trading_day=True,
        monitor_fail_on_alert=bool(getattr(args, "monitor_fail_on_alert", True)),
        append_shadow_email=bool(getattr(args, "append_shadow_email", True)),
    )
    print("refresh = daily --dry-run (生产 c01 不改仓)", flush=True)
    code = cmd_daily(ns)
    # 收口摘要: market_asof / DATA_LAG / live
    try:
        import json
        from pathlib import Path as _P
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from etf_rotation.calendar_util import resolve_trading_day

        td = resolve_trading_day()
        latest_p = ROOT / "output" / "latest.json"
        lj = json.loads(latest_p.read_text(encoding="utf-8")) if latest_p.exists() else {}
        sl = lj.get("signal_live") if isinstance(lj, dict) else None
        asof = (lj or {}).get("market_asof") or td.get("data_asof")
        lag = bool(td.get("data_lag"))
        print("-------- refresh 摘要 --------", flush=True)
        print(
            f"  信号时间: {(lj or {}).get('time') or '—'}  行情截至: {asof or '—'}"
            + ("  DATA_LAG" if lag else ""),
            flush=True,
        )
        print(f"  动作: {(lj or {}).get('action') or '—'}  市场: {'开' if (lj or {}).get('market_ok') else '关'}", flush=True)
        if isinstance(sl, dict):
            lr, xs, dl = sl.get("live_return_pct"), sl.get("live_excess_pct"), sl.get("days_live")
            thin = sl.get("thin_live")
            try:
                lr_s = f"{float(lr):+.3f}%" if lr is not None else "—"
            except Exception:
                lr_s = "—"
            try:
                xs_s = f"{float(xs):+.3f}%" if xs is not None else "—"
            except Exception:
                xs_s = "—"
            tag = " THIN" if thin or (dl is not None and int(dl) < 5) else ""
            print(f"  live={lr_s}{tag}  xs={xs_s}  Lrets={dl if dl is not None else '—'}", flush=True)
            if dl is not None and int(dl) == 0:
                print("  注: Lrets=0=仅锚日; DATA_LAG 时等行情更新后再判 xs", flush=True)
        print("  下一步: ./etf pulse | brief | open --launch site", flush=True)
        print("------------------------------", flush=True)
    except Exception as ex:
        print(f"(refresh 摘要跳过: {ex})", flush=True)
        print("下一步: ./etf pulse  # 刷后看可判/ETA", flush=True)
    return code

def cmd_signal(args: argparse.Namespace) -> int:
    cmd = [PY, str(SCRIPTS / "run_signal.py")]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.strategy:
        cmd += ["--strategy", args.strategy]
    return _run(cmd)


def cmd_monitor(args: argparse.Namespace) -> int:
    cmd = [
        PY,
        str(SCRIPTS / "shadow_monitor.py"),
        "--text-out",
        "output/shadow_monitor.txt",
        "--json-out",
        "output/risk_audit/shadow_monitor.json",
    ]
    if getattr(args, "shadows", ""):
        cmd += ["--shadows", args.shadows]
    if getattr(args, "bars", None):
        cmd += ["--bars", str(args.bars)]
    if getattr(args, "fail_on_alert", False):
        cmd.append("--fail-on-alert")
    if getattr(args, "fail_on_warn", False):
        cmd.append("--fail-on-warn")
    code = _run(cmd)
    print("下一步: ./etf pulse | ./etf status")
    return int(code)


def cmd_warmup(args: argparse.Namespace) -> int:
    cmd = [
        PY,
        str(SCRIPTS / "shadow_warmup.py"),
        "--out",
        "output/risk_audit/shadow_warmup.json",
    ]
    if getattr(args, "shadows", ""):
        cmd += ["--shadows", args.shadows]
    if getattr(args, "pool", ""):
        cmd += ["--pool", args.pool]
    if getattr(args, "tail", None) is not None:
        cmd += ["--tail", str(args.tail)]
    if getattr(args, "reset", False):
        cmd.append("--reset")
    return _run(cmd)




def cmd_pages(_: argparse.Namespace) -> int:
    return _run([PY, str(SCRIPTS / "build_pages.py"), "--out", str(ROOT / "output" / "site")])


def cmd_preview(_: argparse.Namespace) -> int:
    site = ROOT / "output" / "site" / "index.html"
    latest = ROOT / "output" / "latest.txt"
    email = ROOT / "output" / "email_preview.html"
    status = ROOT / "output" / "research_status.txt"
    compare = ROOT / "output" / "site" / "compare.html"
    live = ROOT / "output" / "site" / "live.html"
    summary = ROOT / "output" / "shadow_summary.txt"
    monitor = ROOT / "output" / "shadow_monitor.txt"
    today = ROOT / "output" / "today.txt"
    print("======== 预览路径 ========")
    print(f"面板:   {site}  ({'OK' if site.exists() else '缺失 → etf pages'})")
    print(f"对照:   {compare}  ({'OK' if compare.exists() else '→ etf compare'})")
    print(f"LIVE:   {live}  ({'OK' if live.exists() else '→ etf live'})")
    print(f"摘要:   {summary}  ({'OK' if summary.exists() else '→ etf summary'})")
    print(f"监控:   {monitor}  ({'OK' if monitor.exists() else '→ etf monitor'})")
    print(f"今日:   {today}  ({'OK' if today.exists() else '→ etf today'})")
    print(f"信号:   {latest}  ({'OK' if latest.exists() else '缺失 → etf refresh'})")
    print(f"邮件:   {email}  ({'OK' if email.exists() else '可选 → email-preview'})")
    print(f"状态:   {status}  ({'OK' if status.exists() else '→ etf status'})")
    print("==========================")
    try:
        import json as _json
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from etf_rotation.calendar_util import resolve_trading_day as _rtd
        _td = _rtd()
        _lj = {}
        _lp = ROOT / "output" / "latest.json"
        if _lp.exists():
            _lj = _json.loads(_lp.read_text(encoding="utf-8"))
        _asof = (_lj or {}).get("market_asof") or _td.get("data_asof")
        if _td.get("data_lag") or (
            _asof and str(_td.get("date") or "")[:10] > str(_asof)[:10]
        ):
            print(
                f"⚠ DATA_LAG: 行情截至 {_asof} · wall/交易日 {_td.get('date')} "
                f"(nav/live 以 asof 为准)",
                flush=True,
            )
    except Exception:
        pass
    # monitor + compare + live + summary 先于 status/today/pages
    _run(
        [
            PY,
            str(SCRIPTS / "shadow_monitor.py"),
            "--text-out",
            "output/shadow_monitor.txt",
            "--json-out",
            "output/risk_audit/shadow_monitor.json",
        ]
    )
    _run(
        [
            PY,
            str(SCRIPTS / "shadow_compare.py"),
            "--json-out",
            "output/risk_audit/shadow_compare.json",
            "--text-out",
            "output/shadow_compare.txt",
        ]
    )
    _run(
        [
            PY,
            str(SCRIPTS / "shadow_live.py"),
            "--json-out",
            "output/risk_audit/shadow_live.json",
            "--text-out",
            "output/shadow_live.txt",
        ]
    )
    _run(
        [
            PY,
            str(SCRIPTS / "shadow_summary.py"),
            "--text-out",
            "output/shadow_summary.txt",
            "--json-out",
            "output/risk_audit/shadow_summary.json",
        ]
    )
    _run(
        [
            PY,
            str(SCRIPTS / "research_status.py"),
            "--json-out",
            "output/risk_audit/research_status.json",
            "--text-out",
            "output/research_status.txt",
        ]
    )
    # today 依赖 live/monitor/doctor 产物, 在 status 后
    _run([PY, str(SCRIPTS / "etf.py"), "today"])
    code = _run([PY, str(SCRIPTS / "build_pages.py"), "--out", str(ROOT / "output" / "site")])
    print(f"\n打开面板: file://{ROOT / 'output' / 'site' / 'index.html'}")
    print(f"打开今日: file://{ROOT / 'output' / 'site' / 'today.html'}")
    print(f"打开信号: file://{ROOT / 'output' / 'site' / 'signal.html'}")
    print(f"打开状态: file://{ROOT / 'output' / 'site' / 'status.html'}")
    print(f"打开对照: file://{ROOT / 'output' / 'site' / 'compare.html'}")
    print(f"打开 LIVE: file://{ROOT / 'output' / 'site' / 'live.html'}")
    print(f"打开摘要: file://{ROOT / 'output' / 'site' / 'summary.html'}")
    print(f"打开监控: file://{ROOT / 'output' / 'site' / 'monitor.html'}")
    print(f"今日文本: {ROOT / 'output' / 'today.txt'}")
    print(f"取证 asof: {ROOT / 'output' / 'asof.txt'}")
    print(f"有效收益: {ROOT / 'output' / 'yield.txt'}")
    print(f"三合一: {ROOT / 'output' / 'brief.txt'}")
    print(f"行情状态: {ROOT / 'output' / 'data_status.txt'}")
    print(f"下一步: {ROOT / 'output' / 'next.txt'}")
    print("打开: ./etf open --launch site | brief")
    return code


def cmd_email_preview(args: argparse.Namespace) -> int:
    cmd = [PY, str(SCRIPTS / "send_email.py"), "--dry-print"]
    if args.append_all:
        cmd += [
            "--append-shadow",
            "--append-alerts",
            "--append-status",
            "--append-compare",
            "--append-live",
            "--append-today",
            "--append-asof",
            "--append-yield",
            "--append-brief",
            "--append-data",
            "--append-next",
            "--append-go",
            "--append-ready",
            "--append-digest",
            "--append-eod",
            "--append-progress",
            "--append-pulse",
        ]
    return _run(cmd)


def cmd_compare(_: argparse.Namespace) -> int:
    return _run(
        [
            PY,
            str(SCRIPTS / "shadow_compare.py"),
            "--json-out",
            "output/risk_audit/shadow_compare.json",
            "--text-out",
            "output/shadow_compare.txt",
        ]
    )

    print("下一步: ./etf pulse | ./etf yield | ./etf progress")

def cmd_live(_: argparse.Namespace) -> int:
    code = _run(
        [
            PY,
            str(SCRIPTS / "shadow_live.py"),
            "--json-out",
            "output/risk_audit/shadow_live.json",
            "--text-out",
            "output/shadow_live.txt",
            "--write-states",
        ]
    )
    print("下一步: ./etf pulse | ./etf yield | ./etf progress")
    return int(code)


def cmd_summary(args: argparse.Namespace) -> int:
    cmd = [
        PY,
        str(SCRIPTS / "shadow_summary.py"),
        "--text-out",
        "output/shadow_summary.txt",
        "--json-out",
        "output/risk_audit/shadow_summary.json",
    ]
    if getattr(args, "names", ""):
        cmd += ["--names", args.names]
    code = _run(cmd)
    print("下一步: ./etf pulse | ./etf status")
    return int(code)


def cmd_check(args: argparse.Namespace) -> int:
    """研究健康检查 (默认 quick: 跳过 long_anchor/etf_soft 重检)."""
    cmd = [PY, str(SCRIPTS / "research_healthcheck.py")]
    if getattr(args, "quick", True) and not getattr(args, "full", False):
        cmd.append("--quick")
    if getattr(args, "checks", ""):
        cmd += ["--checks", args.checks]
    if getattr(args, "skip_warmup", False):
        cmd.append("--skip-warmup")
    return _run(cmd)





def cmd_yield(_: argparse.Namespace) -> int:
    """有效收益一页: live%/xs%/THIN/Lrets (真实日更段, 非全样本)."""
    import json
    from datetime import datetime

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation.calendar_util import resolve_trading_day

    td = resolve_trading_day()
    latest_p = ROOT / "output" / "latest.json"
    live_p = ROOT / "output" / "risk_audit" / "shadow_live.json"
    asof_p = ROOT / "output" / "risk_audit" / "asof.json"
    lj: dict = {}
    if latest_p.exists():
        try:
            lj = json.loads(latest_p.read_text(encoding="utf-8"))
        except Exception as ex:
            print(f"latest.json 解析失败: {ex}")
            lj = {}
    sl = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else {}
    # fallback shadow_live SIGNAL row
    if not sl and live_p.exists():
        try:
            rows = json.loads(live_p.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                for rr in rows:
                    if isinstance(rr, dict) and (
                        rr.get("signal") or rr.get("name") == _DEFAULT_SHADOW
                    ):
                        sl = rr
                        break
        except Exception:
            pass

    asof = (
        (sl.get("market_asof") if sl else None)
        or lj.get("market_asof")
        or td.get("data_asof")
    )
    lag = sl.get("data_lag") if sl else None
    if lag is None:
        lag = td.get("data_lag")
    lag = bool(lag)

    def _pct(v):
        try:
            return f"{float(v):+.3f}%" if v is not None else "—"
        except Exception:
            return "—"

    lr = sl.get("live_return_pct") if sl else None
    xs = sl.get("live_excess_pct") if sl else None
    br = sl.get("bench_return_pct") if sl else None
    dl = sl.get("days_live") if sl else None
    if dl is None and sl:
        dl = sl.get("live_n_rets")
    thin = sl.get("thin_live") if sl else None
    if thin is None and dl is not None:
        try:
            thin = int(dl) < 5
        except Exception:
            thin = None

    print("======== 有效收益 (live段) ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(
        f"交易日: {td.get('date')}  行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}"
    )
    print(
        f"信号: {lj.get('time') or '—'}  动作: {lj.get('action') or '—'}  "
        f"市场: {'开' if lj.get('market_ok') else '关'}"
    )
    if sl:
        tag = " THIN" if thin else ""
        print(f"策略: {sl.get('name') or _DEFAULT_SHADOW}")
        print(f"  live={_pct(lr)}{tag}  xs={_pct(xs)}  bench={_pct(br)}")
        print(
            f"  from={sl.get('live_start') or '—'}  Lrets={dl if dl is not None else '—'}  "
            f"持仓={sl.get('holdings') or '—'}  净值={sl.get('total_value') or '—'}"
        )
        if dl is not None:
            try:
                if int(dl) == 0:
                    print("  注: Lrets=0=仅锚日, 尚无 live 样本日")
            except Exception:
                pass
        if thin:
            print("  注: THIN=样本<5日, xs 勿过度解读")
        if lag:
            print("  注: DATA_LAG 时以 asof 为准, 等行情后再判 xs")
    else:
        print("无 signal_live → ./etf live")
    # 真实有效收益门控
    dtr_y, eta_y, lvl_y = _load_ready_eta()
    if dtr_y is None and sl:
        dtr_y = _days_to_ready_from_live(dl)
        eta_y = _eta_ready_note(dtr_y, lag)
    if dtr_y is not None:
        print(f"距可判: {dtr_y} 交易日 (Lrets≥5){('  level='+str(lvl_y)) if lvl_y else ''}")
        if eta_y:
            print(f"ETA: {eta_y}")
    readable = bool(lvl_y == "READY" and not lag)
    if not readable:
        print(
            f"门控: readable_yield=false (level={lvl_y or '—'} lag={lag}) "
            f"→ 勿把 xs 当真实有效收益; ./etf do --dry-run"
        )
    else:
        print("门控: readable_yield=true → live%+xs% 可作为真实有效收益")
    print("口径: 有效收益=live%+xs% (锚点→asof); 非全样本 return_pct")
    print("下一步: ./etf do | ./etf pulse --quiet")
    print("========")

    # 落盘
    try:
        out_dir = ROOT / "output"
        risk = out_dir / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        text = "\n".join(
            [
                "======== 有效收益 (live段) ========",
                f"时间: {datetime.now().isoformat(timespec='seconds')}",
                f"行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}",
                f"动作: {lj.get('action') or '—'}  市场: {'开' if lj.get('market_ok') else '关'}",
                f"策略: {(sl or {}).get('name') or _DEFAULT_SHADOW}",
                f"live={lr} xs={xs} bench={br} Lrets={dl} thin={thin}",
                f"from={(sl or {}).get('live_start')} 持仓={(sl or {}).get('holdings')}",
                f"days_to_ready={locals().get('dtr_y')} eta={locals().get('eta_y') or ''}",
                "========",
            ]
        ) + "\n"
        (out_dir / "yield.txt").write_text(text, encoding="utf-8")
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "market_asof": asof,
            "data_lag": lag,
            "action": lj.get("action"),
            "market_ok": lj.get("market_ok"),
            "latest_time": lj.get("time"),
            "signal_live": sl or None,
            "live_return_pct": lr,
            "live_excess_pct": xs,
            "bench_return_pct": br,
            "days_live": dl,
            "thin_live": thin,
            "days_to_ready": locals().get("dtr_y"),
            "eta_note": locals().get("eta_y"),
            "readable_yield": locals().get("readable"),
            "text": text,
        }
        (risk / "yield.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out_dir / 'yield.txt'}")
        print(f"WROTE {risk / 'yield.json'}")
    except Exception as ex:
        print(f"(yield 落盘跳过: {ex})")
    return 0


def cmd_asof(_: argparse.Namespace) -> int:
    """一页取证: 行情截至 / DATA_LAG / SIGNAL live (只读)."""
    import json
    from datetime import datetime

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation.calendar_util import resolve_trading_day

    td = resolve_trading_day()
    latest_p = ROOT / "output" / "latest.json"
    pipe_p = ROOT / "output" / "risk_audit" / "pipeline_last.json"
    site_p = ROOT / "output" / "site" / "site_meta.json"
    lj = {}
    if latest_p.exists():
        try:
            lj = json.loads(latest_p.read_text(encoding="utf-8"))
        except Exception as ex:
            print(f"latest.json 解析失败: {ex}")
            lj = {}
    sl = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else {}
    pipe = {}
    if pipe_p.exists():
        try:
            pipe = json.loads(pipe_p.read_text(encoding="utf-8"))
        except Exception:
            pipe = {}
    site = {}
    if site_p.exists():
        try:
            site = json.loads(site_p.read_text(encoding="utf-8"))
        except Exception:
            site = {}

    asof = (
        sl.get("market_asof")
        or lj.get("market_asof")
        or pipe.get("data_asof")
        or site.get("market_asof")
        or td.get("data_asof")
    )
    lag = sl.get("data_lag")
    if lag is None:
        lag = pipe.get("data_lag")
    if lag is None:
        lag = site.get("data_lag")
    if lag is None:
        lag = td.get("data_lag")
    lag = bool(lag)

    print("======== 行情/收益取证 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(
        f"交易日: {td.get('is_trading_day')} date={td.get('date')} "
        f"source={td.get('source')}"
    )
    print(f"行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}")
    print(f"信号时间: {lj.get('time') or '—'}  动作: {lj.get('action') or '—'}  市场: {'开' if lj.get('market_ok') else '关'}")
    if sl:
        lr, xs, dl = sl.get("live_return_pct"), sl.get("live_excess_pct"), sl.get("days_live")
        thin = sl.get("thin_live")
        try:
            lr_s = f"{float(lr):+.3f}%" if lr is not None else "—"
        except Exception:
            lr_s = "—"
        try:
            xs_s = f"{float(xs):+.3f}%" if xs is not None else "—"
        except Exception:
            xs_s = "—"
        tag = " THIN" if thin or (dl is not None and int(dl) < 5) else ""
        print(f"SIGNAL: {sl.get('name') or '—'}")
        print(f"  live={lr_s}{tag}  xs={xs_s}  Lrets={dl if dl is not None else '—'}  持仓={sl.get('holdings') or '—'}")
        if dl is not None:
            try:
                if int(dl) == 0:
                    print("  注: Lrets=0=仅锚日, 尚无 live 样本日")
            except Exception:
                pass
    else:
        print("SIGNAL: 无 signal_live → ./etf live")
    print(
        f"pipeline_last: stamp={pipe.get('stamp') or '—'} ok={pipe.get('ok')} "
        f"asof={pipe.get('data_asof') or '—'} lag={pipe.get('data_lag')}"
    )
    print(
        f"site_meta: asof={site.get('market_asof') or '—'} lag={site.get('data_lag')} "
        f"built={site.get('built_at') or '—'}"
    )
    if lag:
        print("读法: DATA_LAG 时 nav/live 以 asof 为准, 等行情更新后再判 xs")
        print("下一步: ./etf wait-asof | ./etf go ; 轨迹 ./etf progress")
    elif str(lj.get("time") or "")[:10] and str(td.get("date") or "")[:10] and str(lj.get("time") or "")[:10] < str(td.get("date") or "")[:10]:
        print("下一步: ./etf refresh  # latest 过旧")
    else:
        print("下一步: ./etf today | status | progress")
    dtr_a, eta_a, lvl_a = _load_ready_eta()
    if dtr_a is None and sl:
        dtr_a = _days_to_ready_from_live(sl.get("days_live"))
        eta_a = _eta_ready_note(dtr_a, lag)
    if dtr_a is not None:
        print(f"距可判: {dtr_a} 交易日{('  '+str(lvl_a)) if lvl_a else ''}")
        if eta_a:
            print(f"ETA: {eta_a}")
        print("轨迹: ./etf progress")
    print("========")
    # 落盘取证
    try:
        out_dir = ROOT / "output"
        risk = out_dir / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        text_lines = [
            "======== 行情/收益取证 ========",
            f"时间: {datetime.now().isoformat(timespec='seconds')}",
            f"交易日: {td.get('is_trading_day')} date={td.get('date')} source={td.get('source')}",
            f"行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}",
            f"信号时间: {lj.get('time') or '—'}  动作: {lj.get('action') or '—'}  市场: {'开' if lj.get('market_ok') else '关'}",
        ]
        if sl:
            text_lines.append(f"SIGNAL: {sl.get('name') or '—'}")
            text_lines.append(
                f"  live={sl.get('live_return_pct')} xs={sl.get('live_excess_pct')} "
                f"Lrets={sl.get('days_live')} thin={sl.get('thin_live')} 持仓={sl.get('holdings')}"
            )
        text_lines.append(
            f"pipeline_last: stamp={pipe.get('stamp')} ok={pipe.get('ok')} "
            f"asof={pipe.get('data_asof')} lag={pipe.get('data_lag')}"
        )
        text_lines.append(
            f"site_meta: asof={site.get('market_asof')} lag={site.get('data_lag')}"
        )
        text_lines.append(
            f"dtr={locals().get('dtr_a')} eta={locals().get('eta_a') or ''} level={locals().get('lvl_a')}"
        )
        text_lines.append("========")
        text = "\n".join(str(x) for x in text_lines) + "\n"
        (out_dir / "asof.txt").write_text(text, encoding="utf-8")
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "trading_day": td,
            "market_asof": asof,
            "data_lag": lag,
            "latest_time": lj.get("time"),
            "action": lj.get("action"),
            "market_ok": lj.get("market_ok"),
            "signal_live": sl or None,
            "pipeline_last": {
                "stamp": pipe.get("stamp"),
                "ok": pipe.get("ok"),
                "data_asof": pipe.get("data_asof"),
                "data_lag": pipe.get("data_lag"),
            },
            "site_meta": {
                "market_asof": site.get("market_asof"),
                "data_lag": site.get("data_lag"),
                "built_at": site.get("built_at"),
            },
            "days_to_ready": locals().get("dtr_a"),
            "eta_note": locals().get("eta_a"),
            "ready_level": locals().get("lvl_a"),
            "text": text,
        }
        (risk / "asof.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out_dir / 'asof.txt'}")
        print(f"WROTE {risk / 'asof.json'}")
    except Exception as ex:
        print(f"(asof 落盘跳过: {ex})")
    return 0









def cmd_digest(args: argparse.Namespace) -> int:
    """人读一页: ready 可判性 + live/xs + decision + 唯一步骤."""
    import json
    from datetime import datetime

    # 默认轻量刷新 ready (可用 --no-refresh 跳过)
    if not getattr(args, "no_refresh", False):
        _run([PY, str(SCRIPTS / "etf.py"), "ready"])

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation.calendar_util import resolve_trading_day

    td = resolve_trading_day()
    risk = ROOT / "output" / "risk_audit"

    def _load(name: str) -> dict:
        p = risk / name
        if not p.exists():
            return {}
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    ready = _load("ready.json")
    go = _load("go.json")
    nxt = _load("next.json")
    data = _load("data_status.json")
    yj = _load("yield.json")
    pull = _load("pull.json")
    latest = {}
    lp = ROOT / "output" / "latest.json"
    if lp.exists():
        try:
            latest = json.loads(lp.read_text(encoding="utf-8"))
        except Exception:
            latest = {}
    sl = latest.get("signal_live") if isinstance(latest.get("signal_live"), dict) else {}
    if not sl and yj.get("signal_live"):
        sl = yj.get("signal_live") if isinstance(yj.get("signal_live"), dict) else yj

    level = ready.get("level") or "—"
    asof = (
        ready.get("market_asof")
        or sl.get("market_asof")
        or latest.get("market_asof")
        or td.get("data_asof")
    )
    lag = ready.get("data_lag")
    if lag is None:
        lag = td.get("data_lag")
    lag = bool(lag)
    lr = ready.get("live_return_pct")
    if lr is None:
        lr = sl.get("live_return_pct")
    xs = ready.get("live_excess_pct")
    if xs is None:
        xs = sl.get("live_excess_pct")
    dl = ready.get("days_live")
    if dl is None:
        dl = sl.get("days_live")
    thin = ready.get("thin_live")
    if thin is None and dl is not None:
        try:
            thin = int(dl) < 5
        except Exception:
            thin = None
    decision = (
        nxt.get("decision")
        or go.get("decision")
        or data.get("decision")
        or ready.get("decision")
        or "—"
    )
    rec = nxt.get("recommend") or go.get("recommend") or "./etf go"
    # 统一短推荐: 未可判时指向裸 ./etf 或 wait
    if level in ("NOT_READY", "WAIT_DATA"):
        if lag:
            rec = "./etf wait --timeout 600"
        else:
            rec = "./etf"
    elif level == "THIN":
        rec = "./etf eod --timeout 1800"
    elif level == "READY" and not lag:
        rec = "./etf yield"
    action = latest.get("action") or ready.get("action") or "—"
    market_ok = latest.get("market_ok")
    if market_ok is None:
        market_ok = ready.get("market_ok")

    def _pct(v):
        try:
            return f"{float(v):+.3f}%" if v is not None else "—"
        except Exception:
            return "—"

    print("======== DIGEST ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(f"可判性: {level}{'  DATA_LAG' if lag else ''}")
    print(f"行情截至: {asof or '—'}  交易日: {td.get('date')}")
    print(
        f"动作: {action}  市场: {'开' if market_ok else '关'}  "
        f"decision: {decision}"
    )
    print(
        f"有效收益: live={_pct(lr)}{' THIN' if thin else ''}  "
        f"xs={_pct(xs)}  Lrets={dl if dl is not None else '—'}"
    )
    _dtr = ready.get("days_to_ready")
    if _dtr is None and dl is not None:
        try:
            _dtr = max(0, 5 - int(dl))
        except Exception:
            _dtr = None
    if _dtr is not None:
        print(f"距可判(样本): {_dtr} 个交易日 (Lrets≥5)")
        _eta_d = _eta_ready_note(_dtr, lag)
        if _eta_d:
            print(f"ETA: {_eta_d}")
    if isinstance(pull.get("after"), dict):
        print(
            f"pull: advanced={pull.get('advanced')} "
            f"asof={(pull.get('after') or {}).get('data_asof')}"
        )
    print(f"推荐: {rec}")
    if level in ("NOT_READY", "WAIT_DATA"):
        print("结论: 暂不可用 live%/xs% 下强结论 → ./etf pulse | ./etf wait-asof")
    elif level == "THIN":
        print("结论: 可观察方向, 样本薄 → ./etf pulse | ./etf daily --dry-run")
    elif level == "READY":
        print("结论: 可用 live%/xs% 作为真实有效收益读数 → ./etf pulse | ./etf yield")
    else:
        print("结论: 见 ready 明细")
    print("========")

    try:
        out = ROOT / "output"
        risk.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "market_asof": asof,
            "data_lag": lag,
            "decision": decision,
            "action": action,
            "market_ok": market_ok,
            "live_return_pct": lr,
            "live_excess_pct": xs,
            "days_live": dl,
            "thin_live": thin,
            "days_to_ready": _dtr,
            "eta_note": locals().get("_eta_d") or _eta_ready_note(_dtr, lag),
            "recommend": rec,
            "text": None,
        }
        lines = [
            "======== DIGEST ========",
            f"可判性: {level}",
            f"asof: {asof} lag={lag}",
            f"live={lr} xs={xs} Lrets={dl} thin={thin}",
            f"decision: {decision}",
            f"eta: {locals().get('_eta_d') or _eta_ready_note(_dtr, lag)}",
            f"推荐: {rec}",
            "========",
        ]
        text = "\n".join(lines) + "\n"
        payload["text"] = text
        (out / "digest.txt").write_text(text, encoding="utf-8")
        (risk / "digest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'digest.txt'}")
        print(f"WROTE {risk / 'digest.json'}")
        print("轨迹: ./etf pulse  |  ./etf progress")
        _append_progress(
            {
                "level": level,
                "market_asof": asof,
                "data_lag": lag,
                "days_live": dl,
                "days_to_ready": _dtr,
                "thin_live": thin,
                "live_return_pct": lr,
                "live_excess_pct": xs,
                "decision": decision,
                "action": action,
                "source": "digest",
            }
        )
    except Exception as ex:
        print(f"(digest 落盘跳过: {ex})")

    # exit by ready level
    if level == "READY":
        return 0
    if level in ("NOT_READY", "WAIT_DATA"):
        return 3
    if level == "THIN":
        return 4
    return 1



def cmd_pulse(args: argparse.Namespace) -> int:
    """一键脉搏: data + ready + progress 摘要 (真实有效收益可读性)."""
    import json
    from datetime import datetime

    no_refresh = bool(getattr(args, "no_refresh", False))
    if not no_refresh:
        _run([PY, str(SCRIPTS / "etf.py"), "data"])
        _run([PY, str(SCRIPTS / "etf.py"), "ready"])
    else:
        rp = ROOT / "output" / "risk_audit" / "ready.json"
        if not rp.exists():
            _run([PY, str(SCRIPTS / "etf.py"), "ready"])

    def _load(name: str) -> dict:
        p = ROOT / "output" / "risk_audit" / name
        if not p.exists():
            return {}
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
            return o if isinstance(o, dict) else {}
        except Exception:
            return {}

    data = _load("data_status.json")
    ready = _load("ready.json")
    nxt = _load("next.json")
    dig = _load("digest.json")
    yj = _load("yield.json")
    prog = _load("progress_latest.json")

    asof = (
        ready.get("market_asof")
        or data.get("market_asof")
        or yj.get("market_asof")
    )
    lag = ready.get("data_lag")
    if lag is None:
        lag = data.get("data_lag")
    lag = bool(lag)
    level = ready.get("level") or dig.get("level") or prog.get("level") or "—"
    decision = (
        data.get("decision")
        or nxt.get("decision")
        or ready.get("decision")
        or dig.get("decision")
        or "—"
    )
    lr = ready.get("live_return_pct")
    if lr is None:
        lr = yj.get("live_return_pct")
    xs = ready.get("live_excess_pct")
    if xs is None:
        xs = yj.get("live_excess_pct")
    dl = ready.get("days_live")
    if dl is None:
        dl = yj.get("days_live")
        if dl is None:
            dl = prog.get("days_live")
    thin = ready.get("thin_live")
    dtr = ready.get("days_to_ready")
    if dtr is None:
        dtr = dig.get("days_to_ready")
        if dtr is None:
            dtr = prog.get("days_to_ready")
        if dtr is None:
            dtr = _days_to_ready_from_live(dl)
    eta = ready.get("eta_note") or dig.get("eta_note") or yj.get("eta_note") or ""
    if not eta:
        eta = _eta_ready_note(dtr, lag)
    # 决策树: 单一 next_action (脚本友好) + 人读 recommend
    stale = bool(data.get("latest_stale"))
    if stale or decision == "refresh":
        next_action = "refresh"
        rec = "./etf refresh"
        why = "latest 过旧, 先刷信号"
    elif lag or decision == "wait_data" or level in ("NOT_READY", "WAIT_DATA"):
        next_action = "wait_asof"
        rec = "./etf wait --timeout 600"
        why = "DATA_LAG/样本不足: 等 asof 推进后再判 live%/xs%"
    elif level == "THIN" or thin:
        next_action = "accumulate"
        rec = "./etf eod --timeout 1800"
        why = "样本<5日: 每个交易日 eod/daily 积累 Lrets"
    elif level == "READY" and not lag:
        next_action = "read_yield"
        rec = "./etf yield"
        why = "可判: 以 live%+xs% 为真实有效收益"
    else:
        next_action = "doctor"
        rec = "./etf doctor"
        why = "状态不明, 先体检"

    def _pct(v):
        try:
            return f"{float(v):+.3f}%" if v is not None else "—"
        except Exception:
            return "—"

    quiet = bool(getattr(args, "quiet", False))
    json_only = bool(getattr(args, "json", False)) and quiet
    if not json_only:
        if quiet:
            # 单行摘要, 便于 cron/脚本 grep
            print(
                f"PULSE level={level}{' DATA_LAG' if lag else ''} asof={asof or '—'} "
                f"live={_pct(lr)} xs={_pct(xs)} Lrets={dl if dl is not None else '—'} "
                f"dtr={dtr if dtr is not None else '—'} action={next_action} → {rec}"
            )
            if eta:
                print(f"ETA: {eta}")
        else:
            print("======== PULSE 脉搏 ========")
            print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
            print(f"level: {level}{'  DATA_LAG' if lag else ''}")
            print(f"asof: {asof or '—'}  decision: {decision}")
            print(
                f"live={_pct(lr)}{' THIN' if thin else ''}  xs={_pct(xs)}  "
                f"Lrets={dl if dl is not None else '—'}"
            )
            if dtr is not None:
                print(f"距可判: {dtr} 交易日 (Lrets≥5)")
            if eta:
                print(f"ETA: {eta}")
            print(f"next_action: {next_action}")
            print(f"推荐: {rec}")
            print(f"原因: {why}")
            if prog:
                print(
                    f"轨迹最新: {prog.get('date')} Lrets={prog.get('days_live')} "
                    f"dtr={prog.get('days_to_ready')} src={prog.get('source')}"
                )
            print("口径: 有效收益=live%+xs% (锚点→asof); THIN/DATA_LAG 勿强解读 xs")
            print("exit: 0=READY  3=WAIT/NOT_READY  4=THIN  1=其他")
            print("========")

    if not getattr(args, "quiet", False) and not getattr(args, "json", False):
        try:
            _run([PY, str(SCRIPTS / "etf.py"), "progress", "--no-refresh", "--tail", "5"])
        except Exception:
            pass

    if level == "READY" and not lag:
        exit_code = 0
    elif level == "THIN" or (thin and not lag and decision == "ok"):
        exit_code = 4
    elif lag or decision == "wait_data" or level in ("NOT_READY", "WAIT_DATA"):
        exit_code = 3
    elif next_action == "refresh":
        exit_code = 2
    else:
        exit_code = 1

    payload = {
        "stamp": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "market_asof": asof,
        "data_lag": lag,
        "decision": decision,
        "next_action": next_action,
        "why": why,
        "live_return_pct": lr,
        "live_excess_pct": xs,
        "days_live": dl,
        "thin_live": thin,
        "days_to_ready": dtr,
        "eta_note": eta,
        "recommend": rec,
        "exit_code": exit_code,
        "readable_yield": bool(level == "READY" and not lag),
        "progress_latest": prog or None,
    }
    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        lines = [
            "======== PULSE 脉搏 ========",
            f"level: {level}",
            f"asof: {asof} lag={lag}",
            f"live={lr} xs={xs} Lrets={dl} thin={thin}",
            f"dtr={dtr} eta={eta}",
            f"decision: {decision}",
            f"next_action: {next_action}",
            f"why: {why}",
            f"推荐: {rec}",
            f"exit_code: {exit_code}",
            f"readable_yield: {bool(level == 'READY' and not lag)}",
            "========",
        ]
        text = "\n".join(lines) + "\n"
        payload["text"] = text
        (out / "pulse.txt").write_text(text, encoding="utf-8")
        (risk / "pulse.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if not quiet:
            print(f"WROTE {out / 'pulse.txt'}")
            print(f"WROTE {risk / 'pulse.json'}")
        _snapshot_progress("pulse")
    except Exception as ex:
        print(f"(pulse 落盘跳过: {ex})")

    if getattr(args, "json", False):
        if quiet:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))

    return int(exit_code)


def cmd_progress(args: argparse.Namespace) -> int:
    """查看可判性/有效收益轨迹 (Lrets 向 READY 推进)."""
    import json
    from datetime import datetime

    if not getattr(args, "no_refresh", False):
        _run([PY, str(SCRIPTS / "etf.py"), "ready"])

    risk = ROOT / "output" / "risk_audit"
    path = risk / "progress.jsonl"
    txt = ROOT / "output" / "progress.txt"
    latest_path = risk / "progress_latest.json"
    rows = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    n = int(getattr(args, "tail", 12) or 12)
    show = rows[-n:]
    trend = {}
    if len(show) >= 2:
        a, b = show[0], show[-1]
        try:
            d0 = int(a.get("days_live") or 0)
            d1 = int(b.get("days_live") or 0)
            trend = {
                "lrets_from": d0,
                "lrets_to": d1,
                "lrets_delta": d1 - d0,
                "level_from": a.get("level"),
                "level_to": b.get("level"),
            }
        except Exception:
            trend = {
                "level_from": a.get("level"),
                "level_to": b.get("level"),
            }
    if getattr(args, "json", False):
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "n": len(rows),
            "tail": n,
            "rows": show,
            "trend": trend or None,
            "latest": None,
            "path_jsonl": str(path),
            "path_txt": str(txt),
        }
        try:
            if latest_path.exists():
                payload["latest"] = json.loads(latest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("======== PROGRESS 可判性轨迹 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}  n={len(rows)} show={len(show)}")
    if not show:
        print("(暂无轨迹; 先 ./etf ready 或 ./etf eod)")
    for o in show:
        if not isinstance(o, dict):
            continue
        print(
            f"{o.get('date')} level={o.get('level')} asof={o.get('market_asof')} "
            f"Lrets={o.get('days_live')} dtr={o.get('days_to_ready')} "
            f"live={o.get('live_return_pct')} xs={o.get('live_excess_pct')} "
            f"lag={o.get('data_lag')} src={o.get('source')}"
        )
    if trend:
        if "lrets_from" in trend:
            print(
                f"样本变化: Lrets {trend['lrets_from']} → {trend['lrets_to']} "
                f"(Δ{trend['lrets_delta']})"
            )
        print(f"level变化: {trend.get('level_from')} → {trend.get('level_to')}")
    latest = None
    try:
        if latest_path.exists():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        latest = None
    if isinstance(latest, dict) and latest.get("days_to_ready") is not None:
        print(
            "ETA: "
            + _eta_ready_note(latest.get("days_to_ready"), bool(latest.get("data_lag")))
        )
    print("读法: dtr=距 READY 交易日数; DATA_LAG 时样本可能不涨")
    print("========")
    if txt.exists():
        print(f"全文: {txt}")
    return 0


def cmd_ready(_: argparse.Namespace) -> int:
    """有效收益可判性: 能否用 live%/xs% 做真实结论 (非全样本)."""
    import json
    from datetime import datetime

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation.calendar_util import resolve_trading_day

    td = resolve_trading_day()
    latest_p = ROOT / "output" / "latest.json"
    lj = {}
    if latest_p.exists():
        try:
            lj = json.loads(latest_p.read_text(encoding="utf-8"))
        except Exception:
            lj = {}
    sl = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else {}
    yj, nj, gj, dj, pj = {}, {}, {}, {}, {}
    for name, var in [
        ("yield.json", "yj"),
        ("next.json", "nj"),
        ("go.json", "gj"),
        ("data_status.json", "dj"),
        ("pull.json", "pj"),
    ]:
        fp = ROOT / "output" / "risk_audit" / name
        if fp.exists():
            try:
                obj = json.loads(fp.read_text(encoding="utf-8"))
                if name.startswith("yield"):
                    yj = obj if isinstance(obj, dict) else {}
                elif name.startswith("next"):
                    nj = obj if isinstance(obj, dict) else {}
                elif name.startswith("go"):
                    gj = obj if isinstance(obj, dict) else {}
                elif name.startswith("data"):
                    dj = obj if isinstance(obj, dict) else {}
                elif name.startswith("pull"):
                    pj = obj if isinstance(obj, dict) else {}
            except Exception:
                pass

    asof = (
        sl.get("market_asof")
        or lj.get("market_asof")
        or dj.get("market_asof")
        or td.get("data_asof")
    )
    lag = sl.get("data_lag")
    if lag is None:
        lag = dj.get("data_lag")
    if lag is None:
        lag = td.get("data_lag")
    lag = bool(lag)

    dl = sl.get("days_live")
    if dl is None and yj:
        dl = yj.get("days_live")
    thin = sl.get("thin_live")
    if thin is None and dl is not None:
        try:
            thin = int(dl) < 5
        except Exception:
            thin = True
    lr = sl.get("live_return_pct")
    xs = sl.get("live_excess_pct")
    market_ok = lj.get("market_ok")
    action = lj.get("action") or "—"
    holdings = sl.get("holdings") or "—"
    decision = nj.get("decision") or dj.get("decision") or gj.get("decision") or "—"

    # readiness levels
    blockers = []
    if lag:
        blockers.append("DATA_LAG: 行情未到 wall 日, xs 勿解读")
    if thin or (dl is not None and int(dl) < 5):
        blockers.append("THIN: live 样本<5日, 统计不稳定")
    if dl is not None and int(dl) == 0:
        blockers.append("Lrets=0: 仅锚日, 尚无 live 收益样本")
    if not sl:
        blockers.append("无 signal_live: 先 live/signal")

    # 距可判: 样本维度 (DATA_LAG 时 asof 未推进, 样本天数也可能不涨)
    days_to_ready = None
    try:
        if dl is not None:
            left = 5 - int(dl)
            days_to_ready = max(0, left)
    except Exception:
        days_to_ready = None

    if not blockers:
        level = "READY"
        level_note = "可用 live%/xs% 做真实有效收益结论"
        exit_code = 0
        days_to_ready = 0
    elif lag and (thin or (dl is not None and int(dl) < 5)):
        level = "NOT_READY"
        level_note = "先 ./etf go/--wait-asof 推进 asof, 再积累 live 样本"
        if days_to_ready is not None:
            level_note += f" (样本约还需 {days_to_ready} 个交易日≥5)"
        exit_code = 3
    elif lag:
        level = "WAIT_DATA"
        level_note = "行情滞后; 收益字段可能仅锚日 → ./etf wait-asof"
        exit_code = 3
    elif thin or (dl is not None and int(dl) < 5):
        level = "THIN"
        level_note = "asof 已齐但样本薄; 可观察方向, 勿下强结论"
        if days_to_ready is not None and days_to_ready > 0:
            level_note += f" (约再 {days_to_ready} 个交易日可 READY)"
        exit_code = 4
    else:
        level = "PARTIAL"
        level_note = "部分条件未满足"
        exit_code = 1

    def _pct(v):
        try:
            return f"{float(v):+.3f}%" if v is not None else "—"
        except Exception:
            return "—"

    print("======== READY 有效收益可判性 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(f"level: {level}")
    print(f"说明: {level_note}")
    print(
        f"交易日: {td.get('date')}  asof: {asof or '—'}{'  DATA_LAG' if lag else ''}"
    )
    print(
        f"动作: {action}  市场: {'开' if market_ok else '关'}  持仓: {holdings}"
    )
    print(
        f"YIELD: live={_pct(lr)}{' THIN' if thin else ''}  xs={_pct(xs)}  "
        f"Lrets={dl if dl is not None else '—'}"
    )
    print(f"decision: {decision}")
    if days_to_ready is not None:
        print(f"距可判(样本): {days_to_ready} 个交易日 (目标 Lrets≥5)")
        _eta = _eta_ready_note(days_to_ready, lag)
        if _eta:
            print(f"ETA: {_eta}")
    if blockers:
        print("阻塞:")
        for b in blockers:
            print(f"  · {b}")
    # 空仓+基准跌时 xs>0 的读法
    print("读法:")
    print("  · 有效收益=live%+xs% (锚点→asof), 非全样本 return_pct")
    print("  · 空仓+基准跌 → xs>0 可能=风控生效; 空仓+基准涨 → xs<0=机会成本")
    print("  · THIN/DATA_LAG/Lrets=0 → 勿过度解读 xs")
    if level in ("NOT_READY", "WAIT_DATA"):
        print("下一步: ./etf pulse")
        print("        或 ./etf wait-asof | ./etf go --timeout 600")
    elif level == "THIN":
        print("下一步: 等更多交易日; ./etf pulse | ./etf yield")
    else:
        print("下一步: ./etf pulse | ./etf yield | ./etf status")
    print("========")

    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "note": level_note,
            "market_asof": asof,
            "data_lag": lag,
            "days_live": dl,
            "thin_live": thin,
            "live_return_pct": lr,
            "live_excess_pct": xs,
            "action": action,
            "market_ok": market_ok,
            "holdings": holdings,
            "decision": decision,
            "blockers": blockers,
            "days_to_ready": days_to_ready,
            "eta_note": _eta_ready_note(days_to_ready, lag),
            "ready": level == "READY",
        }
        text = "\n".join(
            [
                "======== READY 有效收益可判性 ========",
                f"level: {level}",
                f"asof: {asof} lag={lag}",
                f"live={lr} xs={xs} Lrets={dl} thin={thin}",
                f"decision: {decision}",
                f"days_to_ready: {days_to_ready}",
                f"eta: {_eta_ready_note(days_to_ready, lag)}",
                f"blockers: {'; '.join(blockers) if blockers else '无'}",
                "========",
            ]
        ) + "\n"
        (out / "ready.txt").write_text(text, encoding="utf-8")
        (risk / "ready.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'ready.txt'}")
        print(f"WROTE {risk / 'ready.json'}")
        _append_progress(
            {
                "level": level,
                "market_asof": asof,
                "data_lag": lag,
                "days_live": dl,
                "days_to_ready": days_to_ready,
                "thin_live": thin,
                "live_return_pct": lr,
                "live_excess_pct": xs,
                "decision": decision,
                "action": action,
                "source": "ready",
            }
        )
    except Exception as ex:
        print(f"(ready 落盘跳过: {ex})")

    return exit_code



def cmd_eod(args: argparse.Namespace) -> int:
    """收盘后一键: wait-asof(可选) → daily --dry-run → digest/ready 结论."""
    import json
    from datetime import datetime

    timeout = int(getattr(args, "timeout", 1800) or 1800)
    interval = int(getattr(args, "interval", 90) or 90)
    skip_wait = bool(getattr(args, "no_wait", False))
    dry = not bool(getattr(args, "live_signal", False))  # default dry-run daily

    print("======== EOD 收盘闭环 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(f"wait={not skip_wait} timeout={timeout}s interval={interval}s dry_daily={dry}")

    wait_code = 0
    if not skip_wait:
        wait_code = _run(
            [
                PY,
                str(SCRIPTS / "etf.py"),
                "wait-asof",
                "--timeout",
                str(timeout),
                "--interval",
                str(interval),
                "--no-follow",
            ]
        )
        if wait_code == 0:
            print("eod: asof 已推进 → 重跑 signal/live/yield")
            _run(
                [
                    PY,
                    str(SCRIPTS / "run_signal.py"),
                    "--dry-run",
                    "--shadow-exec",
                ]
            )
            _run([PY, str(SCRIPTS / "etf.py"), "live"])
            _run([PY, str(SCRIPTS / "etf.py"), "yield"])
        else:
            print("eod: asof 未推进, 仍继续日更快照 (可能 THIN/LAG)")

    # daily dry-run snapshot (or full dry pipeline without long wait)
    daily_ns_cmd = [
        PY,
        str(SCRIPTS / "etf.py"),
        "daily",
        "--dry-run",
    ]
    # avoid double long pull wait - daily includes pull bench-only which is fine
    daily_code = _run(daily_ns_cmd)

    _run([PY, str(SCRIPTS / "etf.py"), "ready"])
    dig_code = _run([PY, str(SCRIPTS / "etf.py"), "digest", "--no-refresh"])

    # summary
    ready = {}
    dig = {}
    try:
        ready = json.loads((ROOT / "output" / "risk_audit" / "ready.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        dig = json.loads((ROOT / "output" / "risk_audit" / "digest.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    level = (ready or {}).get("level") or (dig or {}).get("level")
    print("-------- EOD 结论 --------")
    print(f"level={level} asof={(ready or {}).get('market_asof') or (dig or {}).get('market_asof')}")
    print(
        f"live={(ready or {}).get('live_return_pct')} xs={(ready or {}).get('live_excess_pct')} "
        f"Lrets={(ready or {}).get('days_live')} days_to_ready={(ready or {}).get('days_to_ready')}"
    )
    _eta_e = (ready or {}).get("eta_note") or _eta_ready_note(
        (ready or {}).get("days_to_ready"), bool((ready or {}).get("data_lag") or (dig or {}).get("data_lag"))
    )
    if _eta_e:
        print(f"ETA: {_eta_e}")
    _rec_e = (dig or {}).get("recommend") or "./etf digest"
    if "progress" not in str(_rec_e):
        _rec_e = f"{_rec_e}  |  ./etf progress"
    print(f"推荐: {_rec_e}")
    if level == "READY":
        print("真实有效收益可读: 以 live%/xs% 为准")
    elif level == "THIN":
        print("样本仍薄: 继续每个交易日 eod/daily 积累")
    else:
        print("尚未可强判: 检查源站 asof / 下一交易日再跑 ./etf eod")

    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "wait_code": wait_code,
            "daily_code": daily_code,
            "digest_code": dig_code,
            "level": level,
            "market_asof": (ready or {}).get("market_asof") or (dig or {}).get("market_asof"),
            "data_lag": (ready or {}).get("data_lag")
            if (ready or {}).get("data_lag") is not None
            else (dig or {}).get("data_lag"),
            "live_return_pct": (ready or {}).get("live_return_pct"),
            "live_excess_pct": (ready or {}).get("live_excess_pct"),
            "days_live": (ready or {}).get("days_live"),
            "days_to_ready": (ready or {}).get("days_to_ready"),
            "eta_note": locals().get("_eta_e") or (ready or {}).get("eta_note"),
            "decision": (dig or {}).get("decision") or (ready or {}).get("decision"),
            "recommend": locals().get("_rec_e") or (dig or {}).get("recommend"),
            "ready": ready,
            "digest": dig,
            "skipped_wait": skip_wait,
            "timeout": timeout,
        }
        text = "\n".join(
            [
                "======== EOD 收盘闭环 ========",
                f"level={level}",
                f"wait_code={wait_code} daily_code={daily_code}",
                f"live={(ready or {}).get('live_return_pct')} xs={(ready or {}).get('live_excess_pct')} "
                f"Lrets={(ready or {}).get('days_live')}",
                f"days_to_ready={(ready or {}).get('days_to_ready')}",
                f"eta={_eta_e if '_eta_e' in dir() else (ready or {}).get('eta_note')}",
                f"推荐: {locals().get('_rec_e') or ((dig or {}).get('recommend') or './etf digest')}",
                "========",
            ]
        ) + "\n"
        (out / "eod.txt").write_text(text, encoding="utf-8")
        (risk / "eod.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'eod.txt'}")
        print(f"WROTE {risk / 'eod.json'}")
        _append_progress(
            {
                "level": level,
                "market_asof": payload.get("market_asof"),
                "data_lag": payload.get("data_lag"),
                "days_live": payload.get("days_live"),
                "days_to_ready": payload.get("days_to_ready"),
                "thin_live": (ready or {}).get("thin_live"),
                "live_return_pct": payload.get("live_return_pct"),
                "live_excess_pct": payload.get("live_excess_pct"),
                "decision": payload.get("decision"),
                "action": (ready or {}).get("action"),
                "source": "eod",
            }
        )
    except Exception as ex:
        print(f"(eod 落盘跳过: {ex})")

    print("下一步: ./etf pulse --quiet  # 收盘后可判/ETA")
    print("========")
    if level == "READY":
        return 0
    if level == "THIN":
        return 4
    return 3 if wait_code != 0 or level in ("NOT_READY", "WAIT_DATA") else int(dig_code or daily_code or 0)


def cmd_go(args: argparse.Namespace) -> int:
    """一键闭环: pull → data → next; wait_data 时可选 wait-asof; 收口 brief."""
    import json
    from datetime import datetime

    timeout = int(getattr(args, "timeout", 600) or 600)
    interval = int(getattr(args, "interval", 60) or 60)
    do_wait = not bool(getattr(args, "no_wait", False))
    skip_pull = bool(getattr(args, "no_pull", False))

    print("======== GO 一键闭环 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(f"pull={not skip_pull} wait={do_wait} timeout={timeout}s")

    if not skip_pull:
        _run([PY, str(SCRIPTS / "etf.py"), "pull", "--bench-only"])
    _run([PY, str(SCRIPTS / "etf.py"), "data"])
    _run([PY, str(SCRIPTS / "etf.py"), "next", "--no-refresh"])

    decision = "unknown"
    try:
        nj = json.loads((ROOT / "output" / "risk_audit" / "next.json").read_text(encoding="utf-8"))
        if isinstance(nj, dict):
            decision = nj.get("decision") or decision
    except Exception:
        pass

    wait_code = 0
    if do_wait and decision == "wait_data":
        print(f"go: decision=wait_data → wait-asof timeout={timeout}")
        wait_code = _run(
            [
                PY,
                str(SCRIPTS / "etf.py"),
                "wait-asof",
                "--timeout",
                str(timeout),
                "--interval",
                str(interval),
            ]
        )
        if wait_code == 0:
            print("go: asof 已推进, 闭环完成")
        else:
            print("go: asof 未推进, 保持观望")
    elif decision == "refresh":
        print("go: decision=refresh → 请 ./etf refresh")
    elif decision == "ok":
        print("go: data ok → yield + brief")
        _run([PY, str(SCRIPTS / "etf.py"), "yield"])
        _run([PY, str(SCRIPTS / "etf.py"), "brief"])
    else:
        print(f"go: decision={decision}")

    try:
        _run([PY, str(SCRIPTS / "etf.py"), "data"])
        _run([PY, str(SCRIPTS / "etf.py"), "next", "--no-refresh"])
        _run([PY, str(SCRIPTS / "etf.py"), "brief"])
        _run([PY, str(SCRIPTS / "etf.py"), "ready"])
        _snapshot_progress("go")
    except Exception:
        pass

    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        nd, dd = {}, {}
        try:
            nd = json.loads((risk / "next.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        try:
            dd = json.loads((risk / "data_status.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        dtr_g, eta_g, lvl_g = _load_ready_eta()
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "decision": (nd or {}).get("decision") or decision,
            "data_lag": (dd or {}).get("data_lag"),
            "market_asof": (dd or {}).get("market_asof") or (nd or {}).get("market_asof"),
            "wait_code": wait_code,
            "did_wait": bool(do_wait and decision == "wait_data"),
            "recommend": (nd or {}).get("recommend"),
            "days_to_ready": dtr_g if dtr_g is not None else (nd or {}).get("days_to_ready"),
            "eta_note": eta_g or (nd or {}).get("eta_note"),
            "ready_level": lvl_g,
        }
        text_out = "\n".join(
            [
                "======== GO 一键闭环 ========",
                f"decision={payload.get('decision')}",
                f"asof={payload.get('market_asof')} lag={payload.get('data_lag')}",
                f"did_wait={payload.get('did_wait')} wait_code={wait_code}",
                f"推荐: {payload.get('recommend') or '—'}",
                "========",
            ]
        ) + "\n"
        (out / "go.txt").write_text(text_out, encoding="utf-8")
        (risk / "go.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'go.txt'}")
        print(f"WROTE {risk / 'go.json'}")
        if payload.get("days_to_ready") is not None:
            print(f"距可判: {payload.get('days_to_ready')} 交易日")
        if payload.get("eta_note"):
            print(f"ETA: {payload.get('eta_note')}")
        print("辅读: ./etf pulse --quiet | ./etf progress")
    except Exception as ex:
        print(f"(go 落盘跳过: {ex})")

    print("========")
    if do_wait and decision == "wait_data":
        return int(wait_code)
    if decision == "wait_data":
        return 3
    if decision == "refresh":
        return 4
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """一键下一步: 根据 data 决策输出唯一推荐动作 (脚本友好)."""
    import json
    from datetime import datetime

    # 可选先强刷行情
    if getattr(args, "pull", False):
        _run([PY, str(SCRIPTS / "etf.py"), "pull", "--bench-only"])
    # 先确保 data_status 新鲜 (除非 --no-refresh)
    if not getattr(args, "no_refresh", False):
        _run([PY, str(SCRIPTS / "etf.py"), "data"])

    risk = ROOT / "output" / "risk_audit" / "data_status.json"
    ds = {}
    if risk.exists():
        try:
            ds = json.loads(risk.read_text(encoding="utf-8"))
        except Exception:
            ds = {}
    decision = (ds or {}).get("decision") or "unknown"
    asof = (ds or {}).get("market_asof")
    lag = (ds or {}).get("data_lag")
    stale = (ds or {}).get("latest_stale")
    action = (ds or {}).get("action")

    # 推荐命令映射
    if decision == "refresh" or stale:
        rec = "./etf refresh"
        why = "latest 过旧, 需刷信号; 然后 ./etf"
        code_hint = 2
    elif decision == "wait_data" or lag:
        rec = "./etf wait --timeout 600"
        why = "DATA_LAG: 等 asof; 巡检 ./etf pulse --quiet"
        code_hint = 3
    elif decision == "ok":
        rec = "./etf"
        why = "数据齐: 裸 ./etf=pulse 看可判后读 yield"
        code_hint = 0
    else:
        rec = "./etf doctor"
        why = "决策未知, 先体检"
        code_hint = 1

    dtr_n, eta_n, lvl_n = _load_ready_eta()
    sl = (ds or {}).get("signal_live") if isinstance((ds or {}).get("signal_live"), dict) else {}
    if dtr_n is None and sl:
        dtr_n = _days_to_ready_from_live(sl.get("days_live"))
        eta_n = _eta_ready_note(dtr_n, bool(lag))

    print("======== NEXT 决策 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(f"decision: {decision}")
    print(f"asof: {asof or '—'}  lag: {lag}  stale: {stale}")
    print(f"动作: {action or '—'}")
    print(f"原因: {why}")
    print(f"推荐: {rec}")
    if dtr_n is not None:
        print(f"距可判: {dtr_n} 交易日{('  '+str(lvl_n)) if lvl_n else ''}")
        if eta_n:
            print(f"ETA: {eta_n}")
        print("辅读: ./etf pulse  # 可判/ETA/轨迹 最短读口")
    print("口径: 有效收益=live%+xs%; DATA_LAG 等行情; 生产 c01 冻结")
    print("========")

    try:
        out = ROOT / "output"
        riskd = out / "risk_audit"
        riskd.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "decision": decision,
            "market_asof": asof,
            "data_lag": lag,
            "latest_stale": stale,
            "action": action,
            "why": why,
            "recommend": rec,
            "days_to_ready": dtr_n,
            "eta_note": eta_n,
            "ready_level": lvl_n,
        }
        text = "\n".join(
            [
                "======== NEXT 决策 ========",
                f"decision: {decision}",
                f"asof: {asof or '—'} lag={lag} stale={stale}",
                f"推荐: {rec}",
                f"dtr={dtr_n} eta={eta_n or ''}",
                "========",
            ]
        ) + "\n"
        (out / "next.txt").write_text(text, encoding="utf-8")
        (riskd / "next.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'next.txt'}")
        print(f"WROTE {riskd / 'next.json'}")
    except Exception as ex:
        print(f"(next 落盘跳过: {ex})")

    # 可选: wait_data 时自动轮询 asof
    if getattr(args, "wait", False) and (
        decision == "wait_data" or lag or code_hint == 3
    ):
        print("next --wait: 启动 wait-asof ...")
        wcode = _run(
            [
                PY,
                str(SCRIPTS / "etf.py"),
                "wait-asof",
                "--timeout",
                str(int(getattr(args, "wait_timeout", 600) or 600)),
                "--interval",
                str(int(getattr(args, "wait_interval", 60) or 60)),
            ]
        )
        if not getattr(args, "no_refresh", False):
            _run([PY, str(SCRIPTS / "etf.py"), "data"])
        _run([PY, str(SCRIPTS / "etf.py"), "next", "--no-refresh"])
        if getattr(args, "exit_code", False):
            return int(wcode if wcode != 0 else 0)
        return int(wcode)

    if getattr(args, "exit_code", False):
        return int(code_hint)
    return 0





def cmd_wait_asof(args: argparse.Namespace) -> int:
    """轮询 pull 直到 asof 推进或超时; 可选自动 live/next/brief."""
    import json
    import time
    from datetime import datetime

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation.calendar_util import resolve_trading_day

    timeout = int(getattr(args, "timeout", 600) or 600)
    interval = int(getattr(args, "interval", 60) or 60)
    interval = max(15, interval)
    bench_only = not bool(getattr(args, "full", False))
    follow = bool(getattr(args, "follow", True))
    # default follow on success; --no-follow disables
    if getattr(args, "no_follow", False):
        follow = False

    td0 = resolve_trading_day()
    start_asof = td0.get("data_asof")
    start_lag = bool(td0.get("data_lag"))
    t0 = time.time()
    attempt = 0
    history = []

    print("======== WAIT-ASOF ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(
        f"起点 asof={start_asof or '—'} lag={start_lag} "
        f"timeout={timeout}s interval={interval}s "
        f"mode={'bench-only' if bench_only else 'full'}"
    )

    advanced = False
    cleared_lag = False
    last_asof = start_asof
    last_lag = start_lag

    while True:
        attempt += 1
        elapsed = int(time.time() - t0)
        pull_cmd = [PY, str(SCRIPTS / "etf.py"), "pull"]
        if bench_only:
            pull_cmd.append("--bench-only")
        print(f"\n-- attempt {attempt} elapsed={elapsed}s --")
        code = _run(pull_cmd)
        td = resolve_trading_day()
        asof = td.get("data_asof")
        lag = bool(td.get("data_lag"))
        last_asof, last_lag = asof, lag
        hist = {
            "attempt": attempt,
            "elapsed": elapsed,
            "pull_exit": code,
            "data_asof": asof,
            "data_lag": lag,
        }
        history.append(hist)
        print(f"asof={asof or '—'} lag={lag} pull_exit={code}")

        advanced = bool(start_asof and asof and str(asof) > str(start_asof)) or bool(
            asof and not start_asof
        )
        cleared_lag = start_lag and not lag
        if advanced or cleared_lag:
            print(f"成功: advanced={advanced} cleared_lag={cleared_lag}")
            break

        if elapsed >= timeout:
            print(f"超时: {timeout}s 内 asof 未推进 (仍 {asof})")
            break
        # sleep remaining capped by interval
        sleep_s = min(interval, max(1, timeout - elapsed))
        print(f"等待 {sleep_s}s 后重试 ...")
        time.sleep(sleep_s)

    ok = bool(advanced or cleared_lag)
    print("-------- 结果 --------")
    print(f"start_asof={start_asof} → last_asof={last_asof}")
    print(f"ok={ok} advanced={advanced} cleared_lag={cleared_lag} attempts={attempt}")

    if ok and follow:
        print("follow: signal(dry) → live → yield → next → brief ...")
        # asof 推进后必须重算信号, 否则 latest.market_asof/signal_live 仍旧
        sig_cmd = [
            PY,
            str(SCRIPTS / "run_signal.py"),
            "--dry-run",
            "--shadow-exec",
        ]
        _run(sig_cmd)
        _run([PY, str(SCRIPTS / "etf.py"), "live"])
        _run([PY, str(SCRIPTS / "etf.py"), "yield"])
        _run([PY, str(SCRIPTS / "etf.py"), "data"])
        _run([PY, str(SCRIPTS / "etf.py"), "next", "--no-refresh"])
        _run([PY, str(SCRIPTS / "etf.py"), "brief"])
        _run([PY, str(SCRIPTS / "etf.py"), "ready"])
        _snapshot_progress("wait_asof")

    # 落盘
    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "timeout": timeout,
            "interval": interval,
            "bench_only": bench_only,
            "start": {"data_asof": start_asof, "data_lag": start_lag},
            "end": {"data_asof": last_asof, "data_lag": last_lag},
            "advanced": advanced,
            "cleared_lag": cleared_lag,
            "ok": ok,
            "attempts": attempt,
            "history": history[-30:],
            "followed": bool(ok and follow),
        }
        text = "\n".join(
            [
                "======== WAIT-ASOF ========",
                f"start={start_asof} end={last_asof}",
                f"ok={ok} advanced={advanced} cleared_lag={cleared_lag}",
                f"attempts={attempt} timeout={timeout}",
                "========",
            ]
        ) + "\n"
        (out / "wait_asof.txt").write_text(text, encoding="utf-8")
        (risk / "wait_asof.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'wait_asof.txt'}")
        print(f"WROTE {risk / 'wait_asof.json'}")
    except Exception as ex:
        print(f"(wait-asof 落盘跳过: {ex})")

    print("下一步: ./etf pulse --quiet  # 看 asof 推进后可判/ETA")
    print("========")
    if ok:
        return 0
    return 3 if last_lag else 5


def cmd_pull(args: argparse.Namespace) -> int:
    """强刷行情缓存 (force_refresh), 复检 asof/DATA_LAG; 不改生产仓."""
    import concurrent.futures
    import json
    from datetime import datetime

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation import config as cfgmod
    from etf_rotation.calendar_util import resolve_trading_day
    from etf_rotation.data import clear_cache, fetch_bench, fetch_klines
    from etf_rotation.research_mainline import SIGNAL_SHADOW, extra_codes_for_strategies

    pool_name = getattr(args, "pool", "pool") or "pool"
    bars = int(getattr(args, "bars", 120) or 120)
    workers = int(getattr(args, "workers", 4) or 4)
    bench_only = bool(getattr(args, "bench_only", False))
    include_extra = not bool(getattr(args, "no_extra", False))

    td0 = resolve_trading_day(bars=max(bars, 60))
    asof0 = td0.get("data_asof")
    lag0 = bool(td0.get("data_lag"))

    pool = cfgmod.load_pool(pool_name)
    etf_list = cfgmod.pool_as_list(pool)
    codes = [c for c, _ in etf_list]
    bench = pool.get("bench") or "SH510300"
    if bench not in codes:
        codes = [bench] + codes
    if include_extra:
        try:
            for c in extra_codes_for_strategies([SIGNAL_SHADOW]) or []:
                if c not in codes:
                    codes.append(c)
        except Exception:
            pass

    print("======== PULL 强刷行情 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(f"pool={pool_name} bars={bars} workers={workers} bench={bench}")
    print(f"刷前 asof={asof0 or '—'} lag={lag0}")
    clear_cache()

    b = fetch_bench(bench, count=bars, force_refresh=True)
    bench_last = None
    if b and b.get("dates"):
        bench_last = b["dates"][-1]
        print(f"bench {bench}: n={len(b['dates'])} last={bench_last}")
    else:
        print(f"bench {bench}: FAIL")

    ok_n = 1 if b else 0
    fail: list[str] = [] if b else [bench]
    if not bench_only:
        targets = [c for c in codes if c != bench]

        def one(code: str):
            try:
                kl = fetch_klines(
                    code,
                    count=bars,
                    force_refresh=True,
                    use_disk=True,
                    use_cache=False,
                )
                last = kl[-1].get("date") if kl else None
                return code, bool(kl), last, len(kl or [])
            except Exception:
                return code, False, None, 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = [ex.submit(one, c) for c in targets]
            for fut in concurrent.futures.as_completed(futs):
                code, ok, last, n = fut.result()
                if ok:
                    ok_n += 1
                else:
                    fail.append(code)

    td1 = resolve_trading_day(bars=max(bars, 60))
    asof1 = td1.get("data_asof")
    lag1 = bool(td1.get("data_lag"))
    advanced = bool(asof0 and asof1 and str(asof1) > str(asof0)) or bool(
        asof1 and not asof0
    )
    cleared_lag = lag0 and not lag1

    print(f"刷后 asof={asof1 or '—'} lag={lag1}")
    total = 1 if bench_only else len(codes)
    print(f"成功 {ok_n}/{total}  失败 {len(fail)}")
    if fail[:8]:
        print(f"失败样例: {fail[:8]}")
    if advanced:
        print(f"asof 推进: {asof0} → {asof1}")
    elif cleared_lag:
        print("DATA_LAG 已清除")
    else:
        print("asof 未推进 (源站可能尚未出当日K线)")
    try:
        _dtr_p, _eta_p, _lvl_p = _load_ready_eta()
        if _dtr_p is not None:
            print(f"距可判: {_dtr_p} 交易日{('  '+str(_lvl_p)) if _lvl_p else ''}")
        if _eta_p:
            print(f"ETA: {_eta_p}")
    except Exception:
        pass
    print("下一步: ./etf pulse | ./etf progress | ./etf wait-asof")
    print("========")

    try:
        out = ROOT / "output"
        risk = out / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "pool": pool_name,
            "bars": bars,
            "bench": bench,
            "bench_only": bench_only,
            "before": {"data_asof": asof0, "data_lag": lag0},
            "after": {"data_asof": asof1, "data_lag": lag1},
            "advanced": advanced,
            "cleared_lag": cleared_lag,
            "ok_n": ok_n,
            "fail_n": len(fail),
            "fail": fail[:50],
            "bench_last": bench_last,
        }
        text = "\n".join(
            [
                "======== PULL 强刷行情 ========",
                f"刷前 asof={asof0} lag={lag0}",
                f"刷后 asof={asof1} lag={lag1}",
                f"advanced={advanced} cleared_lag={cleared_lag}",
                f"ok={ok_n} fail={len(fail)}",
                "========",
            ]
        ) + "\n"
        (out / "pull.txt").write_text(text, encoding="utf-8")
        (risk / "pull.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out / 'pull.txt'}")
        print(f"WROTE {risk / 'pull.json'}")
    except Exception as ex:
        print(f"(pull 落盘跳过: {ex})")

    if advanced or cleared_lag:
        # asof 推进: 默认 follow (人用 ./etf pull); pipeline 传 --no-follow
        do_follow = not bool(getattr(args, "no_follow", False))
        if do_follow:
            print("follow: signal(dry+shadow) → live → yield → ready → progress ...")
            _run([
                PY,
                str(SCRIPTS / "run_signal.py"),
                "--dry-run",
                "--shadow-exec",
            ])
            _run([PY, str(SCRIPTS / "etf.py"), "live"])
            _run([PY, str(SCRIPTS / "etf.py"), "yield"])
            _run([PY, str(SCRIPTS / "etf.py"), "data"])
            _run([PY, str(SCRIPTS / "etf.py"), "next", "--no-refresh"])
            _run([PY, str(SCRIPTS / "etf.py"), "ready"])
            _snapshot_progress("pull")
        else:
            # 仅记事件; 调用方 (pipeline) 后续会跑 signal
            try:
                _run([PY, str(SCRIPTS / "etf.py"), "ready"])
            except Exception:
                pass
            _snapshot_progress("pull_nofollow")
    else:
        # 未推进也记一条事件 (同日 source 去重)
        try:
            _snapshot_progress("pull_noadv")
        except Exception:
            pass

    if getattr(args, "fail_if_lag", False) and lag1:
        return 3
    if getattr(args, "fail_if_not_advanced", False) and not advanced and not cleared_lag:
        return 5
    return 0 if ok_n > 0 else 2


def cmd_data(args: argparse.Namespace) -> int:
    """行情状态: asof / DATA_LAG / 是否该 refresh (脚本友好)."""
    import json
    from datetime import datetime

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation.calendar_util import resolve_trading_day

    td = resolve_trading_day()
    latest_p = ROOT / "output" / "latest.json"
    lj = {}
    if latest_p.exists():
        try:
            lj = json.loads(latest_p.read_text(encoding="utf-8"))
        except Exception as ex:
            print(f"latest.json 解析失败: {ex}")
            lj = {}
    sl = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else {}
    pipe_p = ROOT / "output" / "risk_audit" / "pipeline_last.json"
    pipe = {}
    if pipe_p.exists():
        try:
            pipe = json.loads(pipe_p.read_text(encoding="utf-8"))
        except Exception:
            pipe = {}

    asof = (
        sl.get("market_asof")
        or lj.get("market_asof")
        or pipe.get("data_asof")
        or td.get("data_asof")
    )
    lag = sl.get("data_lag")
    if lag is None:
        lag = pipe.get("data_lag")
    if lag is None:
        lag = td.get("data_lag")
    lag = bool(lag)

    latest_time = str(lj.get("time") or "")
    latest_day = latest_time[:10] if latest_time else ""
    td_date = str(td.get("date") or "")
    stale = bool(latest_day and td_date and latest_day < td_date)

    print("======== 行情状态 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(
        f"交易日: {td.get('is_trading_day')} date={td_date} source={td.get('source')}"
    )
    print(f"行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}")
    print(f"信号时间: {latest_time or '—'}{'  STALE' if stale else ''}")
    print(f"动作: {lj.get('action') or '—'}  市场: {'开' if lj.get('market_ok') else '关'}")
    if sl:
        try:
            lr = f"{float(sl.get('live_return_pct')):+.3f}%" if sl.get('live_return_pct') is not None else "—"
        except Exception:
            lr = "—"
        try:
            xs = f"{float(sl.get('live_excess_pct')):+.3f}%" if sl.get('live_excess_pct') is not None else "—"
        except Exception:
            xs = "—"
        dl = sl.get("days_live")
        thin = sl.get("thin_live")
        tag = " THIN" if thin or (dl is not None and int(dl) < 5) else ""
        print(f"YIELD: live={lr}{tag} xs={xs} Lrets={dl if dl is not None else '—'}")
    yv = pipe.get("yield") if isinstance(pipe.get("yield"), dict) else None
    bv = pipe.get("brief") if isinstance(pipe.get("brief"), dict) else None
    if yv:
        print(
            f"pipeline.yield: live={yv.get('live_return_pct')} xs={yv.get('live_excess_pct')} "
            f"Lrets={yv.get('days_live')} asof={yv.get('market_asof')}"
        )
    if bv:
        print(
            f"pipeline.brief: asof={bv.get('market_asof')} lag={bv.get('data_lag')} "
            f"live={bv.get('live_return_pct')} xs={bv.get('live_excess_pct')}"
        )
    # 决策
    if stale:
        print("决策: latest 过旧 → ./etf refresh")
        decision = "refresh"
    elif lag:
        print("决策: DATA_LAG → ./etf wait-asof | ./etf go")
        print("辅读: ./etf pulse  # 可判/ETA/轨迹 最短读口")
        decision = "wait_data"
    else:
        print("决策: 数据齐 → ./etf pulse | ./etf digest | ./etf yield")
        decision = "ok"
    dtr_d, eta_d, lvl_d = _load_ready_eta()
    if dtr_d is None and isinstance(sl, dict):
        dtr_d = _days_to_ready_from_live(sl.get("days_live"))
        eta_d = _eta_ready_note(dtr_d, lag)
    if dtr_d is not None:
        print(f"距可判: {dtr_d} 交易日{('  '+str(lvl_d)) if lvl_d else ''}")
        if eta_d:
            print(f"ETA: {eta_d}")
    print("========")

    # 落盘
    try:
        out_dir = ROOT / "output"
        risk = out_dir / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "trading_day": {
                "date": td.get("date"),
                "is_trading_day": td.get("is_trading_day"),
                "source": td.get("source"),
                "data_asof": td.get("data_asof"),
                "data_lag": td.get("data_lag"),
            },
            "market_asof": asof,
            "data_lag": lag,
            "latest_time": latest_time or None,
            "latest_stale": stale,
            "action": lj.get("action"),
            "market_ok": lj.get("market_ok"),
            "signal_live": sl or None,
            "pipeline_yield": yv,
            "pipeline_brief": bv,
            "decision": decision,
            "days_to_ready": locals().get("dtr_d"),
            "eta_note": locals().get("eta_d"),
            "ready_level": locals().get("lvl_d"),
        }
        (risk / "data_status.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        lines = [
            "======== 行情状态 ========",
            f"行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}",
            f"信号时间: {latest_time or '—'}{'  STALE' if stale else ''}",
            f"决策: {decision}",
            f"dtr={locals().get('dtr_d')} eta={locals().get('eta_d') or ''}",
            "========",
        ]
        (out_dir / "data_status.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out_dir / 'data_status.txt'}")
        print(f"WROTE {risk / 'data_status.json'}")
    except Exception as ex:
        print(f"(data 落盘跳过: {ex})")

    if getattr(args, "fail_on_lag", False) and lag:
        return 3
    if getattr(args, "fail_on_stale", False) and stale:
        return 4
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """三合一速览: yield + asof 要点 + today 动作 (只读优先, 缺则现算)."""
    import json
    from datetime import datetime

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from etf_rotation.calendar_util import resolve_trading_day

    force = bool(getattr(args, "refresh_local", False))
    # 缺产物时现算 (轻量)
    need = []
    if force or not (ROOT / "output" / "yield.txt").exists():
        need.append("yield")
    if force or not (ROOT / "output" / "asof.txt").exists():
        need.append("asof")
    if force or not (ROOT / "output" / "today.txt").exists():
        need.append("today")
    for step in need:
        _run([PY, str(SCRIPTS / "etf.py"), step])

    td = resolve_trading_day()
    latest_p = ROOT / "output" / "latest.json"
    yj_p = ROOT / "output" / "risk_audit" / "yield.json"
    aj_p = ROOT / "output" / "risk_audit" / "asof.json"
    lj = {}
    if latest_p.exists():
        try:
            lj = json.loads(latest_p.read_text(encoding="utf-8"))
        except Exception:
            lj = {}
    yj = {}
    if yj_p.exists():
        try:
            yj = json.loads(yj_p.read_text(encoding="utf-8"))
        except Exception:
            yj = {}
    aj = {}
    if aj_p.exists():
        try:
            aj = json.loads(aj_p.read_text(encoding="utf-8"))
        except Exception:
            aj = {}
    sl = lj.get("signal_live") if isinstance(lj.get("signal_live"), dict) else None
    if not sl and isinstance(yj, dict):
        sl = yj.get("signal_live") if isinstance(yj.get("signal_live"), dict) else yj

    asof = (
        (sl or {}).get("market_asof")
        or lj.get("market_asof")
        or yj.get("market_asof")
        or aj.get("market_asof")
        or td.get("data_asof")
    )
    lag = bool(
        (sl or {}).get("data_lag")
        if (sl or {}).get("data_lag") is not None
        else (yj.get("data_lag") if yj.get("data_lag") is not None else td.get("data_lag"))
    )

    def _pct(v):
        try:
            return f"{float(v):+.3f}%" if v is not None else "—"
        except Exception:
            return "—"

    print("======== BRIEF 速览 ========")
    print(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    print(
        f"交易日: {td.get('date')}  行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}"
    )
    print(
        f"动作: {lj.get('action') or '—'}  市场: {'开' if lj.get('market_ok') else '关'}  "
        f"信号: {lj.get('time') or '—'}"
    )
    if isinstance(sl, dict) and (
        sl.get("live_return_pct") is not None or sl.get("live_excess_pct") is not None
    ):
        lr, xs, br = sl.get("live_return_pct"), sl.get("live_excess_pct"), sl.get("bench_return_pct")
        dl = sl.get("days_live")
        thin = sl.get("thin_live")
        if thin is None and dl is not None:
            try:
                thin = int(dl) < 5
            except Exception:
                thin = None
        tag = " THIN" if thin else ""
        print(f"YIELD: {sl.get('name') or _DEFAULT_SHADOW}")
        print(f"  live={_pct(lr)}{tag}  xs={_pct(xs)}  bench={_pct(br)}")
        print(
            f"  from={sl.get('live_start') or '—'}  Lrets={dl if dl is not None else '—'}  "
            f"持仓={sl.get('holdings') or '—'}"
        )
    else:
        print("YIELD: 无 signal_live → ./etf live|yield")
    # today first lines
    today_p = ROOT / "output" / "today.txt"
    if today_p.exists():
        body = [
            ln
            for ln in today_p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("====") and "下一步" not in ln and "python3" not in ln
        ][:8]
        if body:
            print("TODAY:")
            for ln in body:
                print(f"  {ln}")
    dtr_b, eta_b, lvl_b = _load_ready_eta()
    if dtr_b is None and isinstance(sl, dict):
        dtr_b = _days_to_ready_from_live(sl.get("days_live"))
        eta_b = _eta_ready_note(dtr_b, lag)
    if dtr_b is not None:
        print(f"距可判: {dtr_b} 交易日{('  '+str(lvl_b)) if lvl_b else ''}")
        if eta_b:
            print(f"ETA: {eta_b}")
    print("口径: 有效收益=live%+xs% (锚点→asof); DATA_LAG 等行情; 生产 c01 冻结")
    if lag:
        print("下一步: ./etf pulse | ./etf wait-asof | ./etf go")
    else:
        print("下一步: ./etf pulse | ./etf ready | ./etf open --launch site")
    print("========")

    # 落盘 brief
    try:
        out_dir = ROOT / "output"
        risk = out_dir / "risk_audit"
        risk.mkdir(parents=True, exist_ok=True)
        text_lines = [
            "======== BRIEF 速览 ========",
            f"时间: {datetime.now().isoformat(timespec='seconds')}",
            f"行情截至: {asof or '—'}{'  DATA_LAG' if lag else ''}",
            f"动作: {lj.get('action') or '—'}  市场: {'开' if lj.get('market_ok') else '关'}",
        ]
        if isinstance(sl, dict):
            text_lines.append(
                f"live={sl.get('live_return_pct')} xs={sl.get('live_excess_pct')} "
                f"Lrets={sl.get('days_live')} thin={sl.get('thin_live')}"
            )
        text_lines.append(
            f"dtr={locals().get('dtr_b')} eta={locals().get('eta_b') or ''}"
        )
        text_lines.append("========")
        text = "\n".join(str(x) for x in text_lines) + "\n"
        (out_dir / "brief.txt").write_text(text, encoding="utf-8")
        payload = {
            "stamp": datetime.now().isoformat(timespec="seconds"),
            "market_asof": asof,
            "data_lag": lag,
            "action": lj.get("action"),
            "market_ok": lj.get("market_ok"),
            "latest_time": lj.get("time"),
            "signal_live": sl if isinstance(sl, dict) else None,
            "days_to_ready": locals().get("dtr_b"),
            "eta_note": locals().get("eta_b"),
            "text": text,
        }
        (risk / "brief.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"WROTE {out_dir / 'brief.txt'}")
        print(f"WROTE {risk / 'brief.json'}")
    except Exception as ex:
        print(f"(brief 落盘跳过: {ex})")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """一键列出/打开关键产物路径 (today/yield/asof/status/site)."""
    import os
    import shutil
    import subprocess
    from pathlib import Path as _P

    targets = {
        "today": ROOT / "output" / "today.txt",
        "brief": ROOT / "output" / "brief.txt",
        "data": ROOT / "output" / "data_status.txt",
        "next": ROOT / "output" / "next.txt",
        "pull": ROOT / "output" / "pull.txt",
        "go": ROOT / "output" / "go.txt",
        "ready": ROOT / "output" / "ready.txt",
        "digest": ROOT / "output" / "digest.txt",
        "eod": ROOT / "output" / "eod.txt",
        "progress": ROOT / "output" / "progress.txt",
        "pulse": ROOT / "output" / "pulse.txt",
        "do": ROOT / "output" / "do.txt",
        "pulse-html": ROOT / "output" / "site" / "pulse.html",
        "wait-asof": ROOT / "output" / "wait_asof.txt",
        "yield": ROOT / "output" / "yield.txt",
        "asof": ROOT / "output" / "asof.txt",
        "status": ROOT / "output" / "research_status.txt",
        "latest": ROOT / "output" / "latest.txt",
        "live": ROOT / "output" / "shadow_live.txt",
        "ready": ROOT / "output" / "ready.txt",
        "digest": ROOT / "output" / "digest.txt",
        "progress": ROOT / "output" / "progress.txt",
        "eod": ROOT / "output" / "eod.txt",
        "go": ROOT / "output" / "go.txt",
        "data": ROOT / "output" / "data_status.txt",
        "next": ROOT / "output" / "next.txt",
        "site": ROOT / "output" / "site" / "index.html",
        "yield-html": ROOT / "output" / "site" / "yield.html",
        "asof-html": ROOT / "output" / "site" / "asof.html",
        "today-html": ROOT / "output" / "site" / "today.html",
        "brief-html": ROOT / "output" / "site" / "brief.html",
        "data-html": ROOT / "output" / "site" / "data.html",
        "next-html": ROOT / "output" / "site" / "next.html",
        "ready-html": ROOT / "output" / "site" / "ready.html",
        "digest-html": ROOT / "output" / "site" / "digest.html",
        "progress-html": ROOT / "output" / "site" / "progress.html",
        "eod-html": ROOT / "output" / "site" / "eod.html",
        "go-html": ROOT / "output" / "site" / "go.html",
    }
    which = (getattr(args, "target", "") or "all").strip().lower()
    if which in ("", "all", "*"):
        keys = list(targets.keys())
    else:
        keys = [k.strip() for k in which.split(",") if k.strip()]
        bad = [k for k in keys if k not in targets]
        if bad:
            print(f"未知目标: {bad}; 可选: {', '.join(targets)}")
            return 2

    do_open = bool(getattr(args, "launch", False))
    opener = None
    if do_open:
        for cand in ("xdg-open", "wslview", "open"):
            if shutil.which(cand):
                opener = cand
                break
        if not opener:
            print("未找到 xdg-open/wslview/open, 仅打印路径")
            do_open = False

    print("======== 关键产物 ========")
    for k in keys:
        path = targets[k]
        ok = path.exists()
        mark = "OK" if ok else "缺"
        print(f"  [{mark}] {k:10s} {path}")
        if ok and (k.endswith("html") or path.suffix == ".html"):
            print(f"         file://{path}")
        if do_open and ok and opener:
            try:
                subprocess.Popen(
                    [opener, str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as ex:
                print(f"         open fail: {ex}")
    print("提示: ./etf open --launch site")
    print("      ./etf open pulse | pulse-html | progress | ready")
    print("      ./etf pulse | progress --json | yield")
    print("========")
    return 0


def cmd_help(_: argparse.Namespace) -> int:
    print(
        f"""
ETF 轮动 · 统一入口
  ./etf            默认 digest (项目根包装, 免 PYTHONPATH)
  ./etf eod        收盘后一键等 asof 并日更
==================
  today           今日一页速览 (动作+live/xs/THIN)
  asof            行情截至/DATA_LAG/live 一页取证
  yield           有效收益一页 (live%/xs%/THIN)
  open            关键产物路径 (可 --launch 打开)
  brief           三合一速览 (yield+asof+today)
  data            行情状态 asof/DATA_LAG/决策
  pull            强刷行情缓存并复检 asof
  wait-asof       轮询 pull 直到 asof 推进
  next            一键下一步 (唯一推荐动作)
  go              一键闭环 (pull→next→wait-asof)
  eod             收盘后一键 (wait-asof→daily→digest)
  wait            wait-asof 别名
  ready           有效收益可判性 (READY/THIN/WAIT_DATA)
  pulse           一键脉搏 (默认裸 ./etf; data+ready+ETA)
  do / act        执行 pulse.next_action
  progress        可判性轨迹 Lrets→READY / ETA (--json)
  digest          人读一页 (ready+yield+decision)
  refresh         一键刷过旧信号 (= daily --dry-run)
  status          研究/生产状态面板
  doctor          一键体检 (环境/配置/产物)
  check           研究健康检查 (默认 --quick; 含 data_asof)
  daily           日更流水线 (默认生产 c01 + 研究影子)
  signal          仅跑信号
  monitor         主线影子只读监控 (暴露/live/xs/告警)
  warmup          影子 port_rets 暖机 (写 live 锚点, 不碰生产)
  pages           构建 GitHub Pages 面板
  preview         刷新监控/状态+面板并打印本地路径
  compare         主线影子对照 (sh/dd/live%/xs%)
  live            主线 live 段收益 + 相对基准超额
  summary         影子仓位摘要 (live/xs/持仓)
  email-preview   邮件 HTML dry-print

示例:
  ./etf today
  ./etf asof
  ./etf yield
  ./etf open
  ./etf open --launch site
  ./etf brief
  ./etf data
  ./etf pull
  ./etf wait-asof
  ./etf next
  ./etf go
  ./etf ready
  ./etf                 # 默认 = pulse
  ./etf pulse
  ./etf pulse --quiet
  ./etf do --dry-run
  ./etf do
  ./etf wait --timeout 600
  ./etf progress
  ./etf progress --json
  ./etf pull --bench-only   # asof 推进则 follow+progress
  ./etf digest
  ./etf refresh
  ./etf doctor
  ./etf check
  ./etf check --checks live
  ./etf check --checks today
  ./etf check --checks data_asof
  ./etf status
  ./etf monitor
  ./etf live
  ./etf summary
  ./etf warmup --tail 120
  ./etf compare
  ./etf daily --dry-run
  ./etf preview

约定:
  · 生产策略 c01 冻结, 不在本 CLI 切换
  · 研究主线影子默认: {_DEFAULT_SHADOW}
  · dry-run 不改生产仓, 默认仍更新研究影子
  · 有效收益看 live% + xs% (非暖机全样本)
  · THIN=live 样本偏薄, 等下一交易日再判 xs
  · DATA_LAG=wall 日 > 行情截至, nav/live 以 asof 为准
  · 真实交易/晋级需 dual-gate, 不在此自动完成
""".strip()
    )
    return 0


def main() -> None:
    # 易用别名: ./etf wait → wait-asof
    if len(sys.argv) >= 2 and sys.argv[1] == "wait":
        sys.argv[1] = "wait-asof"
    ap = argparse.ArgumentParser(description="ETF 轮动统一 CLI")
    sub = ap.add_subparsers(dest="cmd", required=False)

    p_today = sub.add_parser("today", help="今日一页速览")
    p_today.add_argument("--no-write", action="store_true", help="不写 today.txt/json")
    p_today.set_defaults(func=cmd_today)

    p_asof = sub.add_parser("asof", help="行情截至/DATA_LAG/live 取证")
    p_asof.set_defaults(func=cmd_asof)

    p_yield = sub.add_parser("yield", help="有效收益一页 live%/xs%/THIN")
    p_yield.set_defaults(func=cmd_yield)

    p_open = sub.add_parser("open", help="关键产物路径 (可 --launch)")
    p_open.add_argument(
        "target",
        nargs="?",
        default="all",
        help="all|today|brief|yield|asof|status|latest|live|ready|digest|progress|eod|go|data|next|site|*-html",
    )
    p_open.add_argument(
        "--launch",
        action="store_true",
        help="尝试用系统打开器打开 (html/文本)",
    )
    p_open.set_defaults(func=cmd_open)

    p_brief = sub.add_parser("brief", help="三合一速览 yield+asof+today")
    p_brief.add_argument(
        "--refresh-local",
        action="store_true",
        help="强制重算 today/asof/yield 产物",
    )
    p_brief.set_defaults(func=cmd_brief)

    p_data = sub.add_parser("data", help="行情状态 asof/DATA_LAG/决策")
    p_data.add_argument(
        "--fail-on-lag",
        action="store_true",
        help="DATA_LAG 时退出码 3 (脚本门控)",
    )
    p_data.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="latest 过旧时退出码 4",
    )
    p_data.set_defaults(func=cmd_data)

    p_pull = sub.add_parser("pull", help="强刷行情缓存并复检 asof")
    p_pull.add_argument("--pool", default="pool", help="ETF 池配置名")
    p_pull.add_argument("--bars", type=int, default=120)
    p_pull.add_argument("--workers", type=int, default=4)
    p_pull.add_argument("--bench-only", action="store_true", help="只刷基准")
    p_pull.add_argument("--no-extra", action="store_true", help="不刷 extra_universe")
    p_pull.add_argument(
        "--follow",
        action="store_true",
        help="兼容开关 (默认已 follow; 用 --no-follow 关闭)",
    )
    p_pull.add_argument(
        "--fail-if-lag",
        action="store_true",
        help="刷后仍 DATA_LAG 则 exit 3",
    )
    p_pull.add_argument(
        "--no-follow",
        action="store_true",
        help="asof 推进后不跑 signal/live/ready",
    )
    p_pull.add_argument(
        "--fail-if-not-advanced",
        action="store_true",
        help="asof 未推进则 exit 5",
    )
    p_pull.set_defaults(func=cmd_pull)

    p_wait = sub.add_parser("wait-asof", help="轮询 pull 直到 asof 推进")
    p_wait.add_argument("--timeout", type=int, default=600, help="最长等待秒数")
    p_wait.add_argument("--interval", type=int, default=60, help="轮询间隔秒")
    p_wait.add_argument(
        "--full",
        action="store_true",
        help="每次 pull 刷全池 (默认 bench-only)",
    )
    p_wait.add_argument(
        "--no-follow",
        action="store_true",
        help="成功后不自动 live/next/brief",
    )
    p_wait.add_argument(
        "--follow",
        action="store_true",
        default=True,
        help="成功后自动 live+next+brief (默认开)",
    )
    p_wait.set_defaults(func=cmd_wait_asof)

    p_next = sub.add_parser("next", help="一键下一步 (唯一推荐动作)")
    p_next.add_argument(
        "--no-refresh",
        action="store_true",
        help="不重跑 data, 只用已有 data_status",
    )
    p_next.add_argument(
        "--pull",
        action="store_true",
        help="决策前先 pull --bench-only 强刷行情",
    )
    p_next.add_argument(
        "--wait",
        action="store_true",
        help="若 decision=wait_data 则自动 wait-asof",
    )
    p_next.add_argument(
        "--wait-timeout",
        type=int,
        default=600,
        help="配合 --wait 的 wait-asof 超时秒",
    )
    p_next.add_argument(
        "--wait-interval",
        type=int,
        default=60,
        help="配合 --wait 的轮询间隔秒",
    )
    p_next.add_argument(
        "--exit-code",
        action="store_true",
        help="按决策返回退出码: ok=0 wait_data=3 refresh=4 unknown=1",
    )
    p_next.set_defaults(func=cmd_next)

    p_go = sub.add_parser("go", help="一键闭环 pull→next→wait-asof")
    p_go.add_argument("--timeout", type=int, default=600, help="wait-asof 超时秒")
    p_go.add_argument("--interval", type=int, default=60, help="wait-asof 间隔秒")
    p_go.add_argument("--no-wait", action="store_true", help="wait_data 时不自动 wait-asof")
    p_go.add_argument("--no-pull", action="store_true", help="跳过开头 pull")
    p_go.set_defaults(func=cmd_go)

    p_eod = sub.add_parser("eod", help="收盘后一键 wait-asof→daily→digest")
    p_eod.add_argument("--timeout", type=int, default=1800, help="wait-asof 超时秒")
    p_eod.add_argument("--interval", type=int, default=90, help="wait-asof 间隔秒")
    p_eod.add_argument(
        "--no-wait",
        action="store_true",
        help="跳过 wait-asof, 仅 daily 快照",
    )
    p_eod.set_defaults(func=cmd_eod)

    p_ready = sub.add_parser("ready", help="有效收益可判性 READY/THIN/WAIT_DATA")
    p_ready.set_defaults(func=cmd_ready)

    p_do = sub.add_parser("do", help="执行 pulse.next_action (wait/yield/...)")
    p_do.add_argument("action", nargs="?", default="", help="覆盖 next_action 可选")
    p_do.add_argument("--timeout", type=int, default=600, help="wait/eod 超时秒")
    p_do.add_argument("--no-refresh", action="store_true", help="不重跑 pulse")
    p_do.add_argument("--dry-run", action="store_true", help="只打印将执行的动作")
    p_do.add_argument("--no-wait", action="store_true", help="accumulate 时 eod --no-wait")
    p_do.set_defaults(func=cmd_do)
    p_act = sub.add_parser("act", help="do 别名")
    p_act.add_argument("action", nargs="?", default="", help="覆盖 next_action 可选")
    p_act.add_argument("--timeout", type=int, default=600)
    p_act.add_argument("--no-refresh", action="store_true")
    p_act.add_argument("--dry-run", action="store_true")
    p_act.add_argument("--no-wait", action="store_true")
    p_act.set_defaults(func=cmd_do)

    p_pulse = sub.add_parser("pulse", help="一键脉搏 data+ready+ETA (默认入口)")
    p_pulse.add_argument("--no-refresh", action="store_true", help="不重跑 data/ready")
    p_pulse.add_argument("--json", action="store_true", help="只/附加输出 JSON")
    p_pulse.add_argument(
        "--quiet",
        action="store_true",
        help="安静模式: 不展开 progress 轨迹, 适合脚本",
    )
    p_pulse.set_defaults(func=cmd_pulse)

    p_prog = sub.add_parser("progress", help="可判性轨迹 Lrets→READY")
    p_prog.add_argument("--tail", type=int, default=12, help="显示最近 N 条")
    p_prog.add_argument(
        "--no-refresh",
        action="store_true",
        help="不重跑 ready, 只读轨迹",
    )
    p_prog.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON (脚本/取证)",
    )
    p_prog.set_defaults(func=cmd_progress)

    p_digest = sub.add_parser("digest", help="人读一页 ready+yield+decision")
    p_digest.add_argument(
        "--no-refresh",
        action="store_true",
        help="不重跑 ready, 只用已有产物",
    )
    p_digest.set_defaults(func=cmd_digest)

    p_ref = sub.add_parser("refresh", help="一键刷过旧信号 (daily --dry-run)")
    p_ref.add_argument(
        "--force",
        action="store_true",
        help="DATA_LAG 且信号未过旧时仍强制 refresh",
    )
    p_ref.add_argument("--warmup", action="store_true")
    p_ref.add_argument("--strategy", default="c01")
    p_ref.add_argument("--shadow", default=_DEFAULT_SHADOW)
    p_ref.add_argument("--steps", default="")
    p_ref.add_argument("--no-monitor-fail-on-alert", action="store_false", dest="monitor_fail_on_alert")
    p_ref.add_argument("--no-append-shadow-email", action="store_false", dest="append_shadow_email")
    p_ref.set_defaults(func=cmd_refresh, monitor_fail_on_alert=True, append_shadow_email=True)

    p_status = sub.add_parser("status", help="状态面板")
    p_status.set_defaults(func=cmd_status)
    p_doc = sub.add_parser("doctor", help="一键体检")
    p_doc.add_argument("--strict", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_chk = sub.add_parser("check", help="研究健康检查")
    p_chk.add_argument("--full", action="store_true", help="跑完整检查 (含 etf_soft/long_anchor)")
    p_chk.add_argument("--checks", default="", help="逗号分隔检查名; 默认用 healthcheck 默认集")
    p_chk.add_argument("--skip-warmup", action="store_true")
    p_chk.set_defaults(func=cmd_check, quick=True)

    p_daily = sub.add_parser("daily", help="日更 pipeline")
    p_daily.add_argument("--dry-run", action="store_true")
    p_daily.add_argument("--warmup", action="store_true")
    p_daily.add_argument("--strategy", default="c01")
    p_daily.add_argument("--shadow", default=_DEFAULT_SHADOW)
    p_daily.add_argument("--steps", default="")
    p_daily.add_argument("--require-trading-day", action="store_true", default=True)
    p_daily.add_argument("--no-require-trading-day", action="store_false", dest="require_trading_day")
    p_daily.add_argument("--monitor-fail-on-alert", action="store_true", default=True)
    p_daily.add_argument("--no-monitor-fail-on-alert", action="store_false", dest="monitor_fail_on_alert")
    p_daily.add_argument("--append-shadow-email", action="store_true", default=True)
    p_daily.add_argument("--no-append-shadow-email", action="store_false", dest="append_shadow_email")
    p_daily.set_defaults(func=cmd_daily)

    p_sig = sub.add_parser("signal", help="仅信号")
    p_sig.add_argument("--dry-run", action="store_true")
    p_sig.add_argument("--strategy", default="")
    p_sig.set_defaults(func=cmd_signal)

    p_mon = sub.add_parser("monitor", help="主线影子只读监控")
    p_mon.add_argument("--shadows", default="", help="逗号分隔; 默认 MONITOR_SHADOWS")
    p_mon.add_argument("--bars", type=int, default=120)
    p_mon.add_argument("--fail-on-alert", action="store_true")
    p_mon.add_argument("--fail-on-warn", action="store_true")
    p_mon.set_defaults(func=cmd_monitor)

    p_warm = sub.add_parser("warmup", help="影子暖机 (port_rets/live 锚点)")
    p_warm.add_argument("--shadows", default="", help="逗号分隔; 默认 MONITOR_SHADOWS")
    p_warm.add_argument("--pool", default="pool_long_proxy")
    p_warm.add_argument("--tail", type=int, default=120)
    p_warm.add_argument("--reset", action="store_true", help="忽略已有 state 重建")
    p_warm.set_defaults(func=cmd_warmup)

    p_pages = sub.add_parser("pages", help="构建面板")
    p_pages.set_defaults(func=cmd_pages)

    p_prev = sub.add_parser("preview", help="路径预览 + 刷新面板")
    p_prev.set_defaults(func=cmd_preview)

    p_cmp = sub.add_parser("compare", help="主线影子对照")
    p_cmp.set_defaults(func=cmd_compare)

    p_live = sub.add_parser("live", help="主线 live 段收益")
    p_live.set_defaults(func=cmd_live)

    p_sum = sub.add_parser("summary", help="影子仓位摘要")
    p_sum.add_argument("--names", default="", help="逗号分隔; 默认扫 shadow_states")
    p_sum.set_defaults(func=cmd_summary)

    p_mail = sub.add_parser("email-preview", help="邮件预览")
    p_mail.add_argument("--append-all", action="store_true", default=True)
    p_mail.set_defaults(func=cmd_email_preview)

    p_help = sub.add_parser("help", help="帮助")
    p_help.set_defaults(func=cmd_help)

    args = ap.parse_args()
    if getattr(args, "func", None) is None:
        # 裸 ./etf → 最短可读: 可判/ETA/是否能读真实有效收益
        ns = ap.parse_args(["pulse"])
        raise SystemExit(ns.func(ns))
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
