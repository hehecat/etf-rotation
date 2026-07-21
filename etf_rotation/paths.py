"""路径与目录约定.

优先级:
  1) 环境变量 ETF_DATA_DIR
  2) CI / 无桌面时 → <repo>/output
  3) 本机 → ~/桌面/ETF轮动信号
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SCRIPTS_DIR = ROOT / "scripts"
ARCHIVE_DIR = ROOT / "archive"
DOCS_DIR = ROOT / "docs"
OUTPUT_DIR = ROOT / "output"


def _default_data_dir() -> Path:
    env = os.environ.get("ETF_DATA_DIR")
    if env:
        return Path(env)
    # GitHub Actions / 无图形环境
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return OUTPUT_DIR
    desktop = Path.home() / "桌面" / "ETF轮动信号"
    if (Path.home() / "桌面").exists():
        return desktop
    return OUTPUT_DIR


DATA_DIR = _default_data_dir()
STATE_FILE = DATA_DIR / "模拟仓位.json"
LATEST_TXT = DATA_DIR / "latest.txt"
LATEST_JSON = DATA_DIR / "latest.json"
LOG_DIR = DATA_DIR / "logs"
ITER_LOG = DATA_DIR / "迭代记录.md"
SHADOW_DIR = DATA_DIR / "shadow_states"


def shadow_state_file(shadow_name: str) -> Path:
    """研究影子独立仓位文件, 永不与生产 STATE_FILE 混写."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (shadow_name or "shadow"))
    return SHADOW_DIR / f"{safe}.json"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
