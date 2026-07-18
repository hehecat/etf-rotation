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


def _efficiency(c: list[float], idx: int, lb: int, signed: bool = False) -> float:
    """Kaufman 效率比. signed=True 时下跌为负 (方向效率)."""
    if idx < lb or c[idx - lb] <= 0:
        return 0.0
    net_raw = c[idx] - c[idx - lb]
    net = abs(net_raw)
    tr = sum(abs(c[i] - c[i - 1]) for i in range(idx - lb + 1, idx + 1))
    if tr <= 0:
        return 0.0
    er = net / tr
    if signed and net_raw < 0:
        return -er
    return er


def compute_factors(
    close: list[float],
    volume: list[float] | None = None,
    lb: int = 20,
    signed_eff: bool = False,
) -> dict[str, float] | None:
    """单标的因子. close 为全历史序列, 取末尾.

    动量窗口与 factors_at_index / 回测对齐:
      mN = close[t] / close[t-N] - 1  (跨 N 根, 非 N+1)
    """
    n = len(close)
    # 信号默认 ~60 根; m60 不足时回退 mom20 (与旧逻辑一致)
    need = max(lb, 20)
    if n < need + 1:
        return None
    idx = n - 1
    c = close
    mom20 = c[idx] / c[idx - 20] - 1.0
    mom10 = c[idx] / c[idx - 10] - 1.0 if idx >= 10 else 0.0
    mom5 = c[idx] / c[idx - 5] - 1.0 if idx >= 5 else 0.0
    mom1 = c[idx] / c[idx - 1] - 1.0 if c[idx - 1] > 0 else 0.0
    m60 = c[idx] / c[idx - 60] - 1.0 if idx >= 60 else mom20
    mlb = c[idx] / c[idx - lb] - 1.0 if idx >= lb else mom20

    rets = [c[i] / c[i - 1] - 1.0 for i in range(idx - lb + 1, idx + 1) if c[i - 1] > 0]
    vol = statistics.stdev(rets) if len(rets) > 5 else 0.03
    sharp = mlb / vol if vol > 0 else 0.0

    eff = _efficiency(c, idx, lb, signed=signed_eff)
    # 无方向版本保留给对照; 打分默认用 signed 时 eff 已带符号
    eff_abs = _efficiency(c, idx, lb, signed=False)
    mtf = sum(1 for x in [mom5, mom10, mom20] if x > 0) / 3.0

    vt = 1.0
    if volume and len(volume) >= 20:
        v5 = sum(volume[-5:]) / 5
        v20 = sum(volume[-20:]) / 20
        vt = v5 / v20 if v20 > 0 else 1.0

    ma20 = sum(c[idx - 19: idx + 1]) / 20
    mad = c[idx] / ma20 - 1.0 if ma20 > 0 else 0.0

    return {
        "m1": mom1, "m5": mom5, "m10": mom10, "m20": mom20, "m60": m60,
        "mlb": mlb, "vol": vol, "sharp": sharp,
        "eff": eff, "eff_abs": eff_abs, "mtf": mtf,
        "vt": vt, "mad": mad, "close": c[idx],
    }


def factors_at_index(
    close: list[float],
    volume: list[float],
    idx: int,
    lb: int = 20,
    signed_eff: bool = False,
) -> dict[str, float] | None:
    """回测用: 在序列索引 idx 处算因子. 与 compute_factors 窗口定义一致.

    回测引擎 start≈65, 此处要求至少 20/lb 即可; m60 不足时回退 m20.
    """
    if idx < max(lb, 20):
        return None
    c = close
    f: dict[str, float] = {}
    f["m5"] = c[idx] / c[idx - 5] - 1 if idx >= 5 else 0.0
    f["m10"] = c[idx] / c[idx - 10] - 1 if idx >= 10 else 0.0
    f["m20"] = c[idx] / c[idx - 20] - 1
    f["m60"] = c[idx] / c[idx - 60] - 1 if idx >= 60 else f["m20"]
    f["mlb"] = c[idx] / c[idx - lb] - 1
    r = [c[i] / c[i - 1] - 1 for i in range(idx - lb + 1, idx + 1) if c[i - 1] > 0]
    f["vol"] = statistics.stdev(r) if len(r) > 5 else 0.03
    f["sharp"] = f["mlb"] / f["vol"] if f["vol"] > 0 else 0
    f["eff"] = _efficiency(c, idx, lb, signed=signed_eff)
    f["eff_abs"] = _efficiency(c, idx, lb, signed=False)
    f["mtf"] = sum(1 for x in [f["m5"], f["m10"], f["m20"]] if x > 0) / 3
    if volume and len(volume) > idx:
        v5 = sum(volume[idx - 4: idx + 1]) / 5
        v20 = sum(volume[idx - 19: idx + 1]) / 20
        f["vt"] = v5 / v20 if v20 > 0 else 1
    else:
        f["vt"] = 1.0
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
    signed_eff: bool = False,
    require_abs_mom: bool = False,
) -> tuple[dict[str, dict], list[tuple[str, str, str]]]:
    """信号用: 构建带得分的 etf_data + 剔除列表.

    signed_eff: 效率因子带方向 (下跌为负)
    require_abs_mom: 仅 m20>0 的标的参与打分/入选
    """
    etf_data: dict[str, dict] = {}
    rejected: list[tuple[str, str, str]] = []

    for code, bars in market.items():
        name = name_map.get(code, code)
        f = compute_factors(bars["close"], bars.get("volume"), signed_eff=signed_eff)
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
    scores, _ = score_cross_section(raw, weights, abs_mom=require_abs_mom)
    for c, d in etf_data.items():
        d["score"] = scores.get(c, 0.0)
        d["overheat"] = d["mom20"] > overheat
        abs_ok = (not require_abs_mom) or d["mom20"] > 0
        d["eligible"] = (not d["overheat"]) and d["score"] > 0 and abs_ok
        del d["factors"]
    return etf_data, rejected
