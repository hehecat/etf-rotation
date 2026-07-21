"""并行搜索候选规格协议与轴定义."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


# 对照锚点 (merge 时去重保留)
ANCHORS: list[dict[str, Any]] = [
    {"id": "anchor_c01", "lane": "anchor", "base": "c01", "overrides": {"ps": 0.95}, "tags": ["anchor"]},
    {"id": "anchor_c01_q10", "lane": "anchor", "base": "c01_q10", "overrides": {"ps": 0.95}, "tags": ["anchor"]},
    {
        "id": "anchor_c01_q10_vt11",
        "lane": "anchor",
        "base": "c01_q10_vt11",
        "overrides": {"ps": 0.95},
        "tags": ["anchor", "vol_target"],
    },
    {
        "id": "anchor_c01_q10_vt13",
        "lane": "anchor",
        "base": "c01_q10_vt13",
        "overrides": {"ps": 0.95},
        "tags": ["anchor", "vol_target"],
    },
    {
        "id": "anchor_c01_q10_vt09_oh35",
        "lane": "anchor",
        "base": "c01_q10_vt09_oh35",
        "overrides": {"ps": 0.95},
        "tags": ["anchor", "vol_target", "overheat"],
    },
    {
        "id": "anchor_c01_q10_vt09_soft_oh40",
        "lane": "anchor",
        "base": "c01_q10_vt09_soft_oh40",
        "overrides": {"ps": 0.95},
        "tags": ["anchor", "vol_target", "regime"],
    },
    {
        "id": "anchor_c01_q10_vt08_soft_oh38",
        "lane": "anchor",
        "base": "c01_q10_vt08_soft_oh38",
        "overrides": {"ps": 0.95},
        "tags": ["anchor", "vol_target", "regime"],
    },
]

REGIME_MAPS: dict[str, dict[str, float]] = {
    "flip": {"bull": 1.0, "chop": 0.8, "riskoff": 0.0, "bear": 0.0},
    "soft": {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0},
    "dualC": {"bull": 1.0, "chop": 0.5, "riskoff": 0.2, "bear": 0.0},
    "dualB": {"bull": 0.9, "chop": 0.55, "riskoff": 0.25, "bear": 0.0},
}

# 8 组预置因子权重 (仅已有因子键)
FACTOR_WEIGHTS: list[tuple[str, dict[str, float]]] = [
    ("c01w", {"eff": 0.6, "mtf": 0.4}),
    ("pure", {"eff": 1.0}),
    ("em20", {"eff": 0.5, "m20": 0.5}),
    ("g15", {"eff": 0.5, "mtf": 0.3, "m20": 0.2}),
    ("momvol", {"mom_vol": 0.6, "mtf": 0.4}),
    ("momvol_eff", {"mom_vol": 0.4, "eff": 0.4, "mtf": 0.2}),
    ("rel", {"eff": 0.5, "rel_m20": 0.3, "mtf": 0.2}),
    ("up", {"eff": 0.5, "up_ratio": 0.3, "mtf": 0.2}),
]

# GitHub 社区轮动常见变体 (12-1 skip / dual / risk-adj60 / minvol / rsi)
GH_FACTOR_WEIGHTS: list[tuple[str, dict[str, float]]] = [
    ("c01w", {"eff": 0.6, "mtf": 0.4}),
    ("skip12_1", {"m60_skip20": 0.7, "mtf": 0.3}),
    ("skip12_1_eff", {"m60_skip20": 0.5, "eff": 0.3, "mtf": 0.2}),
    ("m20skip5", {"m20_skip5": 0.6, "mtf": 0.4}),
    ("dual60", {"dual_m60": 0.7, "mtf": 0.3}),
    ("dual_eff", {"dual_m60": 0.5, "eff": 0.5}),
    ("mv60", {"mom_vol60": 0.7, "mtf": 0.3}),
    ("mv60_eff", {"mom_vol60": 0.5, "eff": 0.3, "mtf": 0.2}),
    ("lowvol_m20", {"low_vol": 0.4, "m20": 0.6}),
    ("rsi_m20", {"rsi_mid": 0.3, "m20": 0.7}),
    ("m120", {"m120": 0.6, "mtf": 0.4}),
    ("mix_skip_mv", {"m60_skip20": 0.4, "mom_vol60": 0.4, "mtf": 0.2}),
]


def _tag_overrides(overrides: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if "vol_target" in overrides and overrides.get("vol_target"):
        tags.append("vol_target")
    if "regime_map" in overrides and overrides.get("regime_map"):
        tags.append("regime")
    if "w" in overrides:
        tags.append("factor")
    if any(k in overrides for k in ("stop", "rb", "overheat")):
        tags.append("stop_rb")
    ps = overrides.get("ps")
    if ps is not None and float(ps) < 0.90:
        tags.append("linear_delever")
    return tags


def make_spec(
    *,
    lane: str,
    base: str,
    overrides: dict[str, Any],
    id_suffix: str,
    extra_tags: Iterable[str] | None = None,
) -> dict[str, Any]:
    ovr = dict(overrides)
    if "ps" not in ovr:
        ovr["ps"] = 0.95
    tags = _tag_overrides(ovr)
    if extra_tags:
        for t in extra_tags:
            if t not in tags:
                tags.append(t)
    cid = f"{lane}_{id_suffix}"
    # 稳定截断过长 id
    if len(cid) > 80:
        h = hashlib.md5(json.dumps(ovr, sort_keys=True, default=str).encode()).hexdigest()[:6]
        cid = f"{lane}_{id_suffix[:50]}_{h}"
    return {
        "id": cid,
        "lane": lane,
        "base": base,
        "overrides": ovr,
        "tags": tags,
    }


def build_lane_candidates(axis: str) -> list[dict[str, Any]]:
    """按轴生成候选; 规模受计划上限约束."""
    axis = axis.strip()
    out: list[dict[str, Any]] = []

    if axis == "vt":
        # ≤120: 7*3*3*2 = 126 → 去掉 0.09 或减一组; 用 7*3*3*2=126, 截到 120
        for vt in [0.09, 0.10, 0.11, 0.12, 0.13, 0.14, 0.15]:
            for lb in [15, 20, 30]:
                for wmin in [0.10, 0.15, 0.25]:
                    for inv in [False, True]:
                        out.append(
                            make_spec(
                                lane="vt",
                                base="c01_q10",
                                overrides={
                                    "vol_target": vt,
                                    "vol_lookback": lb,
                                    "vol_wmin": wmin,
                                    "vol_wmax": 1.0,
                                    "inv_vol": inv,
                                    "ps": 0.95,
                                },
                                id_suffix=f"vt{int(round(vt*100))}_lb{lb}_mn{int(wmin*100)}_iv{int(inv)}",
                            )
                        )
        out = out[:120]

    elif axis == "regime":
        # 4 maps × 3 vol = 12 ≤20
        for name, rmap in REGIME_MAPS.items():
            for vt in [0.0, 0.11, 0.13]:
                ovr: dict[str, Any] = {"regime_map": rmap, "ps": 0.95}
                if vt > 0:
                    ovr.update(
                        {
                            "vol_target": vt,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                        }
                    )
                else:
                    ovr["vol_target"] = 0.0
                out.append(
                    make_spec(
                        lane="regime",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"{name}_vt{int(round(vt*100))}",
                    )
                )

    elif axis == "factor":
        # 8 weights × 2 top_n = 16
        for wn, w in FACTOR_WEIGHTS:
            for tn in [1, 2]:
                ovr = {
                    "w": w,
                    "top_n": tn,
                    "hyst": 0.2 if tn == 1 else 0.3,
                    "min_hold": 5 if tn == 1 else 7,
                    "ps": 0.95,
                }
                out.append(
                    make_spec(
                        lane="factor",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"{wn}_t{tn}",
                    )
                )

    elif axis == "stop_rb":
        # 3 stop × 4 rb × 3 oh = 36 ≤48; 补 oh=0.35 (focus 新发现)
        for stop in [-0.05, -0.08, -0.10]:
            for rb in [8, 10, 12, 15]:
                for oh in [0.30, 0.35, 0.40]:
                    out.append(
                        make_spec(
                            lane="stop_rb",
                            base="c01_q10",
                            overrides={
                                "stop": stop,
                                "rb": rb,
                                "overheat": oh,
                                "vol_target": 0.11,
                                "vol_lookback": 20,
                                "vol_wmin": 0.15,
                                "vol_wmax": 1.0,
                                "inv_vol": True,
                                "ps": 0.95,
                            },
                            id_suffix=f"st{abs(int(round(stop*100)))}_rb{rb}_oh{int(oh*100)}",
                        )
                    )

    elif axis == "oh_vt":
        # overheat × vol_target 正交 (focus 发现 oh0.35 有效); ≤48
        # 5 vt × 5 oh × 2 base_rb = 50 → 截 48
        for vt in [0.09, 0.10, 0.11, 0.12, 0.13]:
            for oh in [0.28, 0.32, 0.35, 0.38, 0.40]:
                for rb in [10]:
                    out.append(
                        make_spec(
                            lane="oh_vt",
                            base="c01_q10",
                            overrides={
                                "vol_target": vt,
                                "vol_lookback": 20,
                                "vol_wmin": 0.15,
                                "vol_wmax": 1.0,
                                "inv_vol": True,
                                "overheat": oh,
                                "rb": rb,
                                "stop": -0.08,
                                "ps": 0.95,
                            },
                            id_suffix=f"vt{int(round(vt*100))}_oh{int(round(oh*100))}_rb{rb}",
                        )
                    )
        # 再扫 rb∈{9,11} 仅在 vt09/vt11 × oh∈{0.32,0.35,0.38}
        for vt in [0.09, 0.11]:
            for oh in [0.32, 0.35, 0.38]:
                for rb in [9, 11]:
                    out.append(
                        make_spec(
                            lane="oh_vt",
                            base="c01_q10",
                            overrides={
                                "vol_target": vt,
                                "vol_lookback": 20,
                                "vol_wmin": 0.15,
                                "vol_wmax": 1.0,
                                "inv_vol": True,
                                "overheat": oh,
                                "rb": rb,
                                "stop": -0.08,
                                "ps": 0.95,
                            },
                            id_suffix=f"vt{int(round(vt*100))}_oh{int(round(oh*100))}_rb{rb}",
                        )
                    )
        out = out[:48]

    elif axis == "exec":
        # 执行参数: min_hold / hyst / stop 在双端候选基底上; ≤36
        bases = [
            ("vt09oh35", "c01_q10_vt09_oh35"),
            ("vt11", "c01_q10_vt11"),
        ]
        for btag, bname in bases:
            for mh in [3, 5, 7, 10]:
                for hyst in [0.15, 0.20, 0.30]:
                    out.append(
                        make_spec(
                            lane="exec",
                            base=bname,
                            overrides={
                                "min_hold": mh,
                                "hyst": hyst,
                                "ps": 0.95,
                            },
                            id_suffix=f"{btag}_mh{mh}_hy{int(hyst*100)}",
                        )
                    )
            # stop 微调 (基底已有 vt/oh)
            for stop in [-0.05, -0.06, -0.08, -0.10]:
                out.append(
                    make_spec(
                        lane="exec",
                        base=bname,
                        overrides={"stop": stop, "ps": 0.95},
                        id_suffix=f"{btag}_st{abs(int(round(stop*100)))}",
                    )
                )
        out = out[:40]

    elif axis == "regime_oh":
        # regime_map × (vt, oh) 正交; 4 maps × 2 vt × 2 oh = 16 ≤24
        # 只在已验证双端带上交叉, 避免扩维爆炸
        for name, rmap in REGIME_MAPS.items():
            for vt, oh in [(0.09, 0.35), (0.11, 0.35), (0.11, 0.40), (0.09, 0.40)]:
                out.append(
                    make_spec(
                        lane="regime_oh",
                        base="c01_q10",
                        overrides={
                            "regime_map": rmap,
                            "vol_target": vt,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                            "overheat": oh,
                            "rb": 10,
                            "stop": -0.08,
                            "ps": 0.95,
                        },
                        id_suffix=f"{name}_vt{int(round(vt*100))}_oh{int(round(oh*100))}",
                    )
                )
        # 无 regime 对照 (纯 oh_vt 锚)
        for vt, oh in [(0.09, 0.35), (0.11, 0.40)]:
            out.append(
                make_spec(
                    lane="regime_oh",
                    base="c01_q10",
                    overrides={
                        "vol_target": vt,
                        "vol_lookback": 20,
                        "vol_wmin": 0.15,
                        "vol_wmax": 1.0,
                        "inv_vol": True,
                        "overheat": oh,
                        "rb": 10,
                        "stop": -0.08,
                        "ps": 0.95,
                    },
                    id_suffix=f"none_vt{int(round(vt*100))}_oh{int(round(oh*100))}",
                )
            )

    elif axis == "factor_vt":
        # 因子权重 × 双端 vt/oh 基底; 8 weights × 2 stacks × top_n=1 = 16
        # 另 top_n=2 仅 c01w/pure/g15 × 最优 stack, 控规模 ≤28
        stacks = [
            ("vt09oh35", {"vol_target": 0.09, "overheat": 0.35}),
            ("vt11", {"vol_target": 0.11, "overheat": 0.40}),
        ]
        for wn, w in FACTOR_WEIGHTS:
            for stag, so in stacks:
                ovr = {
                    "w": w,
                    "top_n": 1,
                    "hyst": 0.2,
                    "min_hold": 5,
                    "vol_target": so["vol_target"],
                    "vol_lookback": 20,
                    "vol_wmin": 0.15,
                    "vol_wmax": 1.0,
                    "inv_vol": True,
                    "overheat": so["overheat"],
                    "rb": 10,
                    "stop": -0.08,
                    "ps": 0.95,
                }
                out.append(
                    make_spec(
                        lane="factor_vt",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"{wn}_{stag}_t1",
                    )
                )
        for wn, w in FACTOR_WEIGHTS:
            if wn not in ("c01w", "pure", "g15", "em20"):
                continue
            so = {"vol_target": 0.09, "overheat": 0.35}
            ovr = {
                "w": w,
                "top_n": 2,
                "hyst": 0.3,
                "min_hold": 7,
                "vol_target": so["vol_target"],
                "vol_lookback": 20,
                "vol_wmin": 0.15,
                "vol_wmax": 1.0,
                "inv_vol": True,
                "overheat": so["overheat"],
                "rb": 10,
                "stop": -0.08,
                "ps": 0.95,
            }
            out.append(
                make_spec(
                    lane="factor_vt",
                    base="c01_q10",
                    overrides=ovr,
                    id_suffix=f"{wn}_vt09oh35_t2",
                )
            )
        out = out[:28]

    elif axis == "soft_grid":
        # soft 状态机 chop×riskoff 细网格 (V28 最优结构上)
        # bull=1 bear=0 固定; 主扫 vt09+oh40, 辅扫 oh35 / vt11
        def _soft_map(chop: float, riskoff: float) -> dict[str, float]:
            return {"bull": 1.0, "chop": chop, "riskoff": riskoff, "bear": 0.0}

        # 主网格: 5×5 = 25
        for chop in [0.70, 0.80, 0.85, 0.90, 0.95]:
            for ro in [0.0, 0.10, 0.20, 0.25, 0.35]:
                out.append(
                    make_spec(
                        lane="soft_grid",
                        base="c01_q10",
                        overrides={
                            "regime_map": _soft_map(chop, ro),
                            "vol_target": 0.09,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                            "overheat": 0.40,
                            "rb": 10,
                            "stop": -0.08,
                            "ps": 0.95,
                        },
                        id_suffix=f"vt9_oh40_c{int(round(chop*100))}_r{int(round(ro*100))}",
                    )
                )
        # 辅: oh35 × 3×4 = 12
        for chop in [0.80, 0.90, 0.95]:
            for ro in [0.0, 0.15, 0.25, 0.35]:
                out.append(
                    make_spec(
                        lane="soft_grid",
                        base="c01_q10",
                        overrides={
                            "regime_map": _soft_map(chop, ro),
                            "vol_target": 0.09,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                            "overheat": 0.35,
                            "rb": 10,
                            "stop": -0.08,
                            "ps": 0.95,
                        },
                        id_suffix=f"vt9_oh35_c{int(round(chop*100))}_r{int(round(ro*100))}",
                    )
                )
        # 辅: vt11+oh40 × 2×3 = 6
        for chop in [0.80, 0.90]:
            for ro in [0.15, 0.25, 0.35]:
                out.append(
                    make_spec(
                        lane="soft_grid",
                        base="c01_q10",
                        overrides={
                            "regime_map": _soft_map(chop, ro),
                            "vol_target": 0.11,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                            "overheat": 0.40,
                            "rb": 10,
                            "stop": -0.08,
                            "ps": 0.95,
                        },
                        id_suffix=f"vt11_oh40_c{int(round(chop*100))}_r{int(round(ro*100))}",
                    )
                )
        out = out[:48]

    elif axis == "soft_stack":
        # 固定 soft map {1,0.9,0.25,0} 上扫 vt×oh (验证 V28 邻域)
        rmap = {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0}
        for vt in [0.08, 0.09, 0.10, 0.11, 0.12]:
            for oh in [0.32, 0.35, 0.38, 0.40, 0.45]:
                out.append(
                    make_spec(
                        lane="soft_stack",
                        base="c01_q10",
                        overrides={
                            "regime_map": rmap,
                            "vol_target": vt,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                            "overheat": oh,
                            "rb": 10,
                            "stop": -0.08,
                            "ps": 0.95,
                        },
                        id_suffix=f"vt{int(round(vt*100))}_oh{int(round(oh*100))}",
                    )
                )
        out = out[:30]

    elif axis == "soft_rb":
        # soft+vt08 基底上扫 rb×stop×oh (V29 主线正交)
        rmap = {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0}
        # 主: vt08 × oh∈{0.35,0.38,0.40} × rb×stop = 3*4*3 = 36
        for oh in [0.35, 0.38, 0.40]:
            for rb in [8, 10, 12, 15]:
                for stop in [-0.05, -0.08, -0.10]:
                    out.append(
                        make_spec(
                            lane="soft_rb",
                            base="c01_q10",
                            overrides={
                                "regime_map": rmap,
                                "vol_target": 0.08,
                                "vol_lookback": 20,
                                "vol_wmin": 0.15,
                                "vol_wmax": 1.0,
                                "inv_vol": True,
                                "overheat": oh,
                                "rb": rb,
                                "stop": stop,
                                "ps": 0.95,
                            },
                            id_suffix=(
                                f"vt8_oh{int(round(oh*100))}_rb{rb}"
                                f"_st{abs(int(round(stop*100)))}"
                            ),
                        )
                    )
        # 辅: vt09+oh40 × rb×stop (验证是否仍劣于 vt08) = 4*3=12
        for rb in [8, 10, 12, 15]:
            for stop in [-0.05, -0.08, -0.10]:
                out.append(
                    make_spec(
                        lane="soft_rb",
                        base="c01_q10",
                        overrides={
                            "regime_map": rmap,
                            "vol_target": 0.09,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                            "overheat": 0.40,
                            "rb": rb,
                            "stop": stop,
                            "ps": 0.95,
                        },
                        id_suffix=f"vt9_oh40_rb{rb}_st{abs(int(round(stop*100)))}",
                    )
                )
        out = out[:48]

    elif axis == "soft_vt_rb":
        # soft map 固定, vt×rb 粗扫 (oh 固定 0.38, stop -0.08)
        rmap = {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0}
        for vt in [0.07, 0.08, 0.09, 0.10, 0.11]:
            for rb in [8, 10, 12, 15]:
                out.append(
                    make_spec(
                        lane="soft_vt_rb",
                        base="c01_q10",
                        overrides={
                            "regime_map": rmap,
                            "vol_target": vt,
                            "vol_lookback": 20,
                            "vol_wmin": 0.15,
                            "vol_wmax": 1.0,
                            "inv_vol": True,
                            "overheat": 0.38,
                            "rb": rb,
                            "stop": -0.08,
                            "ps": 0.95,
                        },
                        id_suffix=f"vt{int(round(vt*100))}_rb{rb}",
                    )
                )
        out = out[:24]

    elif axis == "soft_exec":
        # 主线 soft+vt08+oh38 上扫 min_hold/hyst/stop (执行摩擦轴)
        rmap = {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0}
        base_ovr = {
            "regime_map": rmap,
            "vol_target": 0.08,
            "vol_lookback": 20,
            "vol_wmin": 0.15,
            "vol_wmax": 1.0,
            "inv_vol": True,
            "overheat": 0.38,
            "rb": 10,
            "ps": 0.95,
        }
        for mh in [3, 5, 7, 10]:
            for hyst in [0.10, 0.15, 0.20, 0.30]:
                ovr = dict(base_ovr)
                ovr.update({"min_hold": mh, "hyst": hyst, "stop": -0.08})
                out.append(
                    make_spec(
                        lane="soft_exec",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"mh{mh}_hy{int(round(hyst*100))}",
                    )
                )
        for stop in [-0.05, -0.06, -0.08, -0.10, -0.12]:
            ovr = dict(base_ovr)
            ovr.update({"min_hold": 5, "hyst": 0.20, "stop": stop})
            out.append(
                make_spec(
                    lane="soft_exec",
                    base="c01_q10",
                    overrides=ovr,
                    id_suffix=f"st{abs(int(round(stop*100)))}",
                )
            )
        # 辅: oh35 / oh40 仅 default exec, 看执行轴是否与 oh 交互
        for oh in [0.35, 0.40]:
            for mh, hyst in [(5, 0.20), (7, 0.15), (3, 0.30)]:
                ovr = dict(base_ovr)
                ovr.update(
                    {
                        "overheat": oh,
                        "min_hold": mh,
                        "hyst": hyst,
                        "stop": -0.08,
                    }
                )
                out.append(
                    make_spec(
                        lane="soft_exec",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"oh{int(round(oh*100))}_mh{mh}_hy{int(round(hyst*100))}",
                    )
                )
        out = out[:40]

    elif axis == "soft_vol":
        # 主线 soft+vt08+oh38 上扫 vol 估计参数 (lb/wmin/mode)
        rmap = {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0}
        base_ovr = {
            "regime_map": rmap,
            "vol_target": 0.08,
            "overheat": 0.38,
            "rb": 10,
            "stop": -0.08,
            "inv_vol": True,
            "vol_wmax": 1.0,
            "ps": 0.95,
        }
        for lb in [10, 15, 20, 30, 40]:
            for wmin in [0.05, 0.10, 0.15, 0.25, 0.35]:
                ovr = dict(base_ovr)
                ovr.update(
                    {
                        "vol_lookback": lb,
                        "vol_wmin": wmin,
                        "vol_mode": "std",
                    }
                )
                out.append(
                    make_spec(
                        lane="soft_vol",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"lb{lb}_mn{int(round(wmin*100))}_std",
                    )
                )
        # 波动估计模式: ewma/down × 关键 lb
        for mode in ["ewma", "down"]:
            for lb in [15, 20, 30]:
                for wmin in [0.10, 0.15, 0.25]:
                    ovr = dict(base_ovr)
                    ovr.update(
                        {
                            "vol_lookback": lb,
                            "vol_wmin": wmin,
                            "vol_mode": mode,
                            "vol_ewma_span": lb,
                        }
                    )
                    out.append(
                        make_spec(
                            lane="soft_vol",
                            base="c01_q10",
                            overrides=ovr,
                            id_suffix=f"lb{lb}_mn{int(round(wmin*100))}_{mode}",
                        )
                    )
        out = out[:48]

    elif axis == "soft_struct":
        # soft 主线上结构开关: soft_trend/park/state_pos/因子 slope
        rmap = {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0}
        base_ovr = {
            "regime_map": rmap,
            "vol_target": 0.08,
            "vol_lookback": 20,
            "vol_wmin": 0.15,
            "vol_wmax": 1.0,
            "inv_vol": True,
            "overheat": 0.38,
            "rb": 10,
            "stop": -0.08,
            "ps": 0.95,
        }
        # 开关组合 (有限笛卡尔, 去噪)
        toggles = [
            ("base", {}),
            ("st", {"soft_trend": True, "park_bench": True}),
            ("st_sp", {"soft_trend": True, "park_bench": True, "state_pos": True}),
            ("sp", {"state_pos": True}),
            (
                "st_parksc",
                {
                    "soft_trend": True,
                    "park_bench": True,
                    "park_scale": 0.55,
                },
            ),
            (
                "st_parksc80",
                {
                    "soft_trend": True,
                    "park_bench": True,
                    "park_scale": 0.80,
                },
            ),
        ]
        for tname, tovr in toggles:
            ovr = dict(base_ovr)
            ovr.update(tovr)
            out.append(
                make_spec(
                    lane="soft_struct",
                    base="c01_q10",
                    overrides=ovr,
                    id_suffix=tname,
                    extra_tags=["struct"],
                )
            )
        # 因子换成 slope_r2 / mom_vol 在 soft 主线
        for wn, w in [
            ("c01w", {"eff": 0.6, "mtf": 0.4}),
            ("slope", {"slope_r2": 0.7, "mtf": 0.3}),
            ("slope20", {"slope_r2_20": 0.6, "eff": 0.4}),
            ("momvol", {"mom_vol": 0.6, "mtf": 0.4}),
            ("accel", {"accel": 0.5, "eff": 0.5}),
        ]:
            for st_on in [False, True]:
                ovr = dict(base_ovr)
                ovr["w"] = w
                if st_on:
                    ovr.update({"soft_trend": True, "park_bench": True})
                out.append(
                    make_spec(
                        lane="soft_struct",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"{wn}_st{int(st_on)}",
                        extra_tags=["struct", "factor"],
                    )
                )
        # park_scale × soft_trend 细扫
        for pscl in [0.40, 0.55, 0.70, 0.85, 1.0]:
            ovr = dict(base_ovr)
            ovr.update(
                {
                    "soft_trend": True,
                    "park_bench": True,
                    "park_scale": pscl,
                    "state_pos": False,
                }
            )
            out.append(
                make_spec(
                    lane="soft_struct",
                    base="c01_q10",
                    overrides=ovr,
                    id_suffix=f"parksc{int(round(pscl*100))}",
                    extra_tags=["struct"],
                )
            )
        out = out[:36]

    elif axis == "factor_gh":
        # GitHub 社区因子 × 主线 soft/oh 栈; ≤ 12 packs × 2 stacks = 24
        stacks = [
            (
                "softvt8",
                {
                    "regime_map": {
                        "bull": 1.0,
                        "chop": 0.9,
                        "riskoff": 0.25,
                        "bear": 0.0,
                    },
                    "vol_target": 0.08,
                    "overheat": 0.38,
                },
            ),
            (
                "vt09oh35",
                {
                    "vol_target": 0.09,
                    "overheat": 0.35,
                },
            ),
        ]
        for wn, w in GH_FACTOR_WEIGHTS:
            for stag, so in stacks:
                ovr: dict[str, Any] = {
                    "w": w,
                    "top_n": 1,
                    "hyst": 0.2,
                    "min_hold": 5,
                    "vol_lookback": 20,
                    "vol_wmin": 0.15,
                    "vol_wmax": 1.0,
                    "inv_vol": True,
                    "rb": 10,
                    "stop": -0.08,
                    "ps": 0.95,
                }
                ovr.update(so)
                out.append(
                    make_spec(
                        lane="factor_gh",
                        base="c01_q10",
                        overrides=ovr,
                        id_suffix=f"{wn}_{stag}",
                        extra_tags=["factor", "github"],
                    )
                )
        out = out[:28]

    elif axis == "universe_park":
        # 跨资产停靠 / 扩宇宙 (抗跌与收益真实结构, 非参数微调)
        # 主线 soft+vt08+oh38 上扫 park/extra/prefer_bench; ≤24
        rmap = {"bull": 1.0, "chop": 0.9, "riskoff": 0.25, "bear": 0.0}
        base = {
            "regime_map": rmap,
            "vol_target": 0.08,
            "vol_lookback": 20,
            "vol_wmin": 0.15,
            "vol_wmax": 1.0,
            "inv_vol": True,
            "overheat": 0.38,
            "rb": 10,
            "stop": -0.08,
            "ps": 0.95,
            "min_hold": 5,
            "hyst": 0.2,
        }
        gold = "SZ159934"
        gold2 = "SH518880"
        nq = "SH513100"
        spx = "SH513500"

        # 1) 对照: 纯主线
        out.append(
            make_spec(
                lane="universe_park",
                base="c01_q10",
                overrides=dict(base),
                id_suffix="base",
                extra_tags=["universe"],
            )
        )
        # 2) prefer_bench only
        o = dict(base)
        o["prefer_bench_if_stronger"] = True
        out.append(
            make_spec(
                lane="universe_park",
                base="c01_q10",
                overrides=o,
                id_suffix="prefer_bench",
                extra_tags=["universe"],
            )
        )
        # 3) park_bench 仅基准
        o = dict(base)
        o.update({"park_bench": True, "soft_trend": False})
        out.append(
            make_spec(
                lane="universe_park",
                base="c01_q10",
                overrides=o,
                id_suffix="park_bench",
                extra_tags=["universe"],
            )
        )
        # 4-7) park_assets 黄金 / 黄金+纳指 / 仅纳指 / 双金
        for tag, assets, soft in [
            ("gold", [gold], False),
            ("gold_soft", [gold], True),
            ("gold_nq", [gold, nq], False),
            ("gold_nq_soft", [gold, nq], True),
            ("gold2", [gold2], False),
            ("nq", [nq], False),
            ("spx", [spx], False),
            ("gold_spx", [gold, spx], False),
        ]:
            o = dict(base)
            o.update(
                {
                    "park_bench": True,
                    "park_assets": assets,
                    "soft_trend": soft,
                    "park_scale": 0.70,
                }
            )
            out.append(
                make_spec(
                    lane="universe_park",
                    base="c01_q10",
                    overrides=o,
                    id_suffix=f"park_{tag}",
                    extra_tags=["universe", "park"],
                )
            )
        # 8) extra_universe 参与进攻打分 (黄金/纳指当候选)
        for tag, assets in [
            ("extra_gold", [gold]),
            ("extra_gold_nq", [gold, nq]),
            ("extra_nq_spx", [nq, spx]),
        ]:
            o = dict(base)
            o["extra_universe"] = assets
            out.append(
                make_spec(
                    lane="universe_park",
                    base="c01_q10",
                    overrides=o,
                    id_suffix=tag,
                    extra_tags=["universe", "extra"],
                )
            )
        # 9) park + prefer + gold
        o = dict(base)
        o.update(
            {
                "park_bench": True,
                "prefer_bench_if_stronger": True,
                "park_assets": [gold],
                "soft_trend": False,
            }
        )
        out.append(
            make_spec(
                lane="universe_park",
                base="c01_q10",
                overrides=o,
                id_suffix="prefer_park_gold",
                extra_tags=["universe"],
            )
        )
        # 10) park_scale 扫描 (gold park)
        for pscl in [0.50, 0.70, 0.90]:
            o = dict(base)
            o.update(
                {
                    "park_bench": True,
                    "park_assets": [gold],
                    "park_scale": pscl,
                    "state_pos": True,
                    "soft_trend": False,
                }
            )
            out.append(
                make_spec(
                    lane="universe_park",
                    base="c01_q10",
                    overrides=o,
                    id_suffix=f"gold_pscl{int(round(pscl*100))}",
                    extra_tags=["universe", "park"],
                )
            )
        out = out[:24]

    else:
        raise ValueError(
            f"未知轴: {axis}; 支持 vt,regime,factor,stop_rb,oh_vt,exec,"
            f"regime_oh,factor_vt,soft_grid,soft_stack,soft_rb,soft_vt_rb,"
            f"soft_exec,soft_vol,soft_struct,factor_gh,universe_park"
        )

    return out


def default_axes() -> list[str]:
    return [
        "vt",
        "regime",
        "factor",
        "stop_rb",
        "oh_vt",
        "exec",
        "regime_oh",
        "factor_vt",
        "soft_grid",
        "soft_stack",
        "soft_rb",
        "soft_vt_rb",
        "soft_exec",
        "soft_vol",
        "soft_struct",
        "factor_gh",
        "universe_park",
    ]


def score_metrics(m: dict[str, Any]) -> float:
    """shortlist 排序分."""
    return (
        float(m.get("sharpe", 0)) * 100
        + float(m.get("calmar", 0)) * 50
        - abs(float(m.get("dd", 0))) * 35
        + float(m.get("ann", 0)) * 0.2
    )


def is_shortlist_eligible(m: dict[str, Any], keep_delever: bool = False) -> bool:
    tags = m.get("tags") or []
    if (not keep_delever) and "linear_delever" in tags:
        return False
    if "expectancy" in m and m["expectancy"] is not None:
        if float(m["expectancy"]) <= 0:
            return False
    if float(m.get("sharpe", 0)) < 0.90:
        return False
    if abs(float(m.get("dd", 0))) > 0.35:
        return False
    return True
