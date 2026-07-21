#!/usr/bin/env python3
"""周检编排: healthcheck → (可选 pipeline dry) → monitor 告警.

用法:
  python3 scripts/run_weekly.py
  python3 scripts/run_weekly.py --quick
  python3 scripts/run_weekly.py --with-pipeline-dry
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import LOG_DIR, OUTPUT_DIR, ensure_dirs  # noqa: E402

PY = sys.executable
MAINLINE = "c01_q10_vt08_soft_oh38,c01_q10_vt09_oh35,c01_q10_vt11"


def run(cmd: list[str]) -> dict:
    t0 = time.time()
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    return {
        "cmd": cmd,
        "exit": r.returncode,
        "ok": r.returncode == 0,
        "seconds": round(time.time() - t0, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="研究主线周检")
    ap.add_argument("--quick", action="store_true", help="healthcheck --quick")
    ap.add_argument(
        "--with-pipeline-dry",
        action="store_true",
        help="额外跑 pipeline dry-run (healthcheck 已含 pipeline 时可省略)",
    )
    ap.add_argument("--skip-healthcheck", action="store_true")
    ap.add_argument("--skip-monitor", action="store_true")
    ap.add_argument("--min-rets", type=int, default=20)
    ap.add_argument("--out", default="output/risk_audit/weekly_last.json")
    args = ap.parse_args()

    ensure_dirs()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"weekly_{stamp}.log"

    results = []
    print("=" * 72)
    print(f"WEEKLY CHECK {stamp}")
    print("=" * 72)

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
        if not args.skip_healthcheck:
            cmd = [PY, str(ROOT / "scripts" / "research_healthcheck.py")]
            if args.quick:
                cmd.append("--quick")
            results.append({"step": "healthcheck", **run(cmd)})

        if args.with_pipeline_dry:
            results.append(
                {
                    "step": "pipeline_dry",
                    **run(
                        [
                            PY,
                            str(ROOT / "scripts" / "run_pipeline.py"),
                            "--dry-run",
                            "--require-trading-day",
                            "--steps",
                            "signal,monitor,compare,live,summary,status,today,pages",
                            "--pages-out",
                            str(OUTPUT_DIR / "site"),
                            "--no-shadow-exec",
                        ]
                    ),
                }
            )

        if not args.skip_monitor:
            results.append(
                {
                    "step": "monitor_alert",
                    **run(
                        [
                            PY,
                            str(ROOT / "scripts" / "shadow_monitor.py"),
                            "--shadows",
                            MAINLINE,
                            "--min-rets",
                            str(args.min_rets),
                            "--fail-on-alert",
                            "--text-out",
                            str(OUTPUT_DIR / "shadow_monitor.txt"),
                            "--json-out",
                            str(OUTPUT_DIR / "risk_audit" / "shadow_monitor.json"),
                        ]
                    ),
                }
            )

        results.append(
            {
                "step": "status",
                **run(
                    [
                        PY,
                        str(ROOT / "scripts" / "research_status.py"),
                        "--json-out",
                        str(OUTPUT_DIR / "risk_audit" / "research_status.json"),
                        "--text-out",
                        str(OUTPUT_DIR / "research_status.txt"),
                    ]
                ),
            }
        )
    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        log_f.close()

    ok_all = all(r.get("ok") for r in results)
    # monitor exit 2 = alerts
    mon = next((r for r in results if r.get("step") == "monitor_alert"), None)
    alert_fail = mon is not None and mon.get("exit") == 2

    payload = {
        "stamp": stamp,
        "ok": ok_all,
        "alert_fail": alert_fail,
        "results": results,
        "log": str(log_path),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print()
    print("=" * 72)
    print(f"WEEKLY DONE ok={ok_all} alert_fail={alert_fail}")
    for r in results:
        print(f"  {'OK' if r['ok'] else 'FAIL':4s} {r['step']} exit={r['exit']} {r['seconds']}s")
    print(f"WROTE {out}")
    print(f"LOG  {log_path}")
    if not ok_all:
        raise SystemExit(2 if alert_fail else 1)


if __name__ == "__main__":
    main()
