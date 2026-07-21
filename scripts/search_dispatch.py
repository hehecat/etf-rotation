#!/usr/bin/env python3
"""一键多轴搜索编排 (CPU 护栏).

用法:
  python3 scripts/search_dispatch.py --run-id auto --axes vt,regime --max-agents 2 --workers 2
  python3 scripts/search_dispatch.py --run-id auto --axes vt --max-agents 6 --workers 3
    # 会降级 workers 使 max_agents*workers<=4 (或 workers=1)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LANES_PY = ROOT / "scripts" / "search_lanes.py"


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    return r.returncode


def main() -> None:
    ap = argparse.ArgumentParser(description="多轴搜索编排 + CPU 护栏")
    ap.add_argument("--run-id", default="auto")
    ap.add_argument("--axes", default="vt,regime,factor,stop_rb")
    ap.add_argument("--max-agents", type=int, default=2, help="同时 run-lane 数")
    ap.add_argument("--workers", type=int, default=2, help="每 lane 进程 worker 数")
    ap.add_argument("--pool", default="pool_long_proxy")
    ap.add_argument("--count", type=int, default=3200)
    ap.add_argument("--comm", type=float, default=0.0003)
    ap.add_argument("--skip-gate", action="store_true")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    max_agents = max(1, min(8, int(args.max_agents)))
    workers = max(1, int(args.workers))
    # 硬规则: max_agents * workers <= 4 or workers == 1
    if max_agents * workers > 4 and workers != 1:
        old = workers
        workers = 1
        print(
            f"WARN: max_agents({max_agents})*workers({old})>4 → 降级 workers={workers}",
            flush=True,
        )
    workers = min(workers, 4)

    run_id = args.run_id
    if run_id in ("", "auto", None):
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    axes = [a.strip() for a in args.axes.split(",") if a.strip()]
    print(
        f"DISPATCH run_id={run_id} axes={axes} max_agents={max_agents} workers={workers} cpu={os.cpu_count()}",
        flush=True,
    )

    rc = run(
        [
            PY,
            str(LANES_PY),
            "init",
            "--run-id",
            run_id,
            "--axes",
            ",".join(axes),
            "--pool",
            args.pool,
            "--count",
            str(args.count),
            "--comm",
            str(args.comm),
        ]
    )
    if rc != 0:
        raise SystemExit(rc)

    def run_one(lane: str) -> tuple[str, int]:
        code = run(
            [
                PY,
                str(LANES_PY),
                "run-lane",
                "--run-id",
                run_id,
                "--lane",
                lane,
                "--workers",
                str(workers),
            ]
        )
        return lane, code

    # 有限并行 run-lane
    failed = []
    with ThreadPoolExecutor(max_workers=max_agents) as ex:
        futs = {ex.submit(run_one, lane): lane for lane in axes}
        for fut in as_completed(futs):
            lane, code = fut.result()
            if code != 0:
                failed.append((lane, code))
                print(f"LANE FAIL {lane} exit={code}", flush=True)
            else:
                print(f"LANE OK {lane}", flush=True)

    if failed:
        print(f"部分 lane 失败: {failed}; 仍尝试 merge", flush=True)

    rc = run([PY, str(LANES_PY), "merge", "--run-id", run_id, "--top", str(args.top)])
    if rc != 0:
        raise SystemExit(rc)

    if not args.skip_gate:
        rc = run([PY, str(LANES_PY), "gate", "--run-id", run_id])
        # gate 子进程可能因 require 失败非0; 仍打印路径
        print(f"gate exit={rc}")

    summary = ROOT / "output" / "search_runs" / run_id / "gate" / "summary.md"
    short = ROOT / "output" / "search_runs" / run_id / "merged" / "shortlist.json"
    print()
    print(f"DONE run_id={run_id}")
    print(f"  shortlist: {short}")
    print(f"  summary:   {summary}")
    if summary.exists():
        print(summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
