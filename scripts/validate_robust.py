#!/usr/bin/env python3
"""防过拟合验收: 样本外 + 前向分析 + 多状态.

标准 (对齐用户清单 / TradeStation 思路):
  1) 样本外: 前后半段不严重背离
  2) 前向分析 (Walk-Forward):
     - 总体盈利
     - 前向效率 WFE >= 50%
     - >=50% 前向窗口盈利
     - 无单一窗口贡献 >50% 总前向利润
     - 最大回撤不超过 40% (全样本与前向拼接)
  3) 多状态: 牛/熊/震荡年均有可接受表现, 非单一年份/单一状态赚钱

用法:
  python3 scripts/validate_robust.py
  python3 scripts/validate_robust.py --strategies c01,c01_q10,c01_q10_vt13
  python3 scripts/validate_robust.py --pool pool_long_proxy --count 3200
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation import config as cfgmod
from etf_rotation import data as data_mod
from etf_rotation.backtest import bt, format_result


def load_data(
    pool_name: str,
    count: int,
    adjust: str = "none",
    extra_codes: list[str] | None = None,
) -> tuple[dict, str]:
    from etf_rotation.search_data import load_pool_data

    data, bench, _sd = load_pool_data(
        pool_name,
        count,
        adjust,
        extra_codes=list(extra_codes or []),
    )
    return data, bench


def common_dates(data: dict) -> list[str]:
    # 与 backtest 一致: 短历史代码不进交集
    lens = {c: len(data[c].get("dates") or []) for c in data}
    max_n = max(lens.values()) if lens else 0
    core = [c for c, n in lens.items() if max_n == 0 or n >= max_n * 0.5]
    if len(core) < 2:
        core = list(data.keys())
    return sorted(set.intersection(*[set(data[c]["dates"]) for c in core]))


def year_of(d: str) -> str:
    return d[:4]


def bench_year_ret(data: dict, bench: str, y: str) -> float | None:
    if bench not in data:
        return None
    pairs = [
        (d, c)
        for d, c in zip(data[bench]["dates"], data[bench]["close"])
        if d.startswith(y) and c and c > 0
    ]
    if len(pairs) < 40:
        return None
    return pairs[-1][1] / pairs[0][1] - 1


def regime_of_bench(by: float) -> str:
    if by >= 0.15:
        return "bull"
    if by <= -0.10:
        return "bear"
    return "chop"


def safe_div(a: float, b: float) -> float:
    if abs(b) < 1e-12:
        return 0.0 if abs(a) < 1e-12 else (1.5 if a > 0 else 0.0)
    return a / b


def clip_wfe(x: float) -> float:
    # 前向效率夹紧到 [0, 1.5], 负 IS 时单独处理
    return max(0.0, min(1.5, x))


def eval_window(data: dict, p: dict, d0: str, d1: str, comm: float) -> dict | None:
    r = bt(data, p, date_range=(d0, d1), commission=comm)
    if not r or r.get("days", 0) < 40:
        return None
    return r


def oos_split(data: dict, p: dict, sd: list[str], comm: float) -> dict:
    """前后半段样本外."""
    mid = len(sd) // 2
    d_is0, d_is1 = sd[0], sd[mid - 1]
    d_oos0, d_oos1 = sd[mid], sd[-1]
    ris = eval_window(data, p, d_is0, d_is1, comm)
    roos = eval_window(data, p, d_oos0, d_oos1, comm)
    full = eval_window(data, p, sd[0], sd[-1], comm)
    if not ris or not roos or not full:
        return {"ok": False, "reason": "window_too_short"}
    # 用年化比作效率; IS 亏损且 OOS 盈利记 1.0, 双亏记 0
    if ris["ann"] > 1e-6:
        fae = clip_wfe(roos["ann"] / ris["ann"])
    elif roos["ann"] >= 0:
        fae = 1.0
    else:
        fae = 0.0
    collapse = (roos["ann"] < ris["ann"] - 15) or (roos["sharpe"] < ris["sharpe"] - 0.5 and roos["ann"] < 0)
    return {
        "ok": True,
        "is": {k: ris.get(k) for k in ["ann", "dd", "sharpe", "calmar", "ret", "days", "d0", "d1"]},
        "oos": {k: roos.get(k) for k in ["ann", "dd", "sharpe", "calmar", "ret", "days", "d0", "d1"]},
        "full": {k: full.get(k) for k in ["ann", "dd", "sharpe", "calmar", "ret", "days", "d0", "d1"]},
        "fae_ann": fae,
        "collapse": collapse,
        "pass_fae": fae >= 0.5,
        "pass_oos_profit": roos["ret"] > 0,
        "pass_oos_dd40": abs(roos["dd"]) <= 0.40,
        "pass_full_dd40": abs(full["dd"]) <= 0.40,
    }


def walk_forward(
    data: dict,
    p: dict,
    sd: list[str],
    comm: float,
    is_years: int = 3,
    oos_years: int = 1,
    step_years: int = 1,
) -> dict:
    """滚动前向: 固定参数 (规则策略无每窗重优化).

    窗口按日历年对齐. 例 is=3, oos=1, step=1:
      [2013-2015 IS] -> [2016 OOS], [2014-2016 IS] -> [2017 OOS], ...
    """
    years = sorted({year_of(d) for d in sd})
    if len(years) < is_years + oos_years:
        return {"ok": False, "reason": "not_enough_years", "windows": []}

    windows = []
    i = 0
    while i + is_years + oos_years - 1 < len(years):
        is_ys = years[i : i + is_years]
        oos_ys = years[i + is_years : i + is_years + oos_years]
        is_d0, is_d1 = f"{is_ys[0]}-01-01", f"{is_ys[-1]}-12-31"
        oos_d0, oos_d1 = f"{oos_ys[0]}-01-01", f"{oos_ys[-1]}-12-31"
        # clip to available
        is_d0 = max(is_d0, sd[0])
        oos_d1 = min(oos_d1, sd[-1])
        ris = eval_window(data, p, is_d0, is_d1, comm)
        roos = eval_window(data, p, oos_d0, oos_d1, comm)
        if ris and roos:
            if ris["ann"] > 1e-6:
                wfe = clip_wfe(roos["ann"] / ris["ann"])
            elif roos["ann"] >= 0:
                wfe = 1.0
            else:
                wfe = 0.0
            windows.append(
                {
                    "is_years": is_ys,
                    "oos_years": oos_ys,
                    "is": {k: ris.get(k) for k in ["ann", "dd", "sharpe", "ret", "days", "d0", "d1"]},
                    "oos": {k: roos.get(k) for k in ["ann", "dd", "sharpe", "ret", "days", "d0", "d1"]},
                    "wfe": wfe,
                    "oos_profit": roos["ret"] > 0,
                }
            )
        i += step_years

    if not windows:
        return {"ok": False, "reason": "no_windows", "windows": []}

    oos_rets = [w["oos"]["ret"] for w in windows]
    oos_anns = [w["oos"]["ann"] for w in windows]
    is_anns = [w["is"]["ann"] for w in windows]
    wfes = [w["wfe"] for w in windows]
    # 总体前向盈利: 复利拼接近似 (1+r1)*(1+r2)*...-1
    total = 1.0
    for r in oos_rets:
        total *= 1 + r
    total_ret = total - 1
    # 利润集中度: 只对正利润窗口
    pos = [r for r in oos_rets if r > 0]
    sum_pos = sum(pos) if pos else 0.0
    max_share = (max(pos) / sum_pos) if sum_pos > 1e-12 else 0.0
    # 若有负窗口, 用 |正利润| 份额; 若全负则 max_share=1 视为集中失败
    if not pos:
        max_share = 1.0
    pct_win = sum(1 for w in windows if w["oos_profit"]) / len(windows)
    mean_wfe = statistics.mean(wfes)
    # 前向段最大回撤: 取各 OOS 窗最大回撤的最差, 以及全样本
    worst_oos_dd = min(w["oos"]["dd"] for w in windows)
    full = eval_window(data, p, sd[0], sd[-1], comm)
    full_dd = full["dd"] if full else -1.0

    checks = {
        "总体盈利": total_ret > 0,
        "WFE>=50%": mean_wfe >= 0.5,
        ">=50%窗口盈利": pct_win >= 0.5,
        "无单窗>50%利润": max_share <= 0.5,
        "回撤<=40%": abs(full_dd) <= 0.40 and abs(worst_oos_dd) <= 0.40,
    }
    return {
        "ok": True,
        "n_windows": len(windows),
        "windows": windows,
        "total_oos_ret": total_ret,
        "mean_oos_ann": statistics.mean(oos_anns),
        "mean_is_ann": statistics.mean(is_anns),
        "mean_wfe": mean_wfe,
        "pct_profitable_windows": pct_win,
        "max_window_profit_share": max_share,
        "worst_oos_dd": worst_oos_dd,
        "full_dd": full_dd,
        "checks": checks,
        "pass_all": all(checks.values()),
        "pass_n": sum(checks.values()),
    }


def multi_regime(data: dict, p: dict, bench: str, sd: list[str], comm: float) -> dict:
    years = sorted({year_of(d) for d in sd})
    rows = []
    for y in years:
        by = bench_year_ret(data, bench, y)
        ry = eval_window(data, p, f"{y}-01-01", f"{y}-12-31", comm)
        if by is None or not ry:
            continue
        rg = regime_of_bench(by)
        rows.append(
            {
                "y": y,
                "rg": rg,
                "bench": by,
                "ret": ry["ret"],
                "ann": ry["ann"],
                "dd": ry["dd"],
                "sharpe": ry.get("sharpe", 0),
                "ex": ry["ret"] - by,
            }
        )
    if not rows:
        return {"ok": False}
    by_rg: dict[str, list] = {"bull": [], "bear": [], "chop": []}
    for r in rows:
        by_rg[r["rg"]].append(r)

    def agg(xs: list[dict]) -> dict:
        if not xs:
            return {"n": 0, "mean_ret": 0, "mean_ann": 0, "mean_ex": 0, "worst_dd": 0, "pct_pos": 0}
        return {
            "n": len(xs),
            "mean_ret": statistics.mean([x["ret"] for x in xs]),
            "mean_ann": statistics.mean([x["ann"] for x in xs]),
            "mean_ex": statistics.mean([x["ex"] for x in xs]),
            "worst_dd": min(x["dd"] for x in xs),
            "pct_pos": sum(1 for x in xs if x["ret"] > 0) / len(xs),
        }

    # 单一年份利润占比 (用正收益年)
    pos = [x for x in rows if x["ret"] > 0]
    sum_pos = sum(x["ret"] for x in pos) if pos else 0.0
    max_year_share = (max(x["ret"] for x in pos) / sum_pos) if sum_pos > 1e-12 else 1.0
    # 单一状态利润占比
    state_pos = {}
    for rg, xs in by_rg.items():
        state_pos[rg] = sum(max(0.0, x["ret"]) for x in xs)
    sp_sum = sum(state_pos.values())
    max_state_share = (max(state_pos.values()) / sp_sum) if sp_sum > 1e-12 else 1.0

    checks = {
        "非单一年份>50%利润": max_year_share <= 0.5,
        "非单一状态>70%利润": max_state_share <= 0.70,
        "熊市可活_dd<=40%": abs(agg(by_rg["bear"])["worst_dd"]) <= 0.40 if by_rg["bear"] else True,
        "至少两状态有正均收益": sum(1 for rg in ("bull", "bear", "chop") if by_rg[rg] and agg(by_rg[rg])["mean_ret"] > 0) >= 2,
    }
    return {
        "ok": True,
        "years": rows,
        "by_regime": {rg: agg(xs) for rg, xs in by_rg.items()},
        "max_year_profit_share": max_year_share,
        "max_state_profit_share": max_state_share,
        "checks": checks,
        "pass_all": all(checks.values()),
        "pass_n": sum(checks.values()),
    }


def grade(oos: dict, wf: dict, reg: dict) -> dict:
    checks = {}
    if oos.get("ok"):
        checks["OOS_FAE>=50%"] = oos["pass_fae"]
        checks["OOS盈利"] = oos["pass_oos_profit"]
        checks["OOS回撤<=40%"] = oos["pass_oos_dd40"]
        checks["全样本回撤<=40%"] = oos["pass_full_dd40"]
        checks["OOS未塌缩"] = not oos["collapse"]
    else:
        for k in ["OOS_FAE>=50%", "OOS盈利", "OOS回撤<=40%", "全样本回撤<=40%", "OOS未塌缩"]:
            checks[k] = False
    if wf.get("ok"):
        for k, v in wf["checks"].items():
            checks[f"WF_{k}"] = v
    else:
        for k in ["总体盈利", "WFE>=50%", ">=50%窗口盈利", "无单窗>50%利润", "回撤<=40%"]:
            checks[f"WF_{k}"] = False
    if reg.get("ok"):
        for k, v in reg["checks"].items():
            checks[f"RG_{k}"] = v
    else:
        for k in ["非单一年份>50%利润", "非单一状态>70%利润", "熊市可活_dd<=40%", "至少两状态有正均收益"]:
            checks[f"RG_{k}"] = False

    pass_n = sum(checks.values())
    total = len(checks)
    # 硬不合格: WF 五项全过才算前向合格; 这里同时给总览
    hard_fail = []
    if oos.get("ok") and oos["collapse"]:
        hard_fail.append("样本外塌缩")
    if wf.get("ok") and not wf["pass_all"]:
        hard_fail.append("前向分析未全过")
    if oos.get("ok") and not oos["pass_fae"]:
        hard_fail.append("FAE<50%")
    if oos.get("ok") and abs(oos["full"]["dd"]) > 0.40:
        hard_fail.append("全样本回撤>40%")
    status = "不合格" if hard_fail else ("观察" if pass_n < total else "合格")
    return {
        "checks": checks,
        "pass_n": pass_n,
        "total": total,
        "hard_fail": hard_fail,
        "status": status,
    }


def grade_etf_soft(full: dict | None, oos: dict) -> dict:
    """ETF 短样本宽松档: 不套 10 年前向/多状态.

    合格 (全过且无 hard):
      - FULL 盈利, dd<=40%, sharpe>=0.8
      - OOS 可用时: OOS 收益 > -15%, 且非 (OOS 收益<0 且夏普<0.3)
    说明: 不用全样本 collapse (IS 年化极高时 OOS 仍大赚也会被误杀).
    """
    checks = {}
    if not full:
        checks = {
            "FULL有数据": False,
            "FULL盈利": False,
            "FULL回撤<=40%": False,
            "FULL夏普>=0.8": False,
            "OOS可接受": False,
        }
        return {
            "checks": checks,
            "pass_n": 0,
            "total": 5,
            "hard_fail": ["无FULL"],
            "status": "不合格",
            "mode": "etf_soft",
        }
    checks["FULL有数据"] = True
    checks["FULL盈利"] = float(full.get("ret") or 0) > 0
    checks["FULL回撤<=40%"] = abs(float(full.get("dd") or 1)) <= 0.40
    checks["FULL夏普>=0.8"] = float(full.get("sharpe") or 0) >= 0.8
    if oos.get("ok"):
        oos_ret = float((oos.get("oos") or {}).get("ret") or 0)
        oos_sh = float((oos.get("oos") or {}).get("sharpe") or 0)
        # 宽松: OOS 大亏或明显失效才否
        oos_ok = (oos_ret > -0.15) and not (oos_ret < 0 and oos_sh < 0.3)
        checks["OOS可接受"] = oos_ok
    else:
        checks["OOS可接受"] = False
    pass_n = sum(checks.values())
    total = len(checks)
    hard_fail = []
    if not checks["FULL盈利"]:
        hard_fail.append("FULL不盈利")
    if not checks["FULL回撤<=40%"]:
        hard_fail.append("FULL回撤>40%")
    if not checks["FULL夏普>=0.8"]:
        hard_fail.append("FULL夏普低")
    if not checks["OOS可接受"]:
        hard_fail.append("OOS不可接受")
    if hard_fail:
        status = "不合格"
    elif pass_n == total:
        status = "合格"
    else:
        status = "观察"
    return {
        "checks": checks,
        "pass_n": pass_n,
        "total": total,
        "hard_fail": hard_fail,
        "status": status,
        "mode": "etf_soft",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="防过拟合验收: OOS + Walk-Forward + 多状态")
    ap.add_argument("--pool", default="pool_long_proxy")
    ap.add_argument("--count", type=int, default=3200)
    ap.add_argument("--adjust", default="none")
    ap.add_argument(
        "--strategies",
        default="c01,c01_q10,c01_q10_vt11,c01_q10_vt13,c01_q10_vt15,c01_q10_rg_flip",
    )
    ap.add_argument("--is-years", type=int, default=3)
    ap.add_argument("--oos-years", type=int, default=1)
    ap.add_argument("--step-years", type=int, default=1)
    ap.add_argument("--comm", type=float, default=0.0003)
    ap.add_argument("--out", default="output/risk_audit/robust_validation.json")
    ap.add_argument("--gate", action="store_true", help="启用退出码门禁")
    ap.add_argument(
        "--require-pass",
        action="append",
        default=[],
        help="要求该策略/id 必须合格; 可重复",
    )
    ap.add_argument(
        "--from-shortlist",
        default=None,
        help="shortlist.json 路径; 对 base+overrides 评测, key 用 id",
    )
    ap.add_argument(
        "--preset",
        choices=["", "long_proxy", "etf_core", "etf_soft"],
        default="",
        help="long_proxy / etf_core(全套硬门禁) / etf_soft(短样本宽松)",
    )
    ap.add_argument(
        "--grade-mode",
        choices=["full", "etf_soft"],
        default="full",
        help="full=14项硬验收; etf_soft=短样本宽松",
    )
    args = ap.parse_args()

    if args.preset == "long_proxy":
        args.pool = "pool_long_proxy"
        args.count = 3200
        args.comm = 0.0003
        args.grade_mode = "full"
        if args.strategies == ap.get_default("strategies"):
            args.strategies = "c01,c01_q10,c01_q10_vt11,c01_q10_vt13,c01_q10_vt15"
    elif args.preset == "etf_core":
        args.pool = "pool"
        args.count = 640
        args.comm = 0.00005
        args.grade_mode = "full"
        if not args.from_shortlist and args.strategies == ap.get_default("strategies"):
            args.strategies = "c01,c01_q10,c01_q10_vt11,c01_q10_vt13"
    elif args.preset == "etf_soft":
        args.pool = "pool"
        args.count = 640
        args.comm = 0.00005
        args.grade_mode = "etf_soft"
        if not args.from_shortlist and args.strategies == ap.get_default("strategies"):
            args.strategies = "c01,c01_q10,c01_q10_vt11,c01_q10_vt09,c01_q10_vt11_rb8,c01_q10_vt11_rb8_st5"

    print("=" * 78)
    print("防过拟合验收 · 样本外 / 前向分析 / 多状态")
    print("=" * 78)

    # 构建待评测列表: (key, params_dict|None, strategy_file_name|None)
    jobs: list[tuple[str, dict | None, str | None]] = []
    extra_codes: list[str] = []
    if args.from_shortlist:
        from etf_rotation.search_data import materialize_params

        short = json.loads(Path(args.from_shortlist).read_text(encoding="utf-8"))
        for item in short:
            cid = item["id"]
            ovr = item.get("overrides") or {}
            p = materialize_params(item["base"], ovr)
            jobs.append((cid, p, None))
            for key in ("park_assets", "extra_universe"):
                for code in ovr.get(key) or []:
                    if code:
                        extra_codes.append(str(code))
        extra_codes = list(dict.fromkeys(extra_codes))
    else:
        names = [x.strip() for x in args.strategies.split(",") if x.strip()]
        for name in names:
            jobs.append((name, None, name))

    data, bench = load_data(args.pool, args.count, args.adjust, extra_codes=extra_codes)
    sd = common_dates(data)
    print(f"pool={args.pool} n={len(data)} {sd[0]}~{sd[-1]} days={len(sd)} bench={bench}")
    if extra_codes:
        print(f"extra_codes={extra_codes}")
    print(f"WF: IS={args.is_years}y OOS={args.oos_years}y step={args.step_years}y comm={args.comm}")
    if args.from_shortlist:
        print(f"from_shortlist={args.from_shortlist}")
    print()

    report: dict = {
        "pool": args.pool,
        "range": [sd[0], sd[-1]],
        "bench": bench,
        "strategies": {},
        "extra_codes": extra_codes,
    }
    if not str(args.pool).startswith("pool_long_proxy"):
        report["sample_note"] = "etf_short_history"
        report["warning"] = "前向窗口数可能不足; 仅作迁移稳健性, 不作 10 年结论"

    for key, p_override, strat_name in jobs:
        print("-" * 78)
        print(f"策略: {key}")
        try:
            if p_override is not None:
                p = p_override
            else:
                p = cfgmod.strategy_for_backtest(cfgmod.load_strategy(strat_name or key))
        except Exception as e:
            print(f"  SKIP load error: {e}")
            continue
        p["ps"] = p.get("ps", 0.95)
        full = eval_window(data, p, sd[0], sd[-1], args.comm)
        if full:
            print(format_result(full, "FULL"))
        oos = oos_split(data, p, sd, args.comm)
        if getattr(args, "grade_mode", "full") == "etf_soft":
            wf = {"ok": False, "reason": "skipped_etf_soft"}
            reg = {"ok": False, "reason": "skipped_etf_soft"}
            g = grade_etf_soft(full, oos)
        else:
            wf = walk_forward(
                data,
                p,
                sd,
                args.comm,
                is_years=args.is_years,
                oos_years=args.oos_years,
                step_years=args.step_years,
            )
            reg = multi_regime(data, p, bench, sd, args.comm)
            g = grade(oos, wf, reg)

        if oos.get("ok"):
            print(
                f"  OOS  IS ann={oos['is']['ann']:+.1f}% sh={oos['is']['sharpe']:.2f} dd={oos['is']['dd']*100:.1f}%  |  "
                f"OOS ann={oos['oos']['ann']:+.1f}% sh={oos['oos']['sharpe']:.2f} dd={oos['oos']['dd']*100:.1f}%  "
                f"FAE={oos['fae_ann']*100:.0f}% collapse={oos['collapse']}"
            )
        if wf.get("ok"):
            print(
                f"  WF   windows={wf['n_windows']} total_oos_ret={wf['total_oos_ret']*100:+.1f}% "
                f"mean_wfe={wf['mean_wfe']*100:.0f}% win%={wf['pct_profitable_windows']*100:.0f}% "
                f"max_share={wf['max_window_profit_share']*100:.0f}% worst_oos_dd={wf['worst_oos_dd']*100:.1f}%"
            )
            print(f"       checks={wf['checks']}")
            for w in wf["windows"]:
                print(
                    f"       {w['is_years'][0]}-{w['is_years'][-1]}→{w['oos_years'][0]}: "
                    f"IS ann={w['is']['ann']:+5.1f}%  OOS ann={w['oos']['ann']:+5.1f}% ret={w['oos']['ret']*100:+5.1f}% "
                    f"dd={w['oos']['dd']*100:5.1f}% wfe={w['wfe']*100:5.0f}% {'✓' if w['oos_profit'] else '×'}"
                )
        if reg.get("ok"):
            br = reg["by_regime"]
            print(
                f"  REG  bull n={br['bull']['n']} mean_ret={br['bull']['mean_ret']*100:+.1f}%  "
                f"bear n={br['bear']['n']} mean_ret={br['bear']['mean_ret']*100:+.1f}% worst_dd={br['bear']['worst_dd']*100:.1f}%  "
                f"chop n={br['chop']['n']} mean_ret={br['chop']['mean_ret']*100:+.1f}%"
            )
            print(
                f"       max_year_share={reg['max_year_profit_share']*100:.0f}% "
                f"max_state_share={reg['max_state_profit_share']*100:.0f}% checks={reg['checks']}"
            )
        print(f"  => {g['status']}  {g['pass_n']}/{g['total']}  hard_fail={g['hard_fail']}")
        report["strategies"][key] = {
            "full": {k: full.get(k) for k in ["ann", "dd", "sharpe", "calmar", "ret"]} if full else None,
            "oos": oos,
            "walk_forward": (
                {
                    **{k: wf[k] for k in wf if k != "windows"},
                    "windows": wf.get("windows", []),
                }
                if wf.get("ok")
                else wf
            ),
            "regime": (
                {
                    **{k: reg[k] for k in reg if k != "years"},
                    "years": reg.get("years", []),
                }
                if reg.get("ok")
                else reg
            ),
            "grade": g,
        }

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    pass_ids, watch_ids, fail_ids = [], [], []
    for name, rec in report["strategies"].items():
        g = rec["grade"]
        full = rec.get("full") or {}
        st = g["status"]
        if st == "合格":
            pass_ids.append(name)
        elif st == "观察":
            watch_ids.append(name)
        else:
            fail_ids.append(name)
        print(
            f"  {name:28s} {g['status']:4s} {g['pass_n']:2d}/{g['total']:<2d}  "
            f"ann={full.get('ann', 0):+.1f}% dd={float(full.get('dd') or 0)*100:.1f}% sh={float(full.get('sharpe') or 0):.2f}  "
            f"fail={g['hard_fail']}"
        )
    print(f"\nWROTE {out}")

    # 门禁退出码
    exit_code = 0
    if args.gate:
        if fail_ids:
            exit_code = 2
        elif not pass_ids:
            exit_code = 1
        else:
            exit_code = 0
        for req in args.require_pass or []:
            # 允许策略名或 shortlist id; 也允许 anchor 名
            ok = False
            for name, rec in report["strategies"].items():
                if name == req or name.endswith(req) or req in name:
                    if rec["grade"]["status"] == "合格":
                        ok = True
                        break
            # 也检查 base 文件名直接加载
            if not ok and req in report["strategies"] and report["strategies"][req]["grade"]["status"] == "合格":
                ok = True
            if not ok:
                # 精确匹配优先
                rec = report["strategies"].get(req)
                if not rec or rec["grade"]["status"] != "合格":
                    exit_code = 2
                    print(f"REQUIRE-PASS fail: {req}")
        print(
            f"GATE exit={exit_code} pass={pass_ids} watch={watch_ids} fail={fail_ids}"
        )
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
