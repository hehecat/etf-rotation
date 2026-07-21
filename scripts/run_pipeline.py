#!/usr/bin/env python3
"""日更编排: (可选 warmup) → signal → monitor → (可选 email).

默认:
  - 生产策略 c01 (冻结)
  - 研究影子 c01_q10_vt08_soft_oh38
  - 不默认 warmup (长代理重; 用 --warmup 显式开)
  - 邮件需 SMTP 环境变量; 缺配置则跳过

用法:
  python3 scripts/run_pipeline.py --dry-run
  python3 scripts/run_pipeline.py --warmup --append-shadow-email
  python3 scripts/run_pipeline.py --steps signal,monitor
  python3 scripts/run_pipeline.py --shadows c01_q10_vt08_soft_oh38,c01_q10_vt11
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import (  # noqa: E402
    LATEST_JSON,
    LATEST_TXT,
    LOG_DIR,
    OUTPUT_DIR,
    ensure_dirs,
)

PY = sys.executable

# 延迟导入避免循环; 失败时回退字面量
try:
    from etf_rotation.research_mainline import MONITOR_SHADOWS, SIGNAL_SHADOW

    DEFAULT_SHADOWS = ",".join(MONITOR_SHADOWS)
    DEFAULT_SIGNAL_SHADOW = SIGNAL_SHADOW
except Exception:
    DEFAULT_SHADOWS = (
        "c01_q10_vt08_soft_oh38,c01_q10_vt08_soft_oh38_xgn,"
        "c01_q10_vt09_oh35,c01_q10_vt11"
    )
    DEFAULT_SIGNAL_SHADOW = "c01_q10_vt08_soft_oh38_xgn"


def run_step(name: str, cmd: list[str], *, cwd: Path = ROOT) -> dict:
    t0 = time.time()
    print(f"\n>>> [{name}] {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=str(cwd))
    dt = time.time() - t0
    ok = r.returncode == 0
    print(f"<<< [{name}] exit={r.returncode} {dt:.1f}s", flush=True)
    return {"step": name, "cmd": cmd, "exit": r.returncode, "ok": ok, "seconds": round(dt, 2)}


def main() -> None:
    ap = argparse.ArgumentParser(description="ETF 日更编排 pipeline")
    ap.add_argument(
        "--steps",
        default="signal,monitor,email",
        help="逗号分隔: warmup,pull,signal,monitor,summary,email,compare,live,status,today,asof,yield,brief,data,next,go,ready,digest,progress,pulse,pages",
    )
    ap.add_argument("--strategy", default="c01", help="生产策略 (默认 c01)")
    ap.add_argument("--shadow", default=DEFAULT_SIGNAL_SHADOW, help="信号侧研究影子")
    ap.add_argument("--shadows", default=DEFAULT_SHADOWS, help="monitor/warmup 多影子列表")
    ap.add_argument("--pool", default="pool")
    ap.add_argument("--bars", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true", help="不改生产仓; 默认仍可写影子")
    ap.add_argument("--no-shadow-exec", action="store_true", help="禁止影子写仓")
    ap.add_argument("--shadow-exec", action="store_true", help="dry-run 时也写影子")
    ap.add_argument("--warmup", action="store_true", help="先跑 shadow_warmup")
    ap.add_argument("--warmup-tail", type=int, default=120)
    ap.add_argument("--warmup-reset", action="store_true")
    ap.add_argument("--warmup-pool", default="pool_long_proxy")
    ap.add_argument("--append-shadow-email", action="store_true", help="邮件附加影子摘要")
    ap.add_argument("--skip-email-if-no-smtp", action="store_true", default=True)
    ap.add_argument("--force-email", action="store_true", help="无 SMTP 也尝试发信(会失败)")
    ap.add_argument(
        "--monitor-fail-on-alert",
        action="store_true",
        help="monitor 步骤启用 --fail-on-alert (error 级告警使 pipeline 失败)",
    )
    ap.add_argument(
        "--monitor-fail-on-warn",
        action="store_true",
        help="monitor 步骤 warn 也失败",
    )
    ap.add_argument(
        "--require-trading-day",
        action="store_true",
        help="非交易日跳过 signal/email (monitor 仍可跑)",
    )
    ap.add_argument(
        "--force-run",
        action="store_true",
        help="忽略交易日门控, 强制跑全部 steps",
    )
    ap.add_argument(
        "--pages-out",
        default="",
        help="若指定目录, 生成静态 Pages 站点 (信号+monitor)",
    )
    ap.add_argument(
        "--pull-full",
        action="store_true",
        help="pull 步骤刷全池 (默认只刷基准)",
    )
    ap.add_argument("--out", default="output/risk_audit/pipeline_last.json")
    args = ap.parse_args()

    ensure_dirs()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"pipeline_{stamp}.log"

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    if args.warmup and "warmup" not in steps:
        steps = ["warmup"] + steps
    if args.pages_out and "pages" not in steps:
        steps = steps + ["pages"]

    # shadow exec 语义对齐 run_signal CLI
    if args.no_shadow_exec:
        sh_exec_flags = ["--no-shadow-exec"]
    elif args.shadow_exec:
        sh_exec_flags = ["--shadow-exec"]
    else:
        # dry-run 只禁生产仓; 研究影子默认仍写 shadow_states (与帮助文案一致)
        sh_exec_flags = ["--shadow-exec"] if args.dry_run else []

    results: list[dict] = []
    print("=" * 72)
    print(f"PIPELINE {stamp} steps={steps}")
    print(f"strategy={args.strategy} shadow={args.shadow} dry_run={args.dry_run}")
    print(f"log={log_path}")
    print("=" * 72)

    # 交易日门控
    trading_day = True
    cal_note = "gate_off"
    data_asof = None
    data_lag = False
    if args.require_trading_day and not args.force_run:
        from etf_rotation.calendar_util import resolve_trading_day

        info = resolve_trading_day(bars=max(args.bars, 60))
        trading_day = bool(info.get("is_trading_day"))
        data_asof = info.get("data_asof")
        data_lag = bool(info.get("data_lag"))
        cal_note = (
            f"source={info.get('source')} date={info.get('date')} "
            f"bench_n={info.get('bench_n')} closed={info.get('in_closed_table')} "
            f"makeup={info.get('in_makeup_table')} "
            f"asof={data_asof} lag={data_lag}"
        )
        print(f"trading_day_gate: is_td={trading_day} ({cal_note})")
        if data_lag:
            print(
                f"DATA_LAG: wall/交易日 {info.get('date')} > 行情截至 {data_asof} "
                f"(signal nav/live 以 asof 为准)"
            )
        if not trading_day:
            # 非交易日: 跳过 signal/email/warmup; monitor/summary/pages 仍可
            skip_set = {"signal", "email", "warmup"}
            kept = []
            for s in steps:
                if s in skip_set:
                    results.append(
                        {
                            "step": s,
                            "ok": True,
                            "skipped": True,
                            "reason": "non_trading_day",
                            "exit": 0,
                            "seconds": 0,
                        }
                    )
                    print(f"skip {s}: non_trading_day")
                else:
                    kept.append(s)
            steps = kept

    # 简单 tee: 同时写 log
    class Tee:
        def __init__(self, *files):
            self.files = files

        def write(self, data):
            for f in self.files:
                f.write(data)
                f.flush()

        def flush(self):
            for f in self.files:
                f.flush()

    log_f = open(log_path, "w", encoding="utf-8")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = Tee(old_out, log_f)  # type: ignore
    sys.stderr = Tee(old_err, log_f)  # type: ignore
    try:
        if "warmup" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "shadow_warmup.py"),
                "--shadows",
                args.shadows,
                "--pool",
                args.warmup_pool,
                "--tail",
                str(args.warmup_tail),
            ]
            if args.warmup_reset:
                cmd.append("--reset")
            results.append(run_step("warmup", cmd))

        # 强刷行情 (默认 bench-only; 在 signal 前尽量推进 asof)
        if "pull" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "pull", "--no-follow"]
            if not args.pull_full:
                cmd.append("--bench-only")
            results.append(run_step("pull", cmd))

        if "signal" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "run_signal.py"),
                "--strategy",
                args.strategy,
                "--shadow",
                args.shadow,
                "--pool",
                args.pool,
                "--bars",
                str(args.bars),
                "--extra-shadows",
                args.shadows,
            ]
            if args.dry_run:
                cmd.append("--dry-run")
            cmd.extend(sh_exec_flags)
            results.append(run_step("signal", cmd))

        if "monitor" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "shadow_monitor.py"),
                "--shadows",
                args.shadows,
                "--bars",
                str(args.bars),
                "--text-out",
                str(OUTPUT_DIR / "shadow_monitor.txt"),
                "--json-out",
                str(OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"),
            ]
            if args.monitor_fail_on_alert:
                cmd.append("--fail-on-alert")
            if args.monitor_fail_on_warn:
                cmd.append("--fail-on-warn")
            results.append(run_step("monitor", cmd))

        if "summary" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "shadow_summary.py"),
                "--names",
                args.shadows,
                "--text-out",
                str(OUTPUT_DIR / "shadow_summary.txt"),
                "--json-out",
                str(OUTPUT_DIR / "risk_audit" / "shadow_summary.json"),
            ]
            results.append(run_step("summary", cmd))

        # compare 在 status/pages 前, 供面板与状态嵌入
        if "compare" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "shadow_compare.py"),
                "--json-out",
                str(OUTPUT_DIR / "risk_audit" / "shadow_compare.json"),
                "--text-out",
                str(OUTPUT_DIR / "shadow_compare.txt"),
            ]
            results.append(run_step("compare", cmd))

        # live 段真实收益 (暖机末→今); 写回 state 锚点
        if "live" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "shadow_live.py"),
                "--json-out",
                str(OUTPUT_DIR / "risk_audit" / "shadow_live.json"),
                "--text-out",
                str(OUTPUT_DIR / "shadow_live.txt"),
                "--write-states",
            ]
            if args.shadows:
                cmd += ["--names", args.shadows]
            results.append(run_step("live", cmd))

        # status 先于 today/email/pages
        if "status" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "research_status.py"),
                "--json-out",
                str(OUTPUT_DIR / "risk_audit" / "research_status.json"),
                "--text-out",
                str(OUTPUT_DIR / "research_status.txt"),
            ]
            results.append(run_step("status", cmd))

        # 今日一页速览 (只读汇总; 依赖 live/monitor)
        if "today" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "today"]
            results.append(run_step("today", cmd))

        # 行情/收益取证 (DATA_LAG vs 过旧; 落盘 asof.txt/json)
        if "asof" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "asof"]
            results.append(run_step("asof", cmd))

        # 有效收益一页 (live%/xs%/THIN; 落盘 yield.txt/json)
        if "yield" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "yield"]
            results.append(run_step("yield", cmd))

        # 三合一速览 (yield+asof+today; 落盘 brief.txt/json)
        if "brief" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "brief"]
            results.append(run_step("brief", cmd))

        # 行情状态决策 (asof/DATA_LAG/STALE; 落盘 data_status)
        if "data" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "data"]
            results.append(run_step("data", cmd))

        # 一键下一步 (依赖 data_status; 落盘 next.txt/json)
        if "next" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "next", "--no-refresh"]
            results.append(run_step("next", cmd))

        # 一键闭环快照 (默认 --no-wait, 不阻塞长轮询)
        if "go" in steps:
            cmd = [
                PY,
                str(ROOT / "scripts" / "etf.py"),
                "go",
                "--no-wait",
                "--no-pull",
            ]
            r = run_step("go", cmd)
            # go 退出码 3/4 是决策码 (wait_data/refresh), 产物已写, 不视作 pipeline 失败
            if r.get("exit") in (3, 4) and (OUTPUT_DIR / "risk_audit" / "go.json").exists():
                r = {
                    **r,
                    "ok": True,
                    "decision_exit": r.get("exit"),
                    "note": "go decision code ignored for pipeline ok",
                }
                print(
                    f"<<< [go] decision_exit={r.get('decision_exit')} "
                    f"treated as OK for pipeline"
                )
            results.append(r)

        # 有效收益可判性 (READY/THIN/WAIT_DATA); 退出码非0不失败
        if "ready" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "ready"]
            r = run_step("ready", cmd)
            if r.get("exit") in (1, 3, 4) and (
                (OUTPUT_DIR / "risk_audit" / "ready.json").exists()
                or (OUTPUT_DIR / "ready.txt").exists()
            ):
                r = {
                    **r,
                    "ok": True,
                    "decision_exit": r.get("exit"),
                    "note": "ready level code ignored for pipeline ok",
                }
                print(
                    f"<<< [ready] decision_exit={r.get('decision_exit')} "
                    f"treated as OK for pipeline"
                )
            results.append(r)

        # 人读摘要 digest (默认 --no-refresh, 复用刚写的 ready)
        if "digest" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "digest", "--no-refresh"]
            r = run_step("digest", cmd)
            if r.get("exit") in (1, 3, 4) and (
                (OUTPUT_DIR / "risk_audit" / "digest.json").exists()
                or (OUTPUT_DIR / "digest.txt").exists()
            ):
                r = {
                    **r,
                    "ok": True,
                    "decision_exit": r.get("exit"),
                    "note": "digest level code ignored for pipeline ok",
                }
                print(
                    f"<<< [digest] decision_exit={r.get('decision_exit')} "
                    f"treated as OK for pipeline"
                )
            results.append(r)

        # 可判性轨迹 progress (只读/轻量, --no-refresh)
        if "progress" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "progress", "--no-refresh"]
            results.append(run_step("progress", cmd))

        # 一键脉搏 pulse (复用 ready; --no-refresh)
        if "pulse" in steps:
            cmd = [PY, str(ROOT / "scripts" / "etf.py"), "pulse", "--no-refresh", "--quiet"]
            r = run_step("pulse", cmd)
            if r.get("exit") in (1, 3, 4) and (
                (OUTPUT_DIR / "risk_audit" / "pulse.json").exists()
                or (OUTPUT_DIR / "pulse.txt").exists()
            ):
                r = {
                    **r,
                    "ok": True,
                    "decision_exit": r.get("exit"),
                    "note": "pulse level code ignored for pipeline ok",
                }
                print(
                    f"<<< [pulse] decision_exit={r.get('decision_exit')} "
                    f"treated as OK for pipeline"
                )
            results.append(r)


        # email 放在 .../ready/digest 后
        if "email" in steps:
            smtp_ok = all(
                os.environ.get(k, "").strip()
                for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO")
            )
            # dry-run: 无 SMTP 也预览邮件正文 (含告警)
            if args.dry_run and not args.force_email:
                cmd = [PY, str(ROOT / "scripts" / "send_email.py"), "--dry-print"]
                if args.append_shadow_email:
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
                        "--append-progress",
                        "--append-pulse",
                        "--shadow-names",
                        args.shadows,
                    ]
                results.append(run_step("email_preview", cmd))
            elif not smtp_ok and not args.force_email:
                print("email: skip (SMTP env incomplete)")
                results.append(
                    {
                        "step": "email",
                        "ok": True,
                        "skipped": True,
                        "reason": "no_smtp",
                        "exit": 0,
                        "seconds": 0,
                    }
                )
            else:
                cmd = [PY, str(ROOT / "scripts" / "send_email.py")]
                if args.append_shadow_email:
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
                        "--append-progress",
                        "--append-pulse",
                        "--shadow-names",
                        args.shadows,
                    ]
                results.append(run_step("email", cmd))

        if "pages" in steps:
            out_dir = args.pages_out or str(OUTPUT_DIR / "site")
            cmd = [
                PY,
                str(ROOT / "scripts" / "build_pages.py"),
                "--out",
                out_dir,
            ]
            results.append(run_step("pages", cmd))
    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        log_f.close()

    ok_all = all(x.get("ok", False) or x.get("skipped") for x in results)

    # 汇总 monitor 告警 (若有产物)
    mon_path = OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"
    alert_error_n = 0
    alert_warn_n = 0
    alert_items: list[dict] = []
    if mon_path.exists():
        try:
            mon = json.loads(mon_path.read_text(encoding="utf-8"))
            alert_error_n = int(mon.get("alert_error_n") or 0)
            alert_warn_n = int(mon.get("alert_warn_n") or 0)
            for row in mon.get("rows") or []:
                for a in row.get("alerts") or []:
                    alert_items.append(
                        {
                            "shadow": row.get("name"),
                            "level": a.get("level"),
                            "code": a.get("code"),
                            "msg": a.get("msg"),
                        }
                    )
        except Exception:
            pass

    # 门控关闭时也尽量记录 asof
    if data_asof is None:
        try:
            from etf_rotation.calendar_util import resolve_trading_day as _rtd
            _info = _rtd(bars=max(args.bars, 60))
            data_asof = _info.get("data_asof")
            data_lag = bool(_info.get("data_lag"))
        except Exception:
            pass
    # latest 补 market_asof
    try:
        if LATEST_JSON.exists():
            import json as _json
            _lj = _json.loads(LATEST_JSON.read_text(encoding="utf-8"))
            if isinstance(_lj, dict):
                data_asof = data_asof or _lj.get("market_asof")
                sl = _lj.get("signal_live") if isinstance(_lj.get("signal_live"), dict) else {}
                if sl.get("data_lag") is not None:
                    data_lag = bool(sl.get("data_lag"))
                if sl.get("market_asof"):
                    data_asof = sl.get("market_asof")
    except Exception:
        pass

    summary = {
        "stamp": stamp,
        "steps_requested": [s.strip() for s in args.steps.split(",") if s.strip()],
        "steps_run": steps,
        "strategy": args.strategy,
        "shadow": args.shadow,
        "dry_run": args.dry_run,
        "trading_day": trading_day,
        "trading_day_note": cal_note if args.require_trading_day else "gate_off",
        "data_asof": data_asof,
        "data_lag": bool(data_lag),
        "ok": ok_all,
        "alert_error_n": alert_error_n,
        "alert_warn_n": alert_warn_n,
        "alerts": alert_items[:50],
        "monitor_fail_on_alert": bool(args.monitor_fail_on_alert),
        "results": results,
        "latest_txt": str(LATEST_TXT) if LATEST_TXT.exists() else None,
        "latest_json": str(LATEST_JSON) if LATEST_JSON.exists() else None,
        "log": str(log_path),
        "pages_out": args.pages_out or None,
    }
    # 有效收益取证字段 (forensics; 不改变 ok 判定)
    try:
        _yj, _lj, _sl = {}, {}, {}
        _yp = OUTPUT_DIR / "risk_audit" / "yield.json"
        if _yp.exists():
            _yj = json.loads(_yp.read_text(encoding="utf-8"))
        if LATEST_JSON.exists():
            _lj = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        if isinstance(_lj, dict) and isinstance(_lj.get("signal_live"), dict):
            _sl = _lj.get("signal_live") or {}
        if not _sl and isinstance(_yj, dict):
            if isinstance(_yj.get("signal_live"), dict):
                _sl = _yj.get("signal_live") or {}
            elif _yj.get("live_return_pct") is not None or _yj.get("live_excess_pct") is not None:
                _sl = _yj
        if _sl:
            summary["yield"] = {
                "name": _sl.get("name"),
                "live_return_pct": _sl.get("live_return_pct"),
                "live_excess_pct": _sl.get("live_excess_pct"),
                "bench_return_pct": _sl.get("bench_return_pct"),
                "days_live": _sl.get("days_live"),
                "thin_live": _sl.get("thin_live"),
                "live_start": _sl.get("live_start"),
                "holdings": _sl.get("holdings"),
                "market_asof": _sl.get("market_asof") or summary.get("data_asof"),
                "data_lag": bool(summary.get("data_lag")),
            }
        else:
            summary["yield"] = None
        # brief 三合一取证
        _bp = OUTPUT_DIR / "risk_audit" / "brief.json"
        if _bp.exists():
            _bj = json.loads(_bp.read_text(encoding="utf-8"))
            if isinstance(_bj, dict):
                _bsl = _bj.get("signal_live") if isinstance(_bj.get("signal_live"), dict) else {}
                summary["brief"] = {
                    "stamp": _bj.get("stamp"),
                    "market_asof": _bj.get("market_asof") or summary.get("data_asof"),
                    "data_lag": _bj.get("data_lag") if _bj.get("data_lag") is not None else summary.get("data_lag"),
                    "action": _bj.get("action"),
                    "market_ok": _bj.get("market_ok"),
                    "live_return_pct": (_bsl or _bj).get("live_return_pct"),
                    "live_excess_pct": (_bsl or _bj).get("live_excess_pct"),
                    "days_live": (_bsl or _bj).get("days_live"),
                    "thin_live": (_bsl or _bj).get("thin_live"),
                    "path_txt": str(OUTPUT_DIR / "brief.txt"),
                    "path_json": str(_bp),
                }
            else:
                summary["brief"] = None
        else:
            summary["brief"] = None
        # data_status 取证
        _dp = OUTPUT_DIR / "risk_audit" / "data_status.json"
        if _dp.exists():
            _dj = json.loads(_dp.read_text(encoding="utf-8"))
            if isinstance(_dj, dict):
                summary["data_status"] = {
                    "market_asof": _dj.get("market_asof") or summary.get("data_asof"),
                    "data_lag": _dj.get("data_lag") if _dj.get("data_lag") is not None else summary.get("data_lag"),
                    "latest_stale": _dj.get("latest_stale"),
                    "decision": _dj.get("decision"),
                    "action": _dj.get("action"),
                    "path_txt": str(OUTPUT_DIR / "data_status.txt"),
                    "path_json": str(_dp),
                }
            else:
                summary["data_status"] = None
        else:
            summary["data_status"] = None
        # next 决策取证
        _np = OUTPUT_DIR / "risk_audit" / "next.json"
        if _np.exists():
            _nj = json.loads(_np.read_text(encoding="utf-8"))
            if isinstance(_nj, dict):
                summary["next"] = {
                    "decision": _nj.get("decision"),
                    "market_asof": _nj.get("market_asof") or summary.get("data_asof"),
                    "data_lag": _nj.get("data_lag"),
                    "latest_stale": _nj.get("latest_stale"),
                    "recommend": _nj.get("recommend"),
                    "why": _nj.get("why"),
                    "path_txt": str(OUTPUT_DIR / "next.txt"),
                    "path_json": str(_np),
                }
            else:
                summary["next"] = None
        else:
            summary["next"] = None
        # pull 取证
        _pp = OUTPUT_DIR / "risk_audit" / "pull.json"
        if _pp.exists():
            _pj = json.loads(_pp.read_text(encoding="utf-8"))
            if isinstance(_pj, dict):
                summary["pull"] = {
                    "stamp": _pj.get("stamp"),
                    "before": _pj.get("before"),
                    "after": _pj.get("after"),
                    "advanced": _pj.get("advanced"),
                    "cleared_lag": _pj.get("cleared_lag"),
                    "ok_n": _pj.get("ok_n"),
                    "fail_n": _pj.get("fail_n"),
                    "bench_last": _pj.get("bench_last"),
                    "path_txt": str(OUTPUT_DIR / "pull.txt"),
                    "path_json": str(_pp),
                }
            else:
                summary["pull"] = None
        else:
            summary["pull"] = None
        # go 闭环快照
        _gp = OUTPUT_DIR / "risk_audit" / "go.json"
        if _gp.exists():
            _gj = json.loads(_gp.read_text(encoding="utf-8"))
            if isinstance(_gj, dict):
                summary["go"] = {
                    "stamp": _gj.get("stamp"),
                    "decision": _gj.get("decision"),
                    "market_asof": _gj.get("market_asof") or summary.get("data_asof"),
                    "data_lag": _gj.get("data_lag"),
                    "did_wait": _gj.get("did_wait"),
                    "wait_code": _gj.get("wait_code"),
                    "recommend": _gj.get("recommend"),
                    "path_txt": str(OUTPUT_DIR / "go.txt"),
                    "path_json": str(_gp),
                }
            else:
                summary["go"] = None
        else:
            summary["go"] = None
        # ready 可判性
        _rp = OUTPUT_DIR / "risk_audit" / "ready.json"
        if _rp.exists():
            _rj = json.loads(_rp.read_text(encoding="utf-8"))
            if isinstance(_rj, dict):
                summary["ready"] = {
                    "level": _rj.get("level"),
                    "ready": _rj.get("ready"),
                    "market_asof": _rj.get("market_asof") or summary.get("data_asof"),
                    "data_lag": _rj.get("data_lag"),
                    "days_live": _rj.get("days_live"),
                    "thin_live": _rj.get("thin_live"),
                    "live_return_pct": _rj.get("live_return_pct"),
                    "live_excess_pct": _rj.get("live_excess_pct"),
                    "note": _rj.get("note"),
                    "path_txt": str(OUTPUT_DIR / "ready.txt"),
                    "path_json": str(_rp),
                }
            else:
                summary["ready"] = None
        else:
            summary["ready"] = None
        # digest 人读摘要
        _dp = OUTPUT_DIR / "risk_audit" / "digest.json"
        if _dp.exists():
            _dj = json.loads(_dp.read_text(encoding="utf-8"))
            if isinstance(_dj, dict):
                summary["digest"] = {
                    "level": _dj.get("level"),
                    "market_asof": _dj.get("market_asof") or summary.get("data_asof"),
                    "data_lag": _dj.get("data_lag"),
                    "decision": _dj.get("decision"),
                    "live_return_pct": _dj.get("live_return_pct"),
                    "live_excess_pct": _dj.get("live_excess_pct"),
                    "days_live": _dj.get("days_live"),
                    "recommend": _dj.get("recommend"),
                    "path_txt": str(OUTPUT_DIR / "digest.txt"),
                    "path_json": str(_dp),
                }
            else:
                summary["digest"] = None
        else:
            summary["digest"] = None
        # progress latest
        _pl = OUTPUT_DIR / "risk_audit" / "progress_latest.json"
        if _pl.exists():
            _pj = json.loads(_pl.read_text(encoding="utf-8"))
            if isinstance(_pj, dict):
                summary["progress"] = {
                    "date": _pj.get("date"),
                    "level": _pj.get("level"),
                    "market_asof": _pj.get("market_asof") or summary.get("data_asof"),
                    "days_live": _pj.get("days_live"),
                    "days_to_ready": _pj.get("days_to_ready"),
                    "live_return_pct": _pj.get("live_return_pct"),
                    "live_excess_pct": _pj.get("live_excess_pct"),
                    "data_lag": _pj.get("data_lag"),
                    "source": _pj.get("source"),
                    "path_txt": str(OUTPUT_DIR / "progress.txt"),
                    "path_jsonl": str(OUTPUT_DIR / "risk_audit" / "progress.jsonl"),
                }
            else:
                summary["progress"] = None
        else:
            summary["progress"] = None
        # pulse
        _pul = OUTPUT_DIR / "risk_audit" / "pulse.json"
        if _pul.exists():
            try:
                _pu = json.loads(_pul.read_text(encoding="utf-8"))
                if isinstance(_pu, dict):
                    summary["pulse"] = {
                        "level": _pu.get("level"),
                        "market_asof": _pu.get("market_asof") or summary.get("data_asof"),
                        "days_live": _pu.get("days_live"),
                        "days_to_ready": _pu.get("days_to_ready"),
                        "eta_note": _pu.get("eta_note"),
                        "decision": _pu.get("decision"),
                        "next_action": _pu.get("next_action"),
                        "why": _pu.get("why"),
                        "recommend": _pu.get("recommend"),
                        "exit_code": _pu.get("exit_code"),
                        "readable_yield": _pu.get("readable_yield"),
                        "live_return_pct": _pu.get("live_return_pct"),
                        "live_excess_pct": _pu.get("live_excess_pct"),
                        "path_txt": str(OUTPUT_DIR / "pulse.txt"),
                        "path_json": str(_pul),
                    }
                else:
                    summary["pulse"] = None
            except Exception:
                summary["pulse"] = None
        else:
            summary["pulse"] = None
        # promote ETA to top-level for scripts
        try:
            _pr = summary.get("progress") if isinstance(summary.get("progress"), dict) else None
            _rd = summary.get("ready") if isinstance(summary.get("ready"), dict) else None
            _yj = summary.get("yield") if isinstance(summary.get("yield"), dict) else None
            _dg = summary.get("digest") if isinstance(summary.get("digest"), dict) else None
            _pu = summary.get("pulse") if isinstance(summary.get("pulse"), dict) else None
            for _src in (_rd, _pr, _pu, _yj, _dg):
                if not isinstance(_src, dict):
                    continue
                if summary.get("days_to_ready") is None and _src.get("days_to_ready") is not None:
                    summary["days_to_ready"] = _src.get("days_to_ready")
                if not summary.get("eta_note") and _src.get("eta_note"):
                    summary["eta_note"] = _src.get("eta_note")
                if not summary.get("ready_level") and _src.get("level"):
                    summary["ready_level"] = _src.get("level")
            _pu2 = summary.get("pulse") if isinstance(summary.get("pulse"), dict) else None
            if isinstance(_pu2, dict):
                if _pu2.get("next_action"):
                    summary["next_action"] = _pu2.get("next_action")
                if _pu2.get("readable_yield") is not None:
                    summary["readable_yield"] = _pu2.get("readable_yield")
                if _pu2.get("exit_code") is not None:
                    summary["pulse_exit"] = _pu2.get("exit_code")
            # ready.json 常有 eta_note, progress 可能没有
            if not summary.get("eta_note"):
                _rp = OUTPUT_DIR / "risk_audit" / "ready.json"
                if _rp.exists():
                    try:
                        _rj = json.loads(_rp.read_text(encoding="utf-8"))
                        if isinstance(_rj, dict) and _rj.get("eta_note"):
                            summary["eta_note"] = _rj.get("eta_note")
                        if summary.get("days_to_ready") is None and _rj.get("days_to_ready") is not None:
                            summary["days_to_ready"] = _rj.get("days_to_ready")
                        if not summary.get("ready_level") and _rj.get("level"):
                            summary["ready_level"] = _rj.get("level")
                    except Exception:
                        pass
            if not summary.get("eta_note") and summary.get("days_to_ready") is not None:
                try:
                    _di = int(summary.get("days_to_ready"))
                    _lag = bool(summary.get("data_lag"))
                    if _di <= 0:
                        summary["eta_note"] = "样本已够, 若无 DATA_LAG 则应 READY"
                    else:
                        summary["eta_note"] = (
                            f"约再 {_di} 个交易日可 READY (Lrets≥5)"
                            + ("; 另需 asof 先推进" if _lag else "")
                        )
                except Exception:
                    pass
        except Exception:
            pass
    except Exception as _ex:
        summary["yield"] = summary.get("yield") or {"error": str(_ex)}
        summary["brief"] = summary.get("brief") if isinstance(summary.get("brief"), dict) else {"error": str(_ex)}
        summary["data_status"] = summary.get("data_status") if isinstance(summary.get("data_status"), dict) else {"error": str(_ex)}
        summary["next"] = summary.get("next") if isinstance(summary.get("next"), dict) else {"error": str(_ex)}
        summary["pull"] = summary.get("pull") if isinstance(summary.get("pull"), dict) else {"error": str(_ex)}
        summary["go"] = summary.get("go") if isinstance(summary.get("go"), dict) else {"error": str(_ex)}
        summary["ready"] = summary.get("ready") if isinstance(summary.get("ready"), dict) else {"error": str(_ex)}
        summary["progress"] = {"error": str(_ex)}
        summary["digest"] = summary.get("digest") if isinstance(summary.get("digest"), dict) else {"error": str(_ex)}
        summary["progress"] = {"error": str(_ex)}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    print("=" * 72)
    print(f"PIPELINE DONE ok={ok_all} asof={data_asof} lag={data_lag}")
    print(f"  alerts error={alert_error_n} warn={alert_warn_n}")
    for x in results:
        flag = "SKIP" if x.get("skipped") else ("OK" if x.get("ok") else "FAIL")
        print(f"  {flag:4s} {x.get('step')} exit={x.get('exit')} {x.get('seconds')}s")
    # 有效收益收口 (signal_live / yield.json)
    try:
        yj = {}
        ypath = OUTPUT_DIR / "risk_audit" / "yield.json"
        if ypath.exists():
            yj = json.loads(ypath.read_text(encoding="utf-8"))
        lj = {}
        lpath = OUTPUT_DIR / "latest.json"
        if lpath.exists():
            lj = json.loads(lpath.read_text(encoding="utf-8"))
        sl = {}
        if isinstance(lj, dict) and isinstance(lj.get("signal_live"), dict):
            sl = lj.get("signal_live") or {}
        if not sl and isinstance(yj, dict):
            sl = yj.get("signal_live") if isinstance(yj.get("signal_live"), dict) else yj
        if sl:
            lr, xs, dl = sl.get("live_return_pct"), sl.get("live_excess_pct"), sl.get("days_live")
            thin = sl.get("thin_live")
            if thin is None and dl is not None:
                try:
                    thin = int(dl) < 5
                except Exception:
                    thin = None
            try:
                lr_s = f"{float(lr):+.3f}%" if lr is not None else "—"
            except Exception:
                lr_s = "—"
            try:
                xs_s = f"{float(xs):+.3f}%" if xs is not None else "—"
            except Exception:
                xs_s = "—"
            tag = " THIN" if thin else ""
            print(
                f"  YIELD live={lr_s}{tag} xs={xs_s} Lrets={dl if dl is not None else '—'} "
                f"asof={sl.get('market_asof') or data_asof or '—'}"
                + (" DATA_LAG" if data_lag else "")
            )
            print("  口径: 有效收益=live%+xs% (锚点→asof); 非全样本")
            # ETA / 距可判 (ready or yield or progress)
            try:
                _eta = None
                _dtr = None
                for _pn in (
                    OUTPUT_DIR / "risk_audit" / "ready.json",
                    OUTPUT_DIR / "risk_audit" / "yield.json",
                    OUTPUT_DIR / "risk_audit" / "progress_latest.json",
                    OUTPUT_DIR / "risk_audit" / "digest.json",
                ):
                    if not _pn.exists():
                        continue
                    _po = json.loads(_pn.read_text(encoding="utf-8"))
                    if not isinstance(_po, dict):
                        continue
                    if _dtr is None and _po.get("days_to_ready") is not None:
                        _dtr = _po.get("days_to_ready")
                    if not _eta and _po.get("eta_note"):
                        _eta = _po.get("eta_note")
                    if _dtr is not None and _eta:
                        break
                if _dtr is not None or _eta:
                    print(
                        f"  ETA dtr={_dtr if _dtr is not None else '—'} "
                        f"{_eta or ''} → ./etf pulse"
                    )
            except Exception:
                pass
            # brief path if exists
            btxt = OUTPUT_DIR / "brief.txt"
            if btxt.exists():
                print(f"  BRIEF: {btxt}")
            dpath = OUTPUT_DIR / "risk_audit" / "data_status.json"
            if dpath.exists():
                try:
                    _dd = json.loads(dpath.read_text(encoding="utf-8"))
                    if isinstance(_dd, dict):
                        print(
                            f"  DATA decision={_dd.get('decision')} "
                            f"asof={_dd.get('market_asof')} lag={_dd.get('data_lag')} "
                            f"stale={_dd.get('latest_stale')}"
                        )
                except Exception:
                    pass
            npath = OUTPUT_DIR / "risk_audit" / "next.json"
            if npath.exists():
                try:
                    _nn = json.loads(npath.read_text(encoding="utf-8"))
                    if isinstance(_nn, dict):
                        print(
                            f"  NEXT decision={_nn.get('decision')} "
                            f"→ {_nn.get('recommend') or '—'}"
                        )
                except Exception:
                    pass
            ppath = OUTPUT_DIR / "risk_audit" / "pull.json"
            if ppath.exists():
                try:
                    _ppj = json.loads(ppath.read_text(encoding="utf-8"))
                    if isinstance(_ppj, dict):
                        print(
                            f"  PULL advanced={_ppj.get('advanced')} "
                            f"asof={(_ppj.get('after') or {}).get('data_asof')} "
                            f"lag={(_ppj.get('after') or {}).get('data_lag')}"
                        )
                except Exception:
                    pass
            gpath = OUTPUT_DIR / "risk_audit" / "go.json"
            if gpath.exists():
                try:
                    _gg = json.loads(gpath.read_text(encoding="utf-8"))
                    if isinstance(_gg, dict):
                        print(
                            f"  GO decision={_gg.get('decision')} "
                            f"asof={_gg.get('market_asof')} lag={_gg.get('data_lag')} "
                            f"→ {_gg.get('recommend') or 'etf go'}"
                        )
                except Exception:
                    pass
            rpath = OUTPUT_DIR / "risk_audit" / "ready.json"
            if rpath.exists():
                try:
                    _rr = json.loads(rpath.read_text(encoding="utf-8"))
                    if isinstance(_rr, dict):
                        print(
                            f"  READY level={_rr.get('level')} "
                            f"asof={_rr.get('market_asof')} "
                            f"Lrets={_rr.get('days_live')} "
                            f"live={_rr.get('live_return_pct')} xs={_rr.get('live_excess_pct')}"
                        )
                except Exception:
                    pass
            dgst = OUTPUT_DIR / "risk_audit" / "digest.json"
            if dgst.exists():
                try:
                    _ddg = json.loads(dgst.read_text(encoding="utf-8"))
                    if isinstance(_ddg, dict):
                        print(
                            f"  DIGEST level={_ddg.get('level')} "
                            f"decision={_ddg.get('decision')} "
                            f"→ {_ddg.get('recommend') or './etf go'}"
                        )
                        dtxt = OUTPUT_DIR / "digest.txt"
                        if dtxt.exists():
                            print("  ---- digest ----")
                            for _ln in dtxt.read_text(encoding="utf-8").splitlines()[:12]:
                                print(f"  {_ln}")
                except Exception:
                    pass
            pth = OUTPUT_DIR / "risk_audit" / "progress_latest.json"
            if pth.exists():
                try:
                    _pp = json.loads(pth.read_text(encoding="utf-8"))
                    if isinstance(_pp, dict):
                        print(
                            f"  PROGRESS date={_pp.get('date')} level={_pp.get('level')} "
                            f"Lrets={_pp.get('days_live')} dtr={_pp.get('days_to_ready')} "
                            f"live={_pp.get('live_return_pct')} xs={_pp.get('live_excess_pct')}"
                        )
                except Exception:
                    pass
            pth2 = OUTPUT_DIR / "risk_audit" / "pulse.json"
            if pth2.exists():
                try:
                    _pu2 = json.loads(pth2.read_text(encoding="utf-8"))
                    if isinstance(_pu2, dict):
                        print(
                            f"  PULSE level={_pu2.get('level')} action={_pu2.get('next_action')} "
                            f"dtr={_pu2.get('days_to_ready')} readable={_pu2.get('readable_yield')} "
                            f"→ {_pu2.get('recommend') or './etf'}"
                        )
                except Exception:
                    pass
        else:
            print("  YIELD: 无 signal_live → python3 scripts/etf.py yield")
    except Exception as ex:
        print(f"  YIELD 摘要跳过: {ex}")
    print("  下一步: ./etf pulse | ./etf wait --timeout 600")
    print(f"WROTE {out}")
    print(f"LOG  {log_path}")
    if not ok_all:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
