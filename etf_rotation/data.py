"""行情取数 (stock-api CLI), 多源回退."""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
from typing import Any

# 进程内缓存: (code, period, count, adjust, source) -> klines
_cache: dict[tuple, list] = {}

# CI/外网不稳时按序尝试
SOURCES = ("auto", "tencent", "eastmoney", "sina")


def fetch_klines(
    code: str,
    period: str = "day",
    count: int = 60,
    adjust: str = "none",
    timeout: int = 35,
    use_cache: bool = True,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """单源取数. source=None 时按 SOURCES 回退直到成功."""
    sources = [source] if source else list(SOURCES)
    last: list = []
    for src in sources:
        key = (code, period, count, adjust, src)
        if use_cache and key in _cache:
            data = _cache[key]
            if data:
                return data
            last = data
            continue
        try:
            cmd = [
                "npx", "-y", "stock-api", "get-klines", code,
                "--period", period,
                "--count", str(count),
                "--adjust", adjust,
                "--source", src,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            data = []
            if r.stdout.strip():
                parsed = json.loads(r.stdout)
                if isinstance(parsed, list):
                    data = parsed
        except Exception:
            data = []
        if use_cache:
            _cache[key] = data
        if data:
            return data
        last = data
    return last


def clear_cache():
    _cache.clear()


def normalize_bars(klines: list[dict], min_bars: int = 22) -> dict[str, list] | None:
    if not klines or len(klines) < min_bars:
        return None
    opens = []
    for k in klines:
        o = k.get("open")
        if o is None:
            o = k["close"]
        opens.append(float(o))
    return {
        "dates": [k["date"] for k in klines],
        "open": opens,
        "close": [float(k["close"]) for k in klines],
        "volume": [float(k.get("volume", 0)) for k in klines],
    }


def fetch_many(
    codes: list[str],
    period: str = "day",
    count: int = 60,
    adjust: str = "none",
    max_workers: int = 6,
    min_bars: int = 22,
    prefer_sources: tuple[str, ...] | None = None,
) -> dict[str, dict]:
    """并行取数. 返回 {code: {dates, close, volume}}."""
    out: dict[str, dict] = {}
    # 临时覆盖源顺序
    global SOURCES
    old = SOURCES
    if prefer_sources:
        SOURCES = prefer_sources

    def one(code: str):
        return code, fetch_klines(code, period=period, count=count, adjust=adjust)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(one, c): c for c in codes}
            for fut in concurrent.futures.as_completed(futs):
                code, klines = fut.result()
                bars = normalize_bars(klines, min_bars=min_bars)
                if bars and len(bars["close"]) >= min_bars:
                    out[code] = bars
    finally:
        SOURCES = old
    return out


def fetch_bench(
    code: str,
    count: int = 60,
    min_bars: int = 22,
) -> dict[str, list] | None:
    """基准单独多源强取, 失败返回 None."""
    for src in ("tencent", "eastmoney", "sina", "auto"):
        klines = fetch_klines(code, count=count, source=src, use_cache=True)
        bars = normalize_bars(klines, min_bars=min_bars)
        if bars:
            return bars
    return None


def attach_names(data: dict[str, dict], name_map: dict[str, str]) -> dict[str, dict]:
    for code, d in data.items():
        d["name"] = name_map.get(code, code)
    return data
