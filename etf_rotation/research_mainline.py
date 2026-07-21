"""研究主线名单 (状态/体检/暖机/监控共用, 避免各脚本漂移).

生产策略始终是 c01 (frozen), 不在此列表.
"""
from __future__ import annotations

# 信号侧默认研究影子 (可被 CLI --shadow 覆盖)
SIGNAL_SHADOW = "c01_q10_vt08_soft_oh38_xgn"

# 监控 / 暖机 / 状态面板
MONITOR_SHADOWS: list[str] = [
    "c01_q10_vt08_soft_oh38",
    "c01_q10_vt08_soft_oh38_xgn",
    "c01_q10_vt09_oh35",
    "c01_q10_vt11",
]

# 健康检查长代理门槛 (全样本 sh/dd 地板, 非网格搜索)
LONG_GATES: dict[str, dict[str, float]] = {
    "c01_q10_vt08_soft_oh38": {"min_sharpe": 1.2, "max_dd": 0.18},
    "c01_q10_vt08_soft_oh38_xgn": {"min_sharpe": 1.3, "max_dd": 0.16},
    "c01_q10_vt09_oh35": {"min_sharpe": 1.15, "max_dd": 0.22},
    "c01_q10_vt11": {"min_sharpe": 1.1, "max_dd": 0.26},
}


def shadows_csv(names: list[str] | None = None) -> str:
    return ",".join(names or MONITOR_SHADOWS)


def extra_codes_for_strategies(names: list[str]) -> list[str]:
    """从策略 JSON 收集 park_assets / extra_universe, 供数据加载."""
    from . import config as cfgmod

    codes: list[str] = []
    for name in names:
        try:
            st = cfgmod.load_strategy(name)
        except Exception:
            continue
        for key in ("park_assets", "extra_universe"):
            for c in st.get(key) or []:
                if c:
                    codes.append(str(c))
    # 稳定去重
    return list(dict.fromkeys(codes))
