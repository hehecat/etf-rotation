"""行情取数 (stock-api CLI)."""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
from typing import Any

# 进程内缓存: (code, period, count, adjust) -> klines
_cache: dict[tuple, list] = {}


def fetch_klines(
    code: str,
    period: str = "day",
    count: int = 60,
    adjust: str = "none",
    timeout: int = 30,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    key = (code, period, count, adjust)
    if use_cache and key in _cache:
        return _cache[key]
    try:
        cmd = [
            "npx", "-y", "stock-api", "get-klines", code,
            "--period", period,
            "--count", str(count),
            "--adjust", adjust,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not r.stdout.strip():
            data = []
        else:
            data = json.loads(r.stdout)
            if not isinstance(data, list):
                data = []
    except Exception:
        data = []
    if use_cache:
        _cache[key] = data
    return data


def clear_cache():
    _cache.clear()


def normalize_bars(klines: list[dict]) -> dict[str, list] | None:
    if not klines or len(klines) < 22:
        return None
    return {
        "dates": [k["date"] for k in klines],
        "close": [float(k["close"]) for k in klines],
        "volume": [float(k.get("volume", 0)) for k in klines],
    }


def fetch_many(
    codes: list[str],
    period: str = "day",
    count: int = 60,
    adjust: str = "none",
    max_workers: int = 8,
    min_bars: int = 22,
) -> dict[str, dict]:
    """并行取数. 返回 {code: {dates, close, volume}}."""
    out: dict[str, dict] = {}

    def one(code: str):
        return code, fetch_klines(code, period=period, count=count, adjust=adjust)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(one, c): c for c in codes}
        for fut in concurrent.futures.as_completed(futs):
            code, klines = fut.result()
            bars = normalize_bars(klines)
            if bars and len(bars["close"]) >= min_bars:
                out[code] = bars
    return out


def attach_names(data: dict[str, dict], name_map: dict[str, str]) -> dict[str, dict]:
    for code, d in data.items():
        d["name"] = name_map.get(code, code)
    return data
