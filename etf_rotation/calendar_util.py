"""交易日计数 — 生产与回测对齐.

优先级:
  1) 显式传入 calendar (行情日期列表)
  2) A股假日表 + 工作日/调休 (config/cn_holidays.json)
  3) 纯周一~五近似
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .paths import CONFIG_DIR

_HOLIDAY_FILE = CONFIG_DIR / "cn_holidays.json"


@lru_cache(maxsize=1)
def load_cn_holidays(path: str | None = None) -> dict[str, set[str]]:
    """加载 closed/makeup 集合. 返回 {closed: set, makeup: set}."""
    p = Path(path) if path else _HOLIDAY_FILE
    closed: set[str] = set()
    makeup: set[str] = set()
    if not p.exists():
        return {"closed": closed, "makeup": makeup}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"closed": closed, "makeup": makeup}
    for _y, days in (raw.get("closed") or {}).items():
        for d in days or []:
            closed.add(str(d)[:10])
    for _y, days in (raw.get("makeup") or {}).items():
        for d in days or []:
            makeup.add(str(d)[:10])
    # 补班日若误写入 closed, 以 makeup 为准开市
    closed -= makeup
    return {"closed": closed, "makeup": makeup}


def reload_cn_holidays() -> None:
    """测试/热更新假日表."""
    load_cn_holidays.cache_clear()


def _parse_day(day: str | datetime | None) -> tuple[str, int]:
    if day is None:
        now = datetime.now()
        return now.strftime("%Y-%m-%d"), now.weekday()
    if isinstance(day, datetime):
        return day.strftime("%Y-%m-%d"), day.weekday()
    day_s = str(day)[:10]
    try:
        weekday = datetime.strptime(day_s, "%Y-%m-%d").weekday()
    except Exception:
        return day_s, -1
    return day_s, weekday


def is_cn_session_day(day: str | datetime | None = None) -> bool:
    """仅假日表+工作日/调休, 不查行情."""
    day_s, weekday = _parse_day(day)
    if weekday < 0:
        return False
    hol = load_cn_holidays()
    if day_s in hol["closed"]:
        return False
    if day_s in hol["makeup"]:
        return True
    return weekday < 5


def is_trading_day(
    day: str | datetime | None = None,
    calendar: list[str] | None = None,
    *,
    use_holidays: bool = True,
) -> bool:
    """判断是否交易日.

    - 有 calendar: 必须在行情日历中 (最严)
    - 无 calendar 且 use_holidays: 假日表 + 工作日/调休
    - 否则: 周一~五
    """
    day_s, weekday = _parse_day(day)
    if weekday < 0:
        return False
    if calendar is not None:
        # 显式空列表 ≠ 未提供; 空列表表示无交易日
        return day_s in set(calendar)
    if use_holidays:
        return is_cn_session_day(day_s)
    return weekday < 5


def trading_days_since(
    date_str: str | None,
    now: datetime,
    calendar: list[str] | None = None,
    *,
    use_holidays: bool = True,
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
    if calendar is not None:
        return sum(1 for d in calendar if date_str < d <= end)
    # fallback: 假日感知工作日
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
        if is_trading_day(cur.strftime("%Y-%m-%d"), None, use_holidays=use_holidays):
            n += 1
        cur += timedelta(days=1)
    return n


def merge_calendar(*date_lists: Iterable[str] | list[str]) -> list[str]:
    """合并多条日期序列为有序去重交易日列表."""
    s: set[str] = set()
    for dl in date_lists:
        if dl:
            s.update(str(x)[:10] for x in dl)
    return sorted(s)


def load_bench_calendar(bars: int = 120, bench: str = "SH510300") -> list[str]:
    """从基准行情拉交易日列表; 失败返回空列表."""
    try:
        from . import data as data_mod

        b = data_mod.fetch_bench(bench, count=bars, min_bars=22)
        if b and b.get("dates"):
            return list(b["dates"])
    except Exception:
        pass
    return []


def resolve_trading_day(
    day: str | datetime | None = None,
    *,
    bars: int = 120,
    bench: str = "SH510300",
) -> dict:
    """统一门控: 返回 is_trading_day + 判定来源 + 行情截至.

    行情日历仅在「日期落在 [first,last]」时作为真源;
    超出范围 (未来/过旧) 回退假日表, 避免把未来正常交易日误判为休市.

    额外字段:
      data_asof: 基准最后一根 K 线日期
      data_lag: wall/判定日 > data_asof (行情尚未更新到该日)
    """
    day_s, _ = _parse_day(day)
    cal = load_bench_calendar(bars=bars, bench=bench)
    hol = load_cn_holidays()
    data_asof = cal[-1] if cal else None
    data_lag = bool(data_asof and day_s > data_asof)
    if cal:
        first, last = cal[0], cal[-1]
        if first <= day_s <= last:
            in_cal = day_s in set(cal)
            return {
                "date": day_s,
                "is_trading_day": in_cal,
                "source": "bench_calendar",
                "bench_n": len(cal),
                "bench_first": first,
                "bench_last": last,
                "data_asof": data_asof,
                "data_lag": data_lag,
                "in_closed_table": day_s in hol["closed"],
                "in_makeup_table": day_s in hol["makeup"],
            }
        ok = is_cn_session_day(day_s)
        return {
            "date": day_s,
            "is_trading_day": ok,
            "source": "cn_holidays_beyond_bench",
            "bench_n": len(cal),
            "bench_first": first,
            "bench_last": last,
            "data_asof": data_asof,
            "data_lag": data_lag,
            "in_closed_table": day_s in hol["closed"],
            "in_makeup_table": day_s in hol["makeup"],
        }
    ok = is_cn_session_day(day_s)
    return {
        "date": day_s,
        "is_trading_day": ok,
        "source": "cn_holidays_or_weekday",
        "bench_n": 0,
        "bench_first": None,
        "bench_last": None,
        "data_asof": None,
        "data_lag": False,
        "in_closed_table": day_s in hol["closed"],
        "in_makeup_table": day_s in hol["makeup"],
    }
