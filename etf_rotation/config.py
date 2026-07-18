"""加载 JSON 配置."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .paths import CONFIG_DIR


def load_json(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_strategy(name: str = "c01") -> dict[str, Any]:
    """加载策略配置. name: c01 / c13_shadow"""
    path = CONFIG_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"策略配置不存在: {path}")
    return load_json(path)


def load_pool(name: str = "pool") -> dict[str, Any]:
    path = CONFIG_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"ETF池配置不存在: {path}")
    return load_json(path)


def pool_as_list(pool_cfg: dict | None = None) -> list[tuple[str, str]]:
    cfg = pool_cfg or load_pool()
    return [(c, n) for c, n in cfg["etfs"]]


def pool_as_dict(pool_cfg: dict | None = None) -> dict[str, str]:
    return {c: n for c, n in pool_as_list(pool_cfg)}


def strategy_for_backtest(strat: dict) -> dict:
    """将策略 JSON 转为回测引擎参数."""
    w = strat.get("weights") or {}
    return {
        "w": deepcopy(w),
        "rb": strat.get("rb_days", 5),
        "top_n": strat.get("top_n", 1),
        "hyst": strat.get("hyst", 0.2),
        "min_hold": strat.get("min_hold", 5),
        "stop": strat.get("stop", -0.08),
        "dual_ma": strat.get("dual_ma", False),
        "overheat": strat.get("overheat", 0.3),
        "lb": strat.get("lb", 20),
        "abs_m": strat.get("abs_m", False) or strat.get("require_abs_mom", False),
        "bm": strat.get("bm", 0),
        "ps": strat.get("position_pct", 0.9),
        "trail": strat.get("trail", 0),
        "inv_vol": strat.get("inv_vol", False),
        "bench": strat.get("bench", "SH510300"),
        "slip": strat.get("slip", 0.0),
        "signed_eff": bool(strat.get("signed_eff", False)),
        "fill": strat.get("fill", "next_open"),
        "empty_free_entry": bool(strat.get("empty_free_entry", True)),
        "park_bench": bool(strat.get("park_bench", False)),
        "prefer_bench_if_stronger": bool(strat.get("prefer_bench_if_stronger", False)),
    }
