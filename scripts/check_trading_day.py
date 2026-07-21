#!/usr/bin/env python3
"""检查某日是否 A 股交易日.

用法:
  python3 scripts/check_trading_day.py
  python3 scripts/check_trading_day.py --date 2026-10-01
  python3 scripts/check_trading_day.py --date 2026-02-14
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.calendar_util import (  # noqa: E402
    is_cn_session_day,
    is_trading_day,
    load_cn_holidays,
    resolve_trading_day,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="A股交易日检查")
    ap.add_argument("--date", default="", help="YYYY-MM-DD, 默认今天")
    ap.add_argument("--bars", type=int, default=120)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    day = args.date or None
    info = resolve_trading_day(day, bars=args.bars)
    hol = load_cn_holidays()
    day_s = info["date"]
    out = {
        **info,
        "cn_session_only": is_cn_session_day(day_s),
        "weekday_only": is_trading_day(day_s, None, use_holidays=False),
        "closed_table_size": len(hol["closed"]),
        "makeup_table_size": len(hol["makeup"]),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"date={out['date']} is_trading_day={out['is_trading_day']} source={out['source']}")
        print(
            f"  cn_session={out['cn_session_only']} weekday_only={out['weekday_only']} "
            f"closed={out['in_closed_table']} makeup={out['in_makeup_table']}"
        )
        print(
            f"  bench_n={out['bench_n']} last={out['bench_last']} "
            f"table closed={out['closed_table_size']} makeup={out['makeup_table_size']}"
        )
    raise SystemExit(0 if out["is_trading_day"] else 2)


if __name__ == "__main__":
    main()
