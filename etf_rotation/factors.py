"""因子与打分."""
from __future__ import annotations

import statistics
from typing import Any


def znorm(vals: dict[str, float]) -> dict[str, float]:
    if len(vals) < 2:
        return {k: 0.0 for k in vals}
    v = list(vals.values())
    m = statistics.mean(v)
    s = statistics.stdev(v) or 1.0
    return {k: (v[i] - m) / s for i, k in enumerate(vals.keys())}


def compute_factors(close: list[float], volume: list[float] | None = None, lb: int = 20) -> dict[str, float] | None:
    """单标的因子. close 为全历史序列, 取末尾."""
    n = len(close)
    need = max(lb, 21)
    if n < need:
        return None
    c = close
    mom20 = c[-1] / c[-21] - 1.0
    mom10 = c[-1] / c[-11] - 1.0 if n >= 11 else 0.0
    mom5 = c[-1] / c[-6] - 1.0 if n >= 6 else 0.0
    mom1 = c[-1] / c[-2] - 1.0 if n >= 2 else 0.0
    m60 = c[-1] / c[-61] - 1.0 if n >= 61 else mom20
    mlb = c[-1] / c[-(lb + 1)] - 1.0 if n > lb else mom20

    rets = [c[i] / c[i - 1] - 1.0 for i in range(n - lb, n) if c[i - 1] > 0]
    vol = statistics.stdev(rets) if len(rets) > 5 else 0.03
    sharp = mlb / vol if vol > 0 else 0.0

    net = abs(c[-1] - c[-(lb + 1)])
    tr = sum(abs(c[i] - c[i - 1]) for i in range(n - lb, n))
    eff = net / tr if tr > 0 else 0.0
    mtf = sum(1 for x in [mom5, mom10, mom20] if x > 0) / 3.0

    vt = 1.0
    if volume and len(volume) >= 20:
        v5 = sum(volume[-5:]) / 5
        v20 = sum(volume[-20:]) / 20
        vt = v5 / v20 if v20 > 0 else 1.0

    ma20 = sum(c[-20:]) / 20
    mad = c[-1] / ma20 - 1.0 if ma20 > 0 else 0.0

    return {
        "m1": mom1, "m5": mom5, "m10": mom10, "m20": mom20, "m60": m60,
        "mlb": mlb, "vol": vol, "sharp": sharp, "eff": eff, "mtf": mtf,
        "vt": vt, "mad": mad, "close": c[-1],
    }


def factors_at_index(close: list[float], volume: list[float], idx: int, lb: int = 20) -> dict[str, float] | None:
    """回测用: 在序列索引 idx 处算因子."""
    if idx < max(lb, 60) + 1:
        return None
    c = close
    f: dict[str, float] = {}
    f["m5"] = c[idx] / c[idx - 5] - 1
    f["m10"] = c[idx] / c[idx - 10] - 1
    f["m20"] = c[idx] / c[idx - 20] - 1
    f["m60"] = c[idx] / c[idx - 60] - 1
    f["mlb"] = c[idx] / c[idx - lb] - 1
    r = [c[i] / c[i - 1] - 1 for i in range(idx - lb + 1, idx + 1) if c[i - 1] > 0]
    f["vol"] = statistics.stdev(r) if len(r) > 5 else 0.03
    f["sharp"] = f["mlb"] / f["vol"] if f["vol"] > 0 else 0
    net = abs(c[idx] - c[idx - lb])
    tr = sum(abs(c[i] - c[i - 1]) for i in range(idx - lb + 1, idx + 1))
    f["eff"] = net / tr if tr > 0 else 0
    f["mtf"] = sum(1 for x in [f["m5"], f["m10"], f["m20"]] if x > 0) / 3
    v5 = sum(volume[idx - 4: idx + 1]) / 5
    v20 = sum(volume[idx - 19: idx + 1]) / 20
    f["vt"] = v5 / v20 if v20 > 0 else 1
    ma20 = sum(c[idx - 19: idx + 1]) / 20
    f["mad"] = c[idx] / ma20 - 1 if ma20 > 0 else 0
    return f


def score_cross_section(
    raw: dict[str, dict[str, float]],
    weights: dict[str, float],
    abs_mom: bool = False,
    breadth_min: float = 0,
) -> tuple[dict[str, float], float]:
    """横截面 Z 打分. 返回 (scores, breadth)."""
    filtered = {}
    for code, f in raw.items():
        if abs_mom and f.get("m20", 0) <= 0:
            continue
        filtered[code] = f
    if len(filtered) < 3:
        return {}, 0.0
    up = sum(1 for f in filtered.values() if f.get("m20", 0) > 0)
    br = up / len(filtered)
    if breadth_min > 0 and br < breadth_min:
        return {}, br
    z: dict[str, dict[str, float]] = {}
    keys = {k for f in filtered.values() for k in f}
    for fn in keys:
        if fn == "vol":
            z[fn] = znorm({c: -f[fn] for c, f in filtered.items()})
        else:
            z[fn] = znorm({c: f.get(fn, 0) for c, f in filtered.items()})
    scores = {
        c: sum(weights.get(fn, 0) * z.get(fn, {}).get(c, 0) for fn in weights)
        for c in filtered
    }
    return scores, br


def build_etf_table(
    market: dict[str, dict],
    name_map: dict[str, str],
    weights: dict[str, float],
    overheat: float = 0.3,
    max_1d_abs: float = 0.12,
    max_20d_abs: float = 0.45,
) -> tuple[dict[str, dict], list[tuple[str, str, str]]]:
    """信号用: 构建带得分的 etf_data + 剔除列表."""
    etf_data: dict[str, dict] = {}
    rejected: list[tuple[str, str, str]] = []

    for code, bars in market.items():
        name = name_map.get(code, code)
        f = compute_factors(bars["close"], bars.get("volume"))
        if not f:
            continue
        reason = None
        if abs(f["m1"]) > max_1d_abs:
            reason = f"单日{f['m1']*100:+.1f}%"
        elif abs(f["m20"]) > max_20d_abs:
            reason = f"20日{f['m20']*100:+.1f}%"
        elif f["close"] <= 0:
            reason = "价格≤0"
        if reason:
            rejected.append((name, code, reason))
            continue
        etf_data[code] = {
            "name": name,
            "close": f["close"],
            "mom20": f["m20"],
            "mom10": f["m10"],
            "mom5": f["m5"],
            "mom1": f["m1"],
            "eff": f["eff"],
            "mtf": f["mtf"],
            "vol": f["vol"],
            "factors": f,
        }

    if len(etf_data) < 3:
        return etf_data, rejected

    raw = {c: d["factors"] for c, d in etf_data.items()}
    scores, _ = score_cross_section(raw, weights)
    for c, d in etf_data.items():
        d["score"] = scores.get(c, 0.0)
        d["overheat"] = d["mom20"] > overheat
        d["eligible"] = (not d["overheat"]) and d["score"] > 0
        del d["factors"]
    return etf_data, rejected
