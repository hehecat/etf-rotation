"""行情取数 (stock-api CLI), 多源回退 + 本地磁盘缓存.

缓存位置 (优先级):
  1) 环境变量 ETF_KLINE_CACHE
  2) $HOME/.cache/etf-rotation/klines
  3) <repo>/output/klines_cache

文件: {code}_{period}_{adjust}.json
命中: 本地根数 >= 请求 count 且未强制刷新 → 截取末尾返回.
qfq 全源失败时自动回退 none, 并写入对应 adjust 文件.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

_cache: dict[tuple, list] = {}
SOURCES = ("auto", "tencent", "eastmoney", "sina")
DEFAULT_MAX_AGE_SEC = int(os.environ.get("ETF_KLINE_MAX_AGE", str(36 * 3600)))


def _cache_root() -> Path:
    env = os.environ.get("ETF_KLINE_CACHE")
    if env:
        p = Path(env)
    else:
        home_cache = Path.home() / ".cache" / "etf-rotation" / "klines"
        try:
            home_cache.mkdir(parents=True, exist_ok=True)
            p = home_cache
        except Exception:
            p = Path(__file__).resolve().parent.parent / "output" / "klines_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _disk_path(code: str, period: str, adjust: str) -> Path:
    safe = f"{code}_{period}_{adjust}".replace("/", "_")
    return _cache_root() / f"{safe}.json"


def _read_disk(code: str, period: str, adjust: str) -> dict[str, Any] | None:
    path = _disk_path(code, period, adjust)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict) or not isinstance(obj.get("klines"), list):
            return None
        return obj
    except Exception:
        return None


def _write_disk(
    code: str,
    period: str,
    adjust: str,
    klines: list[dict[str, Any]],
    source: str,
) -> None:
    if not klines:
        return
    path = _disk_path(code, period, adjust)
    payload = {
        "code": code,
        "period": period,
        "adjust": adjust,
        "source": source,
        "updated": int(time.time()),
        "n": len(klines),
        "d0": klines[0].get("date"),
        "d1": klines[-1].get("date"),
        "klines": klines,
    }
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def cache_info() -> dict[str, Any]:
    root = _cache_root()
    files = list(root.glob("*.json"))
    return {
        "dir": str(root),
        "files": len(files),
        "bytes": sum(f.stat().st_size for f in files if f.is_file()),
    }


def clear_disk_cache(code: str | None = None) -> int:
    root = _cache_root()
    n = 0
    if code:
        for p in root.glob(f"{code}_*.json"):
            p.unlink(missing_ok=True)
            n += 1
    else:
        for p in root.glob("*.json"):
            p.unlink(missing_ok=True)
            n += 1
    return n


def _network_fetch(
    code: str,
    period: str,
    count: int,
    adjust: str,
    source: str,
    timeout: int,
) -> list[dict[str, Any]]:
    try:
        cmd = [
            "npx", "-y", "stock-api", "get-klines", code,
            "--period", period,
            "--count", str(count),
            "--adjust", adjust,
            "--source", source,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not r.stdout.strip():
            return []
        parsed = json.loads(r.stdout)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def fetch_klines(
    code: str,
    period: str = "day",
    count: int = 60,
    adjust: str = "none",
    timeout: int = 35,
    use_cache: bool = True,
    source: str | None = None,
    force_refresh: bool = False,
    use_disk: bool = True,
    max_age_sec: int | None = None,
) -> list[dict[str, Any]]:
    """取 K 线. 优先内存 → 磁盘 → 网络(qfq 失败回退 none) → 过期磁盘."""
    age_limit = DEFAULT_MAX_AGE_SEC if max_age_sec is None else max_age_sec
    sources = [source] if source else list(SOURCES)
    adjust_try = [adjust]
    if adjust == "qfq":
        adjust_try.append("none")

    # 1) 内存
    if use_cache and not force_refresh:
        for adj in adjust_try:
            for src in sources + ["disk"]:
                key = (code, period, count, adj, src)
                if key in _cache and _cache[key]:
                    return _cache[key]
            for (c, per, cnt, adj, src), data in list(_cache.items()):
                if (
                    c == code and per == period and adj in adjust_try
                    and data and len(data) >= count
                ):
                    return data[-count:]

    # 2) 磁盘
    disk_hit: dict[str, Any] | None = None
    disk_adj = adjust
    if use_disk and not force_refresh:
        for adj in adjust_try:
            obj = _read_disk(code, period, adj)
            if not obj or not obj.get("klines"):
                continue
            kl = obj["klines"]
            updated = int(obj.get("updated") or 0)
            fresh = (time.time() - updated) <= age_limit if age_limit > 0 else True
            if len(kl) >= count and fresh:
                out = kl[-count:]
                if use_cache:
                    _cache[(code, period, count, adj, obj.get("source") or "disk")] = out
                return out
            # 记下最长磁盘备胎
            if disk_hit is None or len(kl) > len(disk_hit.get("klines") or []):
                disk_hit = obj
                disk_adj = adj

    # 3) 网络
    need = count
    if disk_hit and disk_hit.get("klines"):
        need = max(count, len(disk_hit["klines"]))

    last: list[dict[str, Any]] = []
    got_src = "auto"
    got_adj = adjust
    for adj in adjust_try:
        for src in sources:
            mem_key = (code, period, need, adj, src)
            data: list[dict[str, Any]] = []
            if use_cache and not force_refresh and mem_key in _cache and _cache[mem_key]:
                data = _cache[mem_key]
            else:
                data = _network_fetch(code, period, need, adj, src, timeout)
                if use_cache:
                    _cache[mem_key] = data
                    if data:
                        _cache[(code, period, count, adj, src)] = data[-count:]
            if data:
                last = data
                got_src = src
                got_adj = adj
                break
        if last:
            break

    if last:
        # 保留更长旧缓存
        if (
            use_disk
            and disk_hit
            and disk_hit.get("klines")
            and len(disk_hit["klines"]) > len(last)
            and disk_hit["klines"][-1].get("date") >= last[-1].get("date")
        ):
            last = disk_hit["klines"]
            got_adj = disk_adj
        elif use_disk:
            _write_disk(code, period, got_adj, last, got_src)
            # 若用户要 qfq 但实际 none, 也写一份 qfq 别名方便下次命中请求
            if adjust == "qfq" and got_adj == "none":
                _write_disk(code, period, "qfq", last, got_src + "|fallback_none")
        out = last[-count:] if len(last) > count else last
        if use_cache:
            _cache[(code, period, count, adjust, got_src)] = out
        return out

    # 4) 网络失败回退磁盘
    if disk_hit and disk_hit.get("klines"):
        kl = disk_hit["klines"]
        out = kl[-count:] if len(kl) >= count else kl
        if use_cache:
            _cache[(code, period, count, disk_adj, "disk")] = out
        return out
    return []


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
    force_refresh: bool = False,
    use_disk: bool = True,
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    global SOURCES
    old = SOURCES
    if prefer_sources:
        SOURCES = prefer_sources

    def one(code: str):
        return code, fetch_klines(
            code,
            period=period,
            count=count,
            adjust=adjust,
            force_refresh=force_refresh,
            use_disk=use_disk,
        )

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
    force_refresh: bool = False,
) -> dict[str, list] | None:
    for src in ("tencent", "eastmoney", "sina", "auto"):
        klines = fetch_klines(
            code, count=count, source=src, use_cache=True, force_refresh=force_refresh
        )
        bars = normalize_bars(klines, min_bars=min_bars)
        if bars:
            return bars
    return None


def attach_names(data: dict[str, dict], name_map: dict[str, str]) -> dict[str, dict]:
    for code, d in data.items():
        d["name"] = name_map.get(code, code)
    return data
