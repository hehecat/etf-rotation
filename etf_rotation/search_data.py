"""并行搜索 / 验收共用的池数据加载."""
from __future__ import annotations

from typing import Any

from . import config as cfgmod
from . import data as data_mod


def load_pool_data(
    pool_name: str = "pool_long_proxy",
    count: int = 3200,
    adjust: str = "none",
    min_bars: int = 80,
    max_workers: int = 6,
    extra_codes: list[str] | None = None,
) -> tuple[dict[str, dict], str, list[str]]:
    """加载并清洗池数据.

    返回 (data, bench, common_dates).
    坏点前向填充; 非基准缺失过多则剔除.
    extra_codes: park/extra_universe 等池外代码, 一并拉取.
    """
    pool_cfg = cfgmod.load_pool(pool_name)
    bench = pool_cfg.get("bench", "SH510300")
    pool = cfgmod.pool_as_dict(pool_cfg)
    extras = [c for c in (extra_codes or []) if c]
    # 常见别名 (生产池黄金代码在长代理中可能不在 etfs 列表)
    name_hints = {
        "SZ159934": "黄金ETF",
        "SH518880": "黄金ETF华安",
        "SH513100": "纳指ETF",
        "SH513500": "标普500ETF",
        "SH511010": "国债ETF",
        "SH511880": "银华日利",
    }
    codes = list(dict.fromkeys([bench] + list(pool) + extras))
    raw = data_mod.fetch_many(
        codes,
        count=count,
        adjust=adjust,
        min_bars=min_bars,
        max_workers=max_workers,
        use_disk=True,
    )
    data: dict[str, dict] = {}
    for c, bars in raw.items():
        closes = list(bars["close"])
        opens = list(bars.get("open") or closes)
        last = None
        bad = 0
        for i, px in enumerate(closes):
            if px is None or px <= 0:
                bad += 1
                if last is not None:
                    closes[i] = last
                    opens[i] = last
            else:
                last = px
        if c != bench and bad / max(len(closes), 1) > 0.05:
            continue
        data[c] = {
            **bars,
            "close": closes,
            "open": opens,
            "name": pool.get(c) or name_hints.get(c, c),
        }
    if bench not in data:
        kl = data_mod.fetch_klines(bench, count=count, adjust=adjust)
        bb = data_mod.normalize_bars(kl, min_bars=min_bars)
        if bb:
            data[bench] = {**bb, "name": "沪深300ETF"}
    if not data:
        raise RuntimeError(f"池 {pool_name} 无有效数据")
    # 公共日期仅按池内核心代码 (bench+原池), 避免 park 短历史压缩全样本
    core = [c for c in data if c == bench or c in pool]
    if not core:
        core = list(data.keys())
    sd = sorted(set.intersection(*[set(data[c]["dates"]) for c in core]))
    return data, bench, sd


def materialize_params(base: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """base 策略 JSON + overrides → backtest 参数 dict."""
    strat = cfgmod.load_strategy(base)
    p = cfgmod.strategy_for_backtest(strat)
    if overrides:
        p.update(overrides)
    if "ps" not in p or p.get("ps") is None:
        p["ps"] = 0.95
    return p
