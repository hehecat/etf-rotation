#!/usr/bin/env python3
"""多研究轴并行策略搜索.

用法:
  python3 scripts/search_lanes.py init --run-id smoke1 --axes vt
  python3 scripts/search_lanes.py run-lane --run-id smoke1 --lane vt --workers 2
  python3 scripts/search_lanes.py merge --run-id smoke1 --top 30
  python3 scripts/search_lanes.py gate --run-id smoke1
  python3 scripts/search_lanes.py promote --run-id smoke1 --candidate <id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.backtest import bt  # noqa: E402
from etf_rotation.search_data import load_pool_data, materialize_params  # noqa: E402
from etf_rotation.search_spec import (  # noqa: E402
    ANCHORS,
    build_lane_candidates,
    default_axes,
    is_shortlist_eligible,
    score_metrics,
)

RUNS = ROOT / "output" / "search_runs"


def run_dir(run_id: str) -> Path:
    return RUNS / run_id


def lane_dir(run_id: str, lane: str) -> Path:
    return run_dir(run_id) / "lanes" / lane


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def cmd_init(args: argparse.Namespace) -> None:
    run_id = args.run_id
    if run_id in ("", "auto", None):
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    axes = [a.strip() for a in (args.axes or ",".join(default_axes())).split(",") if a.strip()]
    rd = run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "axes": axes,
        "pool": args.pool,
        "count": args.count,
        "adjust": args.adjust,
        "comm": args.comm,
        "lanes": {},
    }
    for axis in axes:
        cands = build_lane_candidates(axis)
        # 锚点挂到每个 lane 文件尾 (merge 去重)
        ld = lane_dir(run_id, axis)
        ld.mkdir(parents=True, exist_ok=True)
        cand_path = ld / "candidates.jsonl"
        if cand_path.exists():
            cand_path.unlink()
        rows = cands + [a for a in ANCHORS]
        append_jsonl(cand_path, rows)
        write_json(
            ld / "status.json",
            {"state": "pending", "n_done": 0, "n_total": len(rows), "error": None},
        )
        manifest["lanes"][axis] = {"n_candidates": len(rows), "path": str(cand_path.relative_to(ROOT))}
    write_json(rd / "manifest.json", manifest)
    print(f"INIT run_id={run_id}")
    for ax, info in manifest["lanes"].items():
        print(f"  lane={ax} n={info['n_candidates']}")
    print(f"  dir={rd}")


# 进程池 worker 级数据缓存 (initializer 填充)
_WORKER_DATA = None
_WORKER_COMM = None


def _extra_codes_from_cands(cands: list[dict]) -> list[str]:
    """从 overrides 收集 park_assets / extra_universe, 供数据加载."""
    codes: list[str] = []
    for c in cands:
        o = c.get("overrides") or {}
        for key in ("park_assets", "extra_universe"):
            v = o.get(key) or []
            if isinstance(v, (list, tuple)):
                codes.extend(str(x) for x in v if x)
    # 稳定去重
    return list(dict.fromkeys(codes))


def _worker_init(pool: str, count: int, adjust: str, comm: float, extra_codes: list | None = None) -> None:
    global _WORKER_DATA, _WORKER_COMM
    _WORKER_DATA = load_pool_data(pool, count, adjust, extra_codes=list(extra_codes or []))
    _WORKER_COMM = comm


def _eval_one_cached(spec: dict) -> dict:
    """子进程评测; 依赖 _worker_init 预加载数据."""
    global _WORKER_DATA, _WORKER_COMM
    if _WORKER_DATA is None:
        raise RuntimeError("worker data not initialized")
    data, _bench, _sd = _WORKER_DATA
    p = materialize_params(spec["base"], spec.get("overrides") or {})
    r = bt(data, p, commission=_WORKER_COMM)
    if not r:
        return {
            "id": spec["id"],
            "lane": spec["lane"],
            "base": spec["base"],
            "overrides": spec.get("overrides") or {},
            "tags": spec.get("tags") or [],
            "error": "no_data",
            "sharpe": 0,
            "calmar": 0,
            "dd": -1,
            "ann": 0,
            "ret": 0,
            "n": 0,
            "expectancy": None,
            "d0": None,
            "d1": None,
        }
    return {
        "id": spec["id"],
        "lane": spec["lane"],
        "base": spec["base"],
        "overrides": spec.get("overrides") or {},
        "tags": spec.get("tags") or [],
        "ann": r.get("ann"),
        "dd": r.get("dd"),
        "sharpe": r.get("sharpe"),
        "calmar": r.get("calmar"),
        "ret": r.get("ret"),
        "n": r.get("n"),
        "expectancy": r.get("expectancy"),
        "d0": r.get("d0"),
        "d1": r.get("d1"),
        "error": None,
    }


def _eval_one(payload: tuple) -> dict:
    """兼容旧接口: payload=(spec, pool, count, adjust, comm); 每调用加载一次."""
    spec, pool, count, adjust, comm = payload
    o = spec.get("overrides") or {}
    extras = list(o.get("park_assets") or []) + list(o.get("extra_universe") or [])
    data, bench, sd = load_pool_data(pool, count, adjust, extra_codes=extras)
    p = materialize_params(spec["base"], spec.get("overrides") or {})
    r = bt(data, p, commission=comm)
    if not r:
        return {
            "id": spec["id"],
            "lane": spec["lane"],
            "base": spec["base"],
            "overrides": spec.get("overrides") or {},
            "tags": spec.get("tags") or [],
            "error": "no_data",
            "sharpe": 0,
            "calmar": 0,
            "dd": -1,
            "ann": 0,
            "ret": 0,
            "n": 0,
            "expectancy": None,
            "d0": None,
            "d1": None,
        }
    return {
        "id": spec["id"],
        "lane": spec["lane"],
        "base": spec["base"],
        "overrides": spec.get("overrides") or {},
        "tags": spec.get("tags") or [],
        "ann": r.get("ann"),
        "dd": r.get("dd"),
        "sharpe": r.get("sharpe"),
        "calmar": r.get("calmar"),
        "ret": r.get("ret"),
        "n": r.get("n"),
        "expectancy": r.get("expectancy"),
        "d0": r.get("d0"),
        "d1": r.get("d1"),
        "error": None,
    }


def cmd_run_lane(args: argparse.Namespace) -> None:
    run_id = args.run_id
    lane = args.lane
    workers = int(args.workers)
    if workers <= 0:
        workers = min(4, os.cpu_count() or 2)
    workers = min(workers, 4)

    ld = lane_dir(run_id, lane)
    cand_path = ld / "candidates.jsonl"
    if not cand_path.exists():
        raise SystemExit(f"缺少 candidates: {cand_path}; 先 init")

    # 读 manifest 取数据参数
    man = json.loads((run_dir(run_id) / "manifest.json").read_text(encoding="utf-8"))
    pool = man.get("pool", "pool_long_proxy")
    count = int(man.get("count", 3200))
    adjust = man.get("adjust", "none")
    comm = float(man.get("comm", 0.0003))

    cands = read_jsonl(cand_path)
    n_total = len(cands)
    write_json(ld / "status.json", {"state": "running", "n_done": 0, "n_total": n_total, "error": None})
    # 清理旧 part
    for p in ld.glob("metrics.part*.jsonl"):
        p.unlink()
    metrics_path = ld / "metrics.jsonl"
    if metrics_path.exists():
        metrics_path.unlink()

    print(f"RUN-LANE run={run_id} lane={lane} n={n_total} workers={workers}")
    t0 = time.time()
    extras = _extra_codes_from_cands(cands)
    payloads = [(c, pool, count, adjust, comm) for c in cands]
    done = 0
    # 主进程串行预热一次数据 (确保缓存; 含 park/extra 代码)
    load_pool_data(pool, count, adjust, extra_codes=extras)
    print(f"  cache warm ok extras={extras} ({time.time()-t0:.1f}s)")
    used_pool = False
    if workers == 1:
        for i, pl in enumerate(payloads):
            m = _eval_one(pl)
            part_rows[0].append(m)
            done += 1
            if done % 10 == 0 or done == n_total:
                write_json(
                    ld / "status.json",
                    {"state": "running", "n_done": done, "n_total": n_total, "error": None},
                )
                print(f"  {done}/{n_total} {m['id'][:40]} sh={m.get('sharpe', 0):.2f}", flush=True)
    else:
        # 初始化器只加载一次数据; 若进程池卡住/失败 → 整 lane 串行回退
        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_worker_init,
                initargs=(pool, count, adjust, comm, extras),
            ) as ex:
                futs = {ex.submit(_eval_one_cached, cands[i]): i for i in range(n_total)}
                for fut in as_completed(futs):
                    idx = futs[fut]
                    try:
                        m = fut.result(timeout=180)
                    except Exception as e:
                        spec = cands[idx]
                        m = {
                            "id": spec["id"],
                            "lane": spec["lane"],
                            "base": spec["base"],
                            "overrides": spec.get("overrides") or {},
                            "tags": spec.get("tags") or [],
                            "error": str(e),
                            "sharpe": 0,
                            "calmar": 0,
                            "dd": -1,
                            "ann": 0,
                            "ret": 0,
                            "n": 0,
                            "expectancy": None,
                            "d0": None,
                            "d1": None,
                        }
                    wid = idx % workers
                    part_path = ld / f"metrics.part{wid}.jsonl"
                    append_jsonl(part_path, [m])
                    done += 1
                    if done % 10 == 0 or done == n_total:
                        write_json(
                            ld / "status.json",
                            {
                                "state": "running",
                                "n_done": done,
                                "n_total": n_total,
                                "error": None,
                            },
                        )
                        print(
                            f"  {done}/{n_total} {m['id'][:40]} sh={float(m.get('sharpe') or 0):.2f}",
                            flush=True,
                        )
            used_pool = True
            # 若完成数明显不足, 视为卡死残留, 串行补齐
            if done < n_total * 0.9:
                raise RuntimeError(f"ProcessPool incomplete done={done}/{n_total}")
        except Exception as e:
            print(f"  ProcessPool 失败/不完整, 回退 workers=1: {e}", flush=True)
            # 清理残缺 part, 全量串行重跑 (保证正确性)
            for p in ld.glob("metrics.part*.jsonl"):
                p.unlink()
            part_rows = {0: []}
            done = 0
            for i, pl in enumerate(payloads):
                m = _eval_one(pl)
                part_rows[0].append(m)
                done = i + 1
                if done % 10 == 0 or done == n_total:
                    write_json(
                        ld / "status.json",
                        {
                            "state": "running",
                            "n_done": done,
                            "n_total": n_total,
                            "error": None,
                            "fallback": "serial",
                        },
                    )
                    print(
                        f"  {done}/{n_total} {m['id'][:40]} sh={m.get('sharpe', 0):.2f}",
                        flush=True,
                    )
            append_jsonl(ld / "metrics.part0.jsonl", part_rows[0])
            used_pool = False

    # 合并 part → metrics.jsonl
    all_m = []
    for p in sorted(ld.glob("metrics.part*.jsonl")):
        all_m.extend(read_jsonl(p))
    # 若 workers=1 且写了 part_rows
    if not all_m and part_rows.get(0):
        all_m = part_rows[0]
        append_jsonl(ld / "metrics.part0.jsonl", all_m)
    if metrics_path.exists():
        metrics_path.unlink()
    append_jsonl(metrics_path, all_m)
    write_json(
        ld / "status.json",
        {"state": "done", "n_done": len(all_m), "n_total": n_total, "error": None},
    )
    print(f"DONE lane={lane} n={len(all_m)} elapsed={time.time()-t0:.1f}s → {metrics_path}")


def cmd_merge(args: argparse.Namespace) -> None:
    run_id = args.run_id
    top = int(args.top)
    keep_delever = bool(args.keep_delever)
    rd = run_dir(run_id)
    man = json.loads((rd / "manifest.json").read_text(encoding="utf-8"))
    all_rows: list[dict] = []
    for lane in man.get("lanes", {}):
        mp = lane_dir(run_id, lane) / "metrics.jsonl"
        all_rows.extend(read_jsonl(mp))
    if not all_rows:
        raise SystemExit("无 metrics 可合并; 先 run-lane")

    # 按 id 去重 (锚点重复): 保留分数更高者
    best: dict[str, dict] = {}
    for m in all_rows:
        if m.get("error"):
            continue
        cid = m["id"]
        sc = score_metrics(m)
        m["_score"] = sc
        if cid not in best or sc > best[cid].get("_score", -1e18):
            best[cid] = m
    rows = list(best.values())

    merged_dir = rd / "merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    all_path = merged_dir / "all_metrics.jsonl"
    if all_path.exists():
        all_path.unlink()
    append_jsonl(all_path, rows)

    elig = [m for m in rows if is_shortlist_eligible(m, keep_delever=keep_delever)]
    elig.sort(key=lambda x: x.get("_score", 0), reverse=True)

    # 强制包含锚点 id (不要按 base==c01_q10 扩容)
    force_ids = {
        "anchor_c01_q10_vt11",
        "anchor_c01_q10",
        "anchor_c01_q10_vt13",
        "anchor_c01",
        "anchor_c01_q10_vt09_oh35",
        "anchor_c01_q10_vt09_soft_oh40",
        "anchor_c01_q10_vt08_soft_oh38",
    }
    short: list[dict] = []
    seen = set()
    for m in elig:
        if m["id"] in seen:
            continue
        short.append(m)
        seen.add(m["id"])
        if len(short) >= top:
            break
    for m in rows:
        if m["id"] in force_ids and m["id"] not in seen:
            short.append(m)
            seen.add(m["id"])

    out = []
    for m in short:
        out.append(
            {
                "id": m["id"],
                "lane": m.get("lane"),
                "base": m["base"],
                "overrides": m.get("overrides") or {},
                "tags": m.get("tags") or [],
                "metrics": {
                    k: m.get(k)
                    for k in ["ann", "dd", "sharpe", "calmar", "ret", "n", "expectancy", "d0", "d1"]
                },
                "score": m.get("_score"),
            }
        )
    write_json(merged_dir / "shortlist.json", out)
    print(f"MERGE run={run_id} all={len(rows)} shortlist={len(out)} → {merged_dir / 'shortlist.json'}")
    for i, m in enumerate(out[:10], 1):
        mt = m["metrics"]
        print(
            f"  #{i:02d} {m['id'][:42]:42s} sh={mt.get('sharpe', 0):.2f} "
            f"dd={float(mt.get('dd') or 0)*100:.1f}% ann={float(mt.get('ann') or 0):+.1f}%"
        )


def cmd_gate(args: argparse.Namespace) -> None:
    run_id = args.run_id
    rd = run_dir(run_id)
    short_path = rd / "merged" / "shortlist.json"
    if not short_path.exists():
        raise SystemExit("先 merge")
    gate_dir = rd / "gate"
    gate_dir.mkdir(parents=True, exist_ok=True)

    long_out = gate_dir / "robust_long.json"
    etf_out = gate_dir / "robust_etf.json"
    etf_soft_out = gate_dir / "robust_etf_soft.json"

    # 长代理硬门禁
    cmd_long = [
        sys.executable,
        str(ROOT / "scripts" / "validate_robust.py"),
        "--from-shortlist",
        str(short_path),
        "--pool",
        "pool_long_proxy",
        "--count",
        "3200",
        "--comm",
        "0.0003",
        "--out",
        str(long_out),
        "--gate",
        "--require-pass",
        "anchor_c01_q10_vt11",
    ]
    print("GATE long_proxy ...", flush=True)
    r1 = subprocess.run(cmd_long, cwd=str(ROOT))
    print(f"  long exit={r1.returncode}")

    # ETF 全套硬门禁 (短样本常全灭, 仅作对照)
    cmd_etf = [
        sys.executable,
        str(ROOT / "scripts" / "validate_robust.py"),
        "--from-shortlist",
        str(short_path),
        "--preset",
        "etf_core",
        "--out",
        str(etf_out),
    ]
    print("GATE etf_core ...", flush=True)
    r2 = subprocess.run(cmd_etf, cwd=str(ROOT))
    print(f"  etf exit={r2.returncode}")

    # ETF 短样本宽松档 (晋级用)
    cmd_soft = [
        sys.executable,
        str(ROOT / "scripts" / "validate_robust.py"),
        "--from-shortlist",
        str(short_path),
        "--preset",
        "etf_soft",
        "--out",
        str(etf_soft_out),
    ]
    print("GATE etf_soft ...", flush=True)
    r3 = subprocess.run(cmd_soft, cwd=str(ROOT))
    print(f"  etf_soft exit={r3.returncode}")

    long_rep = json.loads(long_out.read_text(encoding="utf-8")) if long_out.exists() else {}
    etf_rep = json.loads(etf_out.read_text(encoding="utf-8")) if etf_out.exists() else {}
    soft_rep = json.loads(etf_soft_out.read_text(encoding="utf-8")) if etf_soft_out.exists() else {}
    lines = [
        f"# Gate summary · {run_id}",
        "",
        f"- long exit: {r1.returncode}",
        f"- etf_core exit: {r2.returncode}",
        f"- etf_soft exit: {r3.returncode}",
        "",
        "## 晋级规则",
        "",
        "- 长代理 14/14 合格",
        "- ETF 宽松档 (`etf_soft`) 非不合格",
        "- etf_core 仅对照, 短样本全灭不单独否决",
        "",
        "## Candidates",
        "",
    ]
    promote_candidates = []
    for cid, rec in (long_rep.get("strategies") or {}).items():
        g = rec.get("grade") or {}
        st = g.get("status", "?")
        full = rec.get("full") or {}
        etf_st = ((etf_rep.get("strategies") or {}).get(cid) or {}).get("grade", {}).get("status", "缺")
        soft_st = ((soft_rep.get("strategies") or {}).get(cid) or {}).get("grade", {}).get("status", "缺")
        lines.append(
            f"- `{cid}`: long={st} etf_soft={soft_st} etf_core={etf_st} "
            f"ann={full.get('ann', 0):+.1f}% dd={float(full.get('dd') or 0)*100:.1f}% "
            f"sh={float(full.get('sharpe') or 0):.2f} hard={g.get('hard_fail')}"
        )
        # 晋级: 长代理合格 + ETF 宽松非不合格
        if st == "合格" and soft_st != "不合格" and not str(cid).startswith("anchor_"):
            h = hashlib.md5(cid.encode()).hexdigest()[:6]
            lane = cid.split("_", 1)[0]
            suggest = f"c01_q10_{lane}_{h}"
            lines.append(f"  - promote 建议: `{suggest}` ← `--candidate {cid}`")
            promote_candidates.append(cid)
    if not promote_candidates:
        lines.append("")
        lines.append("no_new_pass (保留 c01_q10_vt11)")
    else:
        lines.append("")
        lines.append(f"promote_ready: {', '.join(promote_candidates)}")
    summary_path = gate_dir / "summary.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"WROTE {summary_path}")
    print("\n".join(lines))


def cmd_promote(args: argparse.Namespace) -> None:
    run_id = args.run_id
    cand_id = args.candidate
    rd = run_dir(run_id)
    short = json.loads((rd / "merged" / "shortlist.json").read_text(encoding="utf-8"))
    item = next((x for x in short if x["id"] == cand_id), None)
    if not item:
        raise SystemExit(f"shortlist 无此 id: {cand_id}")
    long_path = rd / "gate" / "robust_long.json"
    etf_path = rd / "gate" / "robust_etf.json"
    soft_path = rd / "gate" / "robust_etf_soft.json"
    if not long_path.exists():
        raise SystemExit("先 gate")
    long_rep = json.loads(long_path.read_text(encoding="utf-8"))
    etf_rep = json.loads(etf_path.read_text(encoding="utf-8")) if etf_path.exists() else {}
    soft_rep = json.loads(soft_path.read_text(encoding="utf-8")) if soft_path.exists() else {}
    g = (long_rep.get("strategies") or {}).get(cand_id, {}).get("grade") or {}
    if g.get("status") != "合格":
        raise SystemExit(f"长代理未合格: {g.get('status')} hard={g.get('hard_fail')}")
    soft_st = ((soft_rep.get("strategies") or {}).get(cand_id) or {}).get("grade", {}).get("status")
    etf_st = ((etf_rep.get("strategies") or {}).get(cand_id) or {}).get("grade", {}).get("status")
    # 优先 etf_soft; 无 soft 结果时回退 etf_core
    if soft_path.exists():
        if soft_st == "不合格":
            raise SystemExit(f"ETF 宽松档不合格 (etf_soft={soft_st}), 拒绝 promote")
    elif etf_st == "不合格":
        raise SystemExit("ETF 不合格, 拒绝 promote")

    base_name = item["base"]
    base_cfg = json.loads((ROOT / "config" / f"{base_name}.json").read_text(encoding="utf-8"))
    ovr = item.get("overrides") or {}
    # 映射 backtest 字段 → JSON 策略字段
    key_map = {
        "w": "weights",
        "rb": "rb_days",
        "ps": "position_pct",
        "abs_m": "require_abs_mom",
    }
    new_cfg = dict(base_cfg)
    for k, v in ovr.items():
        jk = key_map.get(k, k)
        new_cfg[jk] = v
    new_cfg["research"] = True
    new_cfg["frozen"] = False
    h = hashlib.md5(cand_id.encode()).hexdigest()[:6]
    lane = item.get("lane") or "x"
    name = args.name or f"c01_q10_{lane}_{h}"
    new_cfg["name"] = new_cfg.get("name", name) + f"/{cand_id}"
    new_cfg["note"] = (
        f"search promote run={run_id} id={cand_id}; 长代理合格且 ETF 非不合格. 研究影子非生产."
    )
    out = ROOT / "config" / f"{name}.json"
    if out.exists() and not args.force:
        raise SystemExit(f"已存在 {out}; 用 --force 覆盖")
    out.write_text(json.dumps(new_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PROMOTE → {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="多研究轴并行策略搜索")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--run-id", default="auto")
    p_init.add_argument("--axes", default=",".join(default_axes()))
    p_init.add_argument("--pool", default="pool_long_proxy")
    p_init.add_argument("--count", type=int, default=3200)
    p_init.add_argument("--adjust", default="none")
    p_init.add_argument("--comm", type=float, default=0.0003)

    p_run = sub.add_parser("run-lane")
    p_run.add_argument("--run-id", required=True)
    p_run.add_argument("--lane", required=True)
    p_run.add_argument("--workers", type=int, default=0, help="默认 min(4,cpu)")

    p_merge = sub.add_parser("merge")
    p_merge.add_argument("--run-id", required=True)
    p_merge.add_argument("--top", type=int, default=30)
    p_merge.add_argument("--keep-delever", action="store_true")

    p_gate = sub.add_parser("gate")
    p_gate.add_argument("--run-id", required=True)
    p_gate.add_argument("--require-pass-any", action="store_true", help="占位兼容")

    p_prom = sub.add_parser("promote")
    p_prom.add_argument("--run-id", required=True)
    p_prom.add_argument("--candidate", required=True)
    p_prom.add_argument("--name", default=None)
    p_prom.add_argument("--force", action="store_true")

    args = ap.parse_args()
    if args.cmd == "init":
        cmd_init(args)
    elif args.cmd == "run-lane":
        cmd_run_lane(args)
    elif args.cmd == "merge":
        cmd_merge(args)
    elif args.cmd == "gate":
        cmd_gate(args)
    elif args.cmd == "promote":
        cmd_promote(args)
    else:
        raise SystemExit(f"unknown cmd {args.cmd}")


if __name__ == "__main__":
    main()
