"""交易日计数 — 生产与回测对齐.

优先用行情日历 (bench/池交集日期); 无日历时退回工作日近似 (周一~五).
"""
from __future__ import annotations

from datetime import datetime, timedelta


def trading_days_since(
    date_str: str | None,
    now: datetime,
    calendar: list[str] | None = None,
) -> int:
    """自 date_str 之后到 now(含当日若在日历内) 的交易日数.

    语义对齐回测: rebalance 当日 lrb=dc, 需再过 rb 个交易日才 can_rb
    → 返回「已经过了多少个交易日」(不含调仓当日本身).
    """
    if not date_str:
        return 999
    end = now.strftime("%Y-%m-%d") if isinstance(now, datetime) else str(now)
    if end < date_str:
        return 0
    if calendar:
        # 严格: 调仓日之后的交易日个数
        return sum(1 for d in calendar if date_str < d <= end)
    # fallback: 工作日
    try:
        d0 = datetime.strptime(date_str, "%Y-%m-%d").date()
        d1 = now.date() if isinstance(now, datetime) else datetime.strptime(end, "%Y-%m-%d").date()
    except Exception:
        return 999
    if d1 <= d0:
        return 0
    n = 0
    cur = d0 + timedelta(days=1)
    while cur <= d1:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def merge_calendar(*date_lists: list[str]) -> list[str]:
    """合并多条日期序列为有序去重交易日列表."""
    s: set[str] = set()
    for dl in date_lists:
        if dl:
            s.update(dl)
    return sorted(s)
