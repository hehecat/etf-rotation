#!/usr/bin/env python3
"""把 latest 信号发邮件 (HTML + 纯文本).

环境变量 (GitHub Secrets 同名):
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD
  MAIL_FROM / MAIL_TO / MAIL_SUBJECT
  MAIL_PAGES_URL (可选) — 邮件内链到 GitHub Pages

用法:
  python3 scripts/send_email.py
  python3 scripts/send_email.py --append-shadow --append-alerts --append-status
  python3 scripts/send_email.py --append-alerts --append-status --dry-print
  python3 scripts/send_email.py --html --dry-print
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import LATEST_JSON, LATEST_TXT, OUTPUT_DIR  # noqa: E402

try:
    from etf_rotation.research_mainline import SIGNAL_SHADOW  # noqa: E402
except Exception:
    SIGNAL_SHADOW = "c01_q10_vt08_soft_oh38_xgn"


def _shadow_block(names: str = "") -> str:
    try:
        from scripts.shadow_summary import collect_rows, format_text  # type: ignore
    except Exception:
        sys.path.insert(0, str(ROOT / "scripts"))
        from shadow_summary import collect_rows, format_text  # type: ignore
    rows = collect_rows(names)
    text = format_text(rows)
    # 与 etf summary 产物对齐, 便于面板/体检复用
    try:
        out_txt = OUTPUT_DIR / "shadow_summary.txt"
        out_json = OUTPUT_DIR / "risk_audit" / "shadow_summary.json"
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_txt.write_text(text + "\n", encoding="utf-8")
        out_json.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        pass
    return text


def _alerts_block() -> str:
    mon = ROOT / "output" / "risk_audit" / "shadow_monitor.json"
    pipe = ROOT / "output" / "risk_audit" / "pipeline_last.json"
    err_n = warn_n = 0
    items: list[str] = []
    src = None
    if mon.exists():
        try:
            data = json.loads(mon.read_text(encoding="utf-8"))
            err_n = int(data.get("alert_error_n") or 0)
            warn_n = int(data.get("alert_warn_n") or 0)
            for row in data.get("rows") or []:
                for a in row.get("alerts") or []:
                    items.append(
                        f"  [{a.get('level')}] {row.get('name')}: "
                        f"{a.get('code')} — {a.get('msg')}"
                    )
            src = "shadow_monitor.json"
        except Exception as e:
            return f"--------\n告警读取失败: {e}\n"
    elif pipe.exists():
        try:
            data = json.loads(pipe.read_text(encoding="utf-8"))
            err_n = int(data.get("alert_error_n") or 0)
            warn_n = int(data.get("alert_warn_n") or 0)
            for a in data.get("alerts") or []:
                items.append(
                    f"  [{a.get('level')}] {a.get('shadow')}: "
                    f"{a.get('code')} — {a.get('msg')}"
                )
            src = "pipeline_last.json"
        except Exception as e:
            return f"--------\n告警读取失败: {e}\n"
    else:
        return "--------\n告警: 无 monitor/pipeline 产物\n"
    lines = [
        "--------",
        f"告警汇总 error={err_n} warn={warn_n} ({src})",
    ]
    if items:
        lines.extend(items[:30])
    else:
        lines.append("  (无告警)")
    return "\n".join(lines)


def _alert_error_n() -> int:
    mon = ROOT / "output" / "risk_audit" / "shadow_monitor.json"
    if not mon.exists():
        return 0
    try:
        return int(json.loads(mon.read_text(encoding="utf-8")).get("alert_error_n") or 0)
    except Exception:
        return 0


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None



def _zh_level(level) -> str:
    m = {
        "READY": "可当真收益看",
        "THIN": "样本还少",
        "NOT_READY": "暂不可判",
        "WAIT_DATA": "等行情更新",
        "PARTIAL": "部分可观察",
    }
    s = str(level or "").strip()
    return m.get(s, s or "—")


def _zh_action(action) -> str:
    m = {
        "wait_asof": "先等行情更新",
        "refresh": "先刷新信号",
        "accumulate": "继续日更攒样本",
        "read_yield": "可以看真实收益了",
        "doctor": "先体检排查",
        "wait_data": "先等行情更新",
        "ok": "数据齐",
    }
    s = str(action or "").strip()
    return m.get(s, s or "—")


def _zh_sample_days(dl, thin=None) -> str:
    try:
        if dl is None:
            return "样本未知"
        n = int(dl)
        if n <= 0:
            return "还没开始统计"
        if thin or n < 5:
            return f"已统计{n}天(满5天更稳)"
        return f"已统计{n}天"
    except Exception:
        return "样本未知"


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _money(x: Any) -> str:
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "—"


def _ensure_status_txt() -> Path:
    st_path = ROOT / "output" / "research_status.txt"
    if st_path.exists():
        return st_path
    import subprocess

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "research_status.py"),
            "--text-out",
            str(st_path),
            "--json-out",
            str(ROOT / "output" / "risk_audit" / "research_status.json"),
        ],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
    )
    return st_path


def _compare_block() -> str:
    path = ROOT / "output" / "shadow_compare.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "shadow_compare.py"),
                "--text-out",
                str(path),
                "--json-out",
                str(ROOT / "output" / "risk_audit" / "shadow_compare.json"),
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "--------\n主线对照: 无产物\n"


def _live_block() -> str:
    path = ROOT / "output" / "shadow_live.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "shadow_live.py"),
                "--text-out",
                str(path),
                "--json-out",
                str(ROOT / "output" / "risk_audit" / "shadow_live.json"),
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "--------\n主线 LIVE: 无产物\n"


def _today_block() -> str:
    path = ROOT / "output" / "today.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "today",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "--------\n今日速览: 无产物\n"



def _asof_block() -> str:
    path = ROOT / "output" / "asof.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "asof",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== 行情取证 ========\n(无 asof 产物)\n"




def _yield_block() -> str:
    path = ROOT / "output" / "yield.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "yield",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== 有效收益 ========\n(无 yield 产物)\n"



def _brief_block() -> str:
    path = ROOT / "output" / "brief.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "brief",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== BRIEF ========\n(无 brief 产物)\n"



def _data_block() -> str:
    path = ROOT / "output" / "data_status.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "data",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== 行情状态 ========\n(无 data_status 产物)\n"



def _next_block() -> str:
    path = ROOT / "output" / "next.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "next",
                "--no-refresh",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== NEXT ========\n(无 next 产物)\n"



def _pull_block() -> str:
    path = ROOT / "output" / "pull.txt"
    if not path.exists():
        return "======== PULL ========\n(无 pull 产物; ./etf pull --bench-only)\n"
    return path.read_text(encoding="utf-8")



def _go_block() -> str:
    path = ROOT / "output" / "go.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "go",
                "--no-wait",
                "--no-pull",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== GO ========\n(无 go 产物)\n"



def _ready_block() -> str:
    path = ROOT / "output" / "ready.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "ready",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== READY ========\n(无 ready 产物)\n"



def _digest_block() -> str:
    path = ROOT / "output" / "digest.txt"
    if not path.exists():
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "etf.py"),
                "digest",
                "--no-refresh",
            ],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== DIGEST ========\n(无 digest 产物)\n"



def _eod_block() -> str:
    path = ROOT / "output" / "eod.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== EOD ========\n(无 eod 产物; ./etf eod --no-wait)\n"



def _progress_block() -> str:
    path = ROOT / "output" / "progress.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== 统计进度 ========\n(暂无统计进度; ./etf progress)\n"



def _pulse_block() -> str:
    path = ROOT / "output" / "pulse.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "======== 现在怎么办 ========\n(暂无脉搏; ./etf pulse)\n"

def _eta_summary_line() -> str:
    """邮件首屏: 距可判/ETA (真实有效收益可读性)."""
    import json
    dtr = None
    eta = ""
    level = None
    lag = None
    for rel in (
        "risk_audit/ready.json",
        "risk_audit/digest.json",
        "risk_audit/progress_latest.json",
        "risk_audit/yield.json",
        "risk_audit/next.json",
        "risk_audit/pulse.json",
    ):
        p = ROOT / "output" / rel
        if not p.exists():
            continue
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(o, dict):
            continue
        if dtr is None and o.get("days_to_ready") is not None:
            dtr = o.get("days_to_ready")
        if not eta and o.get("eta_note"):
            eta = str(o.get("eta_note"))
        if level is None and o.get("level"):
            level = o.get("level")
        if lag is None and o.get("data_lag") is not None:
            lag = bool(o.get("data_lag"))
        if dtr is not None and eta:
            break
    parts = ["======== 能不能当真看收益 ========"]
    if level:
        parts.append(f"结论: {_zh_level(level)}")
    if dtr is not None:
        parts.append(f"距可判: 大约还要{dtr}个交易日 (满5天更稳)")
    if eta:
        parts.append(
            "说明: "
            + str(eta)
            .replace("READY (Lrets≥5)", "可当真看收益")
            .replace("Lrets≥5", "满5天更稳")
            .replace("READY", "可当真看收益")
            .replace("DATA_LAG", "行情未更新")
            .replace("另需 asof 先推进", "先等今日行情写入")
            .replace("另需 行情 先推进", "先等今日行情写入")
            .replace("asof", "行情")
            .replace("约再 ", "大约还要")
            .replace("可 可当真", "可当真")
        )
    elif dtr is not None:
        try:
            di = int(dtr)
            if di <= 0:
                parts.append("样本已够 (若行情也齐 → 可当真收益看)")
            else:
                lag_s = "；先等今日行情写入" if lag else ""
                parts.append(f"还要约{di}个交易日才能当真看收益{lag_s}")
        except Exception:
            pass
    try:
        pp = ROOT / "output" / "risk_audit" / "pulse.json"
        if pp.exists():
            pj = json.loads(pp.read_text(encoding="utf-8"))
            if isinstance(pj, dict) and pj.get("next_action"):
                parts.append(
                    "下一步: %s · %s → ./etf do"
                    % (
                        _zh_action(pj.get("next_action")),
                        "现在可当真收益看" if pj.get("readable_yield") else "现在不要当真收益看",
                    )
                )
    except Exception:
        pass
    if len(parts) == 1:
        return ""
    parts.append("轨迹: ./etf progress | 执行: ./etf do")
    parts.append("========")
    return "\n".join(parts)



def _plain_body(
    base: str,
    *,
    append_shadow: bool,
    shadow_names: str,
    append_alerts: bool,
    append_status: bool,
    append_compare: bool = False,
    append_live: bool = False,
    append_today: bool = False,
    append_asof: bool = False,
    append_yield: bool = False,
    append_brief: bool = False,
    append_data: bool = False,
    append_next: bool = False,
    append_pull: bool = False,
    append_go: bool = False,
    append_ready: bool = False,
    append_digest: bool = False,
    append_eod: bool = False,
    append_progress: bool = False,
    append_pulse: bool = False,
) -> str:
    lines = []
    for line in base.splitlines():
        if re.fullmatch(r"[=─-]{10,}", line.strip()):
            lines.append("--------")
        else:
            lines.append(line)
    body = "\n".join(lines)

    try:
        eta_blk = _eta_summary_line()
        if eta_blk:
            body = body.rstrip() + "\n\n" + eta_blk + "\n"
    except Exception as e:
        body = body.rstrip() + f"\n\n--------\n可判说明生成失败: {e}\n"

    if append_shadow:
        try:
            body = body.rstrip() + "\n\n" + _shadow_block(shadow_names) + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\n影子摘要失败: {e}\n"

    if append_alerts:
        try:
            body = body.rstrip() + "\n\n" + _alerts_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\n告警附加失败: {e}\n"

    # latest 过旧 / 行情滞后 (纯文本)
    try:
        from etf_rotation.calendar_util import resolve_trading_day

        latest = _load_json(LATEST_JSON) or {}
        td = resolve_trading_day()
        td_date = str((td or {}).get("date") or "")[:10]
        ld = str((latest or {}).get("time") or "")[:10]
        asof = str((latest or {}).get("market_asof") or (td or {}).get("data_asof") or "")[:10]
        notes = []
        if (td or {}).get("data_lag") or (asof and td_date and asof < td_date):
            notes.append(
                f"⚠ 行情还没更新完: 行情截至 {asof} · 今天交易日 {td_date} → ./etf next|data"
            )
        if (td or {}).get("is_trading_day") and td_date and ld and ld < td_date:
            notes.append(
                f"⚠ latest 过旧: 信号日 {ld} < 交易日 {td_date} → ./etf refresh"
            )
        if notes:
            body = body.rstrip() + "\n\n" + "\n".join(notes) + "\n"
    except Exception:
        pass

    if append_compare:
        try:
            body = body.rstrip() + "\n\n" + _compare_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\n主线对照附加失败: {e}\n"

    if append_live:
        try:
            body = body.rstrip() + "\n\n" + _live_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\nLIVE 附加失败: {e}\n"

    if append_today:
        try:
            body = body.rstrip() + "\n\n" + _today_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\n今日速览附加失败: {e}\n"

    if append_asof:
        try:
            body = body.rstrip() + "\n\n" + _asof_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\nasof 取证附加失败: {e}\n"

    if append_yield:
        try:
            body = body.rstrip() + "\n\n" + _yield_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\nyield 附加失败: {e}\n"

    if append_brief:
        try:
            body = body.rstrip() + "\n\n" + _brief_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\nbrief 附加失败: {e}\n"

    if append_data:
        try:
            body = body.rstrip() + "\n\n" + _data_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\ndata 附加失败: {e}\n"

    if append_next:
        try:
            body = body.rstrip() + "\n\n" + _next_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\nnext 附加失败: {e}\n"

    if append_pull:
        try:
            body = body.rstrip() + "\n\n" + _pull_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\npull 附加失败: {e}\n"

    if append_go:
        try:
            body = body.rstrip() + "\n\n" + _go_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\ngo 附加失败: {e}\n"

    if append_ready:
        try:
            body = body.rstrip() + "\n\n" + _ready_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\nready 附加失败: {e}\n"

    if append_digest:
        try:
            body = body.rstrip() + "\n\n" + _digest_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\ndigest 附加失败: {e}\n"

    if append_eod:
        try:
            body = body.rstrip() + "\n\n" + _eod_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\neod 附加失败: {e}\n"

    if append_progress:
        try:
            body = body.rstrip() + "\n\n" + _progress_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\nprogress 附加失败: {e}\n"

    if append_pulse:
        try:
            body = body.rstrip() + "\n\n" + _pulse_block() + "\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\npulse 附加失败: {e}\n"

    if append_status:
        try:
            st_path = _ensure_status_txt()
            if st_path.exists():
                body = body.rstrip() + "\n\n" + st_path.read_text(encoding="utf-8") + "\n"
            else:
                body = body.rstrip() + "\n\n--------\n研究状态: 无产物\n"
        except Exception as e:
            body = body.rstrip() + f"\n\n--------\n研究状态附加失败: {e}\n"
    return body


def _extract_action(text: str, latest: dict | None) -> str:
    if latest and latest.get("action"):
        return str(latest["action"])
    for line in text.splitlines():
        if "今日动作" in line:
            return line.split("今日动作")[-1].strip(" :：")
    return "日报"


def _html_report(
    latest: dict | None,
    plain: str,
    *,
    append_shadow: bool,
    shadow_names: str,
    append_alerts: bool,
    append_status: bool,
    append_compare: bool = False,
    append_live: bool = False,
    append_today: bool = False,
    append_asof: bool = False,
    append_yield: bool = False,
    append_brief: bool = False,
    append_data: bool = False,
    append_next: bool = False,
    append_pull: bool = False,
    append_go: bool = False,
    append_ready: bool = False,
    append_digest: bool = False,
    append_eod: bool = False,
    append_progress: bool = False,
    append_pulse: bool = False,
    pages_url: str,
) -> str:
    action = _esc(_extract_action(plain, latest))
    cfg = _esc((latest or {}).get("config") or "c01")
    when = _esc((latest or {}).get("time") or "—")
    market_ok = bool((latest or {}).get("market_ok"))
    frozen = bool((latest or {}).get("frozen"))
    holding = (latest or {}).get("holding")
    hold_name = "空仓"
    if isinstance(holding, dict):
        hold_name = holding.get("name") or holding.get("code") or "持仓中"
    elif holding:
        hold_name = str(holding)
    reasons = (latest or {}).get("reasons") or []
    top = (latest or {}).get("top3") or []
    checks = (latest or {}).get("checks") or []
    shadow = (latest or {}).get("shadow") or {}
    err_n = _alert_error_n()
    mon = _load_json(OUTPUT_DIR / "risk_audit" / "shadow_monitor.json") or {}
    warn_n = int(mon.get("alert_warn_n") or 0)

    trend = "趋势开" if market_ok else "趋势关"
    trend_bg = "#dafbe1" if market_ok else "#eaeef2"
    trend_fg = "#1a7f37" if market_ok else "#59636a"
    action_bg = "#ddf4ff"
    if err_n:
        action_bg = "#ffebe9"
    elif "空仓" in action:
        action_bg = "#f6f8fa"

    reason_lis = "".join(f"<li style='margin:4px 0'>{_esc(r)}</li>" for r in reasons[:8]) or "<li>无</li>"
    top_rows = ""
    for i, t in enumerate(top, 1):
        try:
            sc = f"{float(t.get('score')):+.2f}"
        except Exception:
            sc = "—"
        top_rows += (
            f"<tr><td style='padding:6px 8px'>{i}</td>"
            f"<td style='padding:6px 8px'>{_esc(t.get('name') or t.get('code'))}</td>"
            f"<td style='padding:6px 8px;color:#656d76'>{_esc(t.get('code'))}</td>"
            f"<td style='padding:6px 8px;text-align:right;font-family:Menlo,Consolas,monospace'>{sc}</td></tr>"
        )
    if not top_rows:
        top_rows = "<tr><td colspan='4' style='padding:8px;color:#656d76'>无 TOP</td></tr>"

    check_rows = ""
    for c in checks:
        ok = bool(c.get("ok"))
        mark = "✓" if ok else "✗"
        color = "#1a7f37" if ok else "#cf222e"
        check_rows += (
            f"<tr><td style='padding:6px 8px;color:{color};font-weight:700'>{mark}</td>"
            f"<td style='padding:6px 8px'>{_esc(c.get('name') or c.get('id'))}</td>"
            f"<td style='padding:6px 8px;color:#656d76'>{_esc(c.get('detail') or c.get('msg') or '')}</td></tr>"
        )
    if not check_rows:
        check_rows = "<tr><td colspan='3' style='padding:8px;color:#656d76'>无检查清单</td></tr>"

    # shadow monitor mini
    mon_rows_html = ""
    for r in (mon.get("rows") or [])[:6]:
        mon_rows_html += (
            f"<tr>"
            f"<td style='padding:6px 8px'>{_esc(r.get('name'))}</td>"
            f"<td style='padding:6px 8px;text-align:right'>{_esc(r.get('target_exposure'))}</td>"
            f"<td style='padding:6px 8px'>{_esc(r.get('holdings'))}</td>"
            f"<td style='padding:6px 8px;text-align:right'>{_money(r.get('total_value'))}</td>"
            f"<td style='padding:6px 8px;text-align:right'>{_esc(r.get('return_pct'))}%</td>"
            f"</tr>"
        )
    if not mon_rows_html:
        mon_rows_html = "<tr><td colspan='5' style='padding:8px;color:#656d76'>无监控行</td></tr>"

    # recent actions (compact)
    act_rows = ""
    act_path = ROOT / "output" / "action_history.jsonl"
    acts: list[dict] = []
    if act_path.exists():
        try:
            for line in act_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        acts.append(rec)
        except Exception:
            acts = []
    # unique by day, last 5
    by_day: dict[str, dict] = {}
    for a in acts:
        d = str(a.get("date") or "")[:10]
        if d:
            by_day[d] = a
    for d in sorted(by_day.keys())[-5:][::-1]:
        a = by_day[d]
        act_rows += (
            f"<tr><td style='padding:6px 8px;color:#656d76'>{_esc(d)}</td>"
            f"<td style='padding:6px 8px'>{_esc(a.get('action'))}</td></tr>"
        )
    if not act_rows:
        act_rows = "<tr><td colspan='2' style='padding:8px;color:#656d76'>暂无历史</td></tr>"

    pages_link = ""
    if pages_url:
        pages_link = (
            f"<p style='margin:12px 0 0'>"
            f"<a href='{_esc(pages_url)}' style='color:#0969da'>打开 GitHub Pages 面板</a></p>"
        )

    extra_sections = ""
    try:
        _eta_txt = _eta_summary_line()
        if _eta_txt:
            extra_sections += (
                f"<div style='margin-top:12px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px;background:#fff8c5'>"
                f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>能不能当真看收益</div>"
                f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(_eta_txt)}</pre></div>"
            )
    except Exception:
        pass
    if append_alerts:
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-weight:700;margin-bottom:6px'>告警</div>"
            f"<div style='color:#656d76;font-size:13px'>error={err_n} · warn={warn_n}</div>"
            f"<pre style='white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:10px;"
            f"border-radius:8px;margin:8px 0 0'>{_esc(_alerts_block())}</pre></div>"
        )
    if append_shadow:
        try:
            sh_txt = _shadow_block(shadow_names)
        except Exception as e:
            sh_txt = f"影子摘要失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-weight:700;margin-bottom:6px'>影子仓位</div>"
            f"<pre style='white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:10px;"
            f"border-radius:8px;margin:8px 0 0'>{_esc(sh_txt)}</pre></div>"
        )
    if append_compare:
        try:
            cmp_txt = _compare_block()
        except Exception as e:
            cmp_txt = f"主线对照失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-weight:700;margin-bottom:6px'>主线对照</div>"
            f"<pre style='white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:10px;"
            f"border-radius:8px;margin:8px 0 0'>{_esc(cmp_txt)}</pre></div>"
        )
    if append_live:
        try:
            live_txt = _live_block()
        except Exception as e:
            live_txt = f"LIVE 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-weight:700;margin-bottom:6px'>主线 LIVE 收益</div>"
            f"<pre style='white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:10px;"
            f"border-radius:8px;margin:8px 0 0'>{_esc(live_txt)}</pre></div>"
        )
    if append_today:
        try:
            today_txt = _today_block()
        except Exception as e:
            today_txt = f"今日速览失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-weight:700;margin-bottom:6px'>今日速览</div>"
            f"<pre style='white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:10px;"
            f"border-radius:8px;margin:8px 0 0'>{_esc(today_txt)}</pre></div>"
        )
    if append_asof:
        try:
            asof_txt = _asof_block()
        except Exception as e:
            asof_txt = f"asof 取证失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>行情取证 (asof)</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(asof_txt)}</pre></div>"
        )

    if append_yield:
        try:
            yield_txt = _yield_block()
        except Exception as e:
            yield_txt = f"yield 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>真实有效收益</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(yield_txt)}</pre></div>"
        )

    if append_brief:
        try:
            brief_txt = _brief_block()
        except Exception as e:
            brief_txt = f"brief 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>三合一速览 (brief)</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(brief_txt)}</pre></div>"
        )

    if append_data:
        try:
            data_txt = _data_block()
        except Exception as e:
            data_txt = f"data 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>行情状态 (data)</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(data_txt)}</pre></div>"
        )

    if append_next:
        try:
            next_txt = _next_block()
        except Exception as e:
            next_txt = f"next 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>一键下一步 (next)</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(next_txt)}</pre></div>"
        )

    if append_pull:
        try:
            pull_txt = _pull_block()
        except Exception as e:
            pull_txt = f"pull 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>行情强刷 (pull)</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(pull_txt)}</pre></div>"
        )

    if append_go:
        try:
            go_txt = _go_block()
        except Exception as e:
            go_txt = f"go 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>一键闭环 (go)</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(go_txt)}</pre></div>"
        )

    if append_ready:
        try:
            ready_txt = _ready_block()
        except Exception as e:
            ready_txt = f"ready 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>可判性 (ready)</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(ready_txt)}</pre></div>"
        )

    if append_digest:
        try:
            digest_txt = _digest_block()
        except Exception as e:
            digest_txt = f"digest 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>DIGEST 摘要</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(digest_txt)}</pre></div>"
        )

    if append_eod:
        try:
            eod_txt = _eod_block()
        except Exception as e:
            eod_txt = f"eod 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>EOD 收盘闭环</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(eod_txt)}</pre></div>"
        )

    if append_progress:
        try:
            progress_txt = _progress_block()
        except Exception as e:
            progress_txt = f"progress 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>统计进度</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(progress_txt)}</pre></div>"
        )

    if append_pulse:
        try:
            pulse_txt = _pulse_block()
        except Exception as e:
            pulse_txt = f"pulse 失败: {e}"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-size:12px;font-weight:600;color:#24292f;margin-bottom:6px'>现在怎么办</div>"
            f"<pre style='margin:0;white-space:pre-wrap;font-size:12px;line-height:1.45'>{_esc(pulse_txt)}</pre></div>"
        )

    if append_status:
        st_path = _ensure_status_txt()
        st_txt = st_path.read_text(encoding="utf-8") if st_path.exists() else "无状态产物"
        extra_sections += (
            f"<div style='margin-top:16px;padding:12px 14px;border:1px solid #d0d7de;border-radius:10px'>"
            f"<div style='font-weight:700;margin-bottom:6px'>研究状态</div>"
            f"<pre style='white-space:pre-wrap;font-size:12px;background:#f6f8fa;padding:10px;"
            f"border-radius:8px;margin:8px 0 0'>{_esc(st_txt)}</pre></div>"
        )

    sh_action = _esc(shadow.get("action") or "—")
    ret = (latest or {}).get("return_pct")
    tv = (latest or {}).get("total_value")
    # 研究影子 实盘段/相对大盘 (日更有效收益)
    sh_state = shadow.get("state") if isinstance(shadow.get("state"), dict) else {}
    sh_live = sh_state.get("live") if isinstance(sh_state.get("live"), dict) else {}
    if not sh_live and isinstance(shadow.get("live"), dict):
        sh_live = shadow.get("live") or {}
    sh_live_pct = sh_live.get("return_pct")
    if sh_live_pct is None:
        sh_live_pct = shadow.get("live_return_pct")
    sh_xs = shadow.get("live_excess_pct")
    sh_bench_ret = shadow.get("bench_return_pct")
    sh_days = None
    sh_thin = False
    # 优先 latest.signal_live, 再 shadow_live.json
    try:
        sl = (latest or {}).get("signal_live") if isinstance(latest, dict) else None
        if isinstance(sl, dict):
            if sh_live_pct is None:
                sh_live_pct = sl.get("live_return_pct")
            if sh_xs is None:
                sh_xs = sl.get("live_excess_pct")
            if sh_bench_ret is None:
                sh_bench_ret = sl.get("bench_return_pct")
            sh_days = sl.get("days_live")
            if sh_days is None:
                sh_days = sl.get("live_n_rets")
            sh_thin = bool(
                sl.get("thin_live")
                or (sh_days is not None and int(sh_days) < 5)
            )
    except Exception:
        pass
    if sh_days is None or sh_xs is None or sh_live_pct is None or not sh_thin:
        try:
            live_path = ROOT / "output" / "risk_audit" / "shadow_live.json"
            if live_path.exists():
                live_rows = json.loads(live_path.read_text(encoding="utf-8"))
                if isinstance(live_rows, list):
                    for rr in live_rows:
                        if rr.get("signal") or rr.get("is_signal_default"):
                            if sh_live_pct is None:
                                sh_live_pct = rr.get("live_return_pct")
                            if sh_xs is None:
                                sh_xs = rr.get("live_excess_pct")
                            if sh_bench_ret is None:
                                sh_bench_ret = rr.get("bench_return_pct")
                            if sh_days is None:
                                sh_days = rr.get("days_live")
                                if sh_days is None:
                                    sh_days = rr.get("live_n_rets")
                            if not sh_thin:
                                sh_thin = bool(
                                    rr.get("thin_live")
                                    or (sh_days is not None and int(sh_days) < 5)
                                )
                            break
        except Exception:
            pass
    try:
        sh_live_s = f"{float(sh_live_pct):+.2f}%" if sh_live_pct is not None else "—"
    except Exception:
        sh_live_s = "—"
    if sh_thin:
        sh_live_s = f"{sh_live_s} 样本还少"
    try:
        sh_xs_s = f"{float(sh_xs):+.2f}%" if sh_xs is not None else "—"
    except Exception:
        sh_xs_s = "—"
    try:
        sh_bench_s = (
            f"{float(sh_bench_ret):+.2f}%" if sh_bench_ret is not None else "—"
        )
    except Exception:
        sh_bench_s = "—"
    if sh_days is not None:
        sh_bench_s = f"{sh_bench_s} · {_zh_sample_days(sh_days)}"

    # latest 过旧 / 行情滞后提示
    stale_note = ""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from etf_rotation.calendar_util import resolve_trading_day

        td = resolve_trading_day()
        td_date = str((td or {}).get("date") or "")[:10]
        ld = str((latest or {}).get("time") or when or "")[:10]
        asof = str((latest or {}).get("market_asof") or (td or {}).get("data_asof") or "")[:10]
        bits = []
        if (td or {}).get("data_lag") or (asof and td_date and asof < td_date):
            bits.append(
                f"<b>⚠ 行情还没更新完</b> · 行情截至 {_esc(asof)} · 今天交易日 {_esc(td_date)} "
                f"(净值/收益以行情截止日为准) · <code>./etf asof</code>"
            )
        if (td or {}).get("is_trading_day") and td_date and ld and ld < td_date:
            bits.append(
                f"<b>⚠ latest 过旧</b> · 信号日 {_esc(ld)} &lt; 交易日 {_esc(td_date)} · "
                f"请跑 <code>./etf refresh</code>"
            )
        if bits:
            stale_note = (
                f"<div style='margin:12px 18px 0;padding:10px 12px;border-radius:10px;"
                f"background:#fff8c5;border:1px solid #d4a72c;color:#7d4e00;font-size:13px'>"
                + "<br>".join(bits)
                + "</div>"
            )
    except Exception:
        stale_note = ""

    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF轮动日报</title></head>
<body style="margin:0;padding:0;background:#f6f8fa;color:#1f2328;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.5">
  <div style="max-width:680px;margin:0 auto;padding:16px">
    <div style="background:#ffffff;border:1px solid #d0d7de;border-radius:14px;overflow:hidden">
      <div style="padding:16px 18px;border-bottom:1px solid #d0d7de;background:{action_bg}">
        <div style="font-size:12px;color:#656d76">ETF 轮动日报 · {_esc(cfg)} · {when} · 行情截至 {_esc((latest or {}).get("market_asof") or "—")}</div>
        <div style="font-size:22px;font-weight:750;margin-top:4px">{action}</div>
        <div style="margin-top:8px">
          <span style="display:inline-block;padding:2px 8px;border-radius:999px;background:{trend_bg};
           color:{trend_fg};font-size:12px;font-weight:600;margin-right:6px">{trend}</span>
          <span style="display:inline-block;padding:2px 8px;border-radius:999px;background:#ddf4ff;
           color:#0969da;font-size:12px;font-weight:600;margin-right:6px">{'生产冻结' if frozen else '未冻结'}</span>
          <span style="display:inline-block;padding:2px 8px;border-radius:999px;background:#f6f8fa;
           color:#656d76;font-size:12px;font-weight:600">告警 E{err_n}/W{warn_n}</span>
        </div>
        {pages_link}
      </div>
      {stale_note}

      <div style="padding:14px 18px;display:flex;flex-wrap:wrap;gap:10px">
        <div style="flex:1 1 140px;background:#f6f8fa;border-radius:10px;padding:10px 12px">
          <div style="font-size:12px;color:#656d76">持仓</div>
          <div style="font-weight:700">{_esc(hold_name)}</div>
        </div>
        <div style="flex:1 1 140px;background:#f6f8fa;border-radius:10px;padding:10px 12px">
          <div style="font-size:12px;color:#656d76">研究影子·实盘段</div>
          <div style="font-weight:700">{_esc(sh_live_s)}</div>
          <div style="font-size:11px;color:#656d76;margin-top:2px">已统计{_esc(sh_days if sh_days is not None else '—')} · 截至 {_esc((latest or {}).get('market_asof') or '—')}{(' · 行情未更新' if (latest or {}).get('signal_live', {}).get('data_lag') or (sh_days == 0) else '')}</div>
        </div>
        <div style="flex:1 1 140px;background:#f6f8fa;border-radius:10px;padding:10px 12px">
          <div style="font-size:12px;color:#656d76">相对大盘</div>
          <div style="font-weight:700">{_esc(sh_xs_s)}</div>
          <div style="font-size:11px;color:#656d76;margin-top:2px">基准 {_esc(sh_bench_s)}</div>
        </div>
        <div style="flex:1 1 140px;background:#f6f8fa;border-radius:10px;padding:10px 12px">
          <div style="font-size:12px;color:#656d76">影子建议</div>
          <div style="font-weight:700;font-size:13px">{sh_action}</div>
        </div>
      </div>

      <div style="padding:0 18px 14px">
        <div style="font-weight:700;margin:4px 0 6px">原因</div>
        <ul style="margin:0;padding-left:18px">{reason_lis}</ul>
      </div>

      <div style="padding:0 18px 16px">
        <div style="font-weight:700;margin-bottom:6px">近几日动作</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #d0d7de">
          <thead>
            <tr style="background:#f6f8fa;color:#656d76;text-align:left">
              <th style="padding:6px 8px">日期</th>
              <th style="padding:6px 8px">动作</th>
            </tr>
          </thead>
          <tbody>{act_rows}</tbody>
        </table>
      </div>

      <div style="padding:0 18px 16px">
        <div style="font-weight:700;margin-bottom:6px">决策检查</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #d0d7de;border-radius:8px">
          <tbody>{check_rows}</tbody>
        </table>
      </div>

      <div style="padding:0 18px 16px">
        <div style="font-weight:700;margin-bottom:6px">主策略 TOP</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #d0d7de">
          <thead>
            <tr style="background:#f6f8fa;color:#656d76;text-align:left">
              <th style="padding:6px 8px">#</th><th style="padding:6px 8px">名称</th>
              <th style="padding:6px 8px">代码</th><th style="padding:6px 8px;text-align:right">得分</th>
            </tr>
          </thead>
          <tbody>{top_rows}</tbody>
        </table>
      </div>

      <div style="padding:0 18px 16px">
        <div style="font-weight:700;margin-bottom:6px">影子监控</div>
        <table style="width:100%;border-collapse:collapse;font-size:12px;border:1px solid #d0d7de">
          <thead>
            <tr style="background:#f6f8fa;color:#656d76;text-align:left">
              <th style="padding:6px 8px">影子</th>
              <th style="padding:6px 8px;text-align:right">暴露</th>
              <th style="padding:6px 8px">持仓</th>
              <th style="padding:6px 8px;text-align:right">资产</th>
              <th style="padding:6px 8px;text-align:right">收益</th>
            </tr>
          </thead>
          <tbody>{mon_rows_html}</tbody>
        </table>
      </div>

      <div style="padding:0 18px 18px">{extra_sections}
        <details style="margin-top:14px">
          <summary style="cursor:pointer;color:#656d76;font-size:13px">展开纯文本全文</summary>
          <pre style="white-space:pre-wrap;font-size:11px;background:#f6f8fa;padding:10px;
           border-radius:8px;border:1px solid #d0d7de">{_esc(plain)}</pre>
        </details>
        <p style="margin:14px 0 0;color:#656d76;font-size:12px">非投资建议 · 生产 c01 冻结 · 研究影子不交易</p>
      </div>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="", help="正文文件, 默认 latest.txt")
    ap.add_argument("--append-shadow", action="store_true", help="附加影子仓位摘要")
    ap.add_argument("--shadow-names", default="", help="影子名逗号分隔")
    ap.add_argument("--append-alerts", action="store_true", help="附加 monitor 告警")
    ap.add_argument(
        "--append-status",
        action="store_true",
        help="附加 research_status 面板文本",
    )
    ap.add_argument(
        "--append-compare",
        action="store_true",
        help="附加主线影子对照 (shadow_compare)",
    )
    ap.add_argument(
        "--append-live",
        action="store_true",
        help="附加主线 LIVE 收益 (shadow_live)",
    )
    ap.add_argument(
        "--append-today",
        action="store_true",
        help="附加今日一页速览 (today)",
    )
    ap.add_argument(
        "--append-asof",
        action="store_true",
        help="附加行情取证 (asof)",
    )
    ap.add_argument(
        "--append-yield",
        action="store_true",
        help="附加真实有效收益",
    )
    ap.add_argument(
        "--append-brief",
        action="store_true",
        help="附加三合一速览 (brief)",
    )
    ap.add_argument(
        "--append-data",
        action="store_true",
        help="附加行情状态 (data)",
    )
    ap.add_argument(
        "--append-next",
        action="store_true",
        help="附加一键下一步 (next)",
    )
    ap.add_argument(
        "--append-pull",
        action="store_true",
        help="附加行情强刷 (pull)",
    )
    ap.add_argument(
        "--append-go",
        action="store_true",
        help="附加一键闭环 (go)",
    )
    ap.add_argument(
        "--append-ready",
        action="store_true",
        help="附加有效收益可判性 (ready)",
    )
    ap.add_argument(
        "--append-digest",
        action="store_true",
        help="附加人读摘要 (digest)",
    )
    ap.add_argument(
        "--append-eod",
        action="store_true",
        help="附加收盘闭环 (eod)",
    )
    ap.add_argument(
        "--append-progress",
        action="store_true",
        help="附加可判性轨迹 (progress)",
    )
    ap.add_argument(
        "--append-pulse",
        action="store_true",
        help="附加脉搏 (pulse)",
    )
    ap.add_argument(
        "--html",
        action="store_true",
        default=True,
        help="发送 HTML+纯文本 (默认开)",
    )
    ap.add_argument(
        "--plain-only",
        action="store_true",
        help="仅纯文本 (兼容旧客户端)",
    )
    ap.add_argument("--dry-print", action="store_true", help="只打印不发信")
    args = ap.parse_args()

    body_path = Path(args.file) if args.file else LATEST_TXT
    if not body_path.exists():
        print(f"ERROR: 找不到信号文件 {body_path}")
        sys.exit(1)
    raw = body_path.read_text(encoding="utf-8")
    latest = _load_json(LATEST_JSON)

    plain = _plain_body(
        raw,
        append_shadow=args.append_shadow,
        shadow_names=args.shadow_names,
        append_alerts=args.append_alerts,
        append_status=args.append_status,
        append_compare=args.append_compare,
        append_live=args.append_live,
        append_today=args.append_today,
        append_asof=args.append_asof,
        append_yield=args.append_yield,
        append_brief=args.append_brief,
        append_data=args.append_data,
        append_next=args.append_next,
        append_pull=args.append_pull,
        append_go=args.append_go,
        append_ready=args.append_ready,
        append_digest=args.append_digest,
        append_eod=args.append_eod,
        append_progress=args.append_progress,
        append_pulse=args.append_pulse,
    )
    action = _extract_action(plain, latest)
    err_n = _alert_error_n()
    alert_tag = f" | ALERT×{err_n}" if err_n else ""
    live_tag = ""
    try:
        src = None
        if isinstance(latest, dict) and isinstance(latest.get("signal_live"), dict):
            src = latest.get("signal_live")
        if src is None:
            live_path = ROOT / "output" / "risk_audit" / "shadow_live.json"
            if live_path.exists():
                live_rows = json.loads(live_path.read_text(encoding="utf-8"))
                if isinstance(live_rows, list):
                    for rr in live_rows:
                        if not isinstance(rr, dict):
                            continue
                        if (
                            rr.get("signal")
                            or rr.get("is_signal_default")
                            or rr.get("name") == SIGNAL_SHADOW
                        ):
                            src = rr
                            break
        if src is not None:
            lr = src.get("live_return_pct")
            xs = src.get("live_excess_pct")
            dl = src.get("days_live")
            if dl is None:
                dl = src.get("live_n_rets")
            thin = src.get("thin_live")
            if thin is None and dl is not None:
                try:
                    thin = int(dl) < 5
                except Exception:
                    thin = False
            parts = []
            if lr is not None:
                parts.append(f"实盘段{float(lr):+.1f}%")
            if xs is not None:
                parts.append(f"相对大盘{float(xs):+.1f}%")
            if thin:
                parts.append("样本还少")
            if dl is not None:
                try:
                    parts.append(_zh_sample_days(dl, thin))
                except Exception:
                    pass
            # DATA_LAG 标进主题, 避免扫一眼误判 xs=0
            lag = src.get("data_lag")
            asof = src.get("market_asof") or (latest or {}).get("market_asof")
            if lag is None:
                try:
                    from etf_rotation.calendar_util import resolve_trading_day
                    td0 = resolve_trading_day()
                    lag = bool(td0.get("data_lag"))
                    asof = asof or td0.get("data_asof")
                except Exception:
                    lag = False
            if lag:
                parts.append("行情未更新")
                parts.append("等行情")
            elif asof:
                parts.append(f"截至{asof}")
            # data_status.decision 补充
            try:
                dsp = ROOT / "output" / "risk_audit" / "data_status.json"
                if dsp.exists():
                    dd = json.loads(dsp.read_text(encoding="utf-8"))
                    if isinstance(dd, dict) and dd.get("decision") in ("wait_data", "refresh"):
                        tag = "等行情" if dd.get("decision") == "wait_data" else "信号过旧"
                        if tag not in parts:
                            parts.append(tag)
            except Exception:
                pass
            try:
                pp = ROOT / "output" / "risk_audit" / "pull.json"
                if pp.exists():
                    pj = json.loads(pp.read_text(encoding="utf-8"))
                    if isinstance(pj, dict) and pj.get("advanced") is False and (
                        (pj.get("after") or {}).get("data_lag")
                    ):
                        if "NO_ADVANCE" not in parts:
                            parts.append("行情没推进")
            except Exception:
                pass
            try:
                rp = ROOT / "output" / "risk_audit" / "ready.json"
                if rp.exists():
                    rj = json.loads(rp.read_text(encoding="utf-8"))
                    if isinstance(rj, dict) and rj.get("level"):
                        lv = str(rj.get("level"))
                        if lv in ("NOT_READY", "WAIT_DATA", "THIN", "READY", "PARTIAL"):
                            zh = _zh_level(lv)
                            if zh not in parts:
                                parts.append(zh)
            except Exception:
                pass
            try:
                rp = ROOT / "output" / "risk_audit" / "ready.json"
                if rp.exists():
                    rj = json.loads(rp.read_text(encoding="utf-8"))
                    if isinstance(rj, dict) and rj.get("days_to_ready") is not None:
                        try:
                            dtr = int(rj.get("days_to_ready"))
                            tag = f"还要约{dtr}天"
                            if tag not in parts and dtr > 0:
                                parts.append(tag)
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                pp = ROOT / "output" / "risk_audit" / "pulse.json"
                if pp.exists():
                    pj = json.loads(pp.read_text(encoding="utf-8"))
                    if isinstance(pj, dict):
                        lv = str(pj.get("level") or "")
                        if lv and lv != "READY" and "PULSE" not in parts:
                            # 提醒先看脉搏, 勿把 样本还少/LAG 当有效收益
                            parts.append("先看脉搏")
            except Exception:
                pass
            if parts:
                _map = {
                    "WAIT_DATA": "等行情",
                    "DATA_LAG": "行情未更新",
                    "NO_ADVANCE": "行情没推进",
                    "NOT_READY": "暂不可判",
                    "THIN": "样本还少",
                    "READY": "可当真收益看",
                    "STALE": "信号过旧",
                    "PULSE": "先看脉搏",
                }
                parts = [_map.get(str(p), str(p)) for p in parts]
                # 去重保序
                seen = set()
                parts2 = []
                for p in parts:
                    if p in seen:
                        continue
                    seen.add(p)
                    parts2.append(p)
                live_tag = " | " + " ".join(parts2)
    except Exception:
        live_tag = ""
    subject_prefix = os.environ.get("MAIL_SUBJECT", "ETF轮动信号")
    subject = f"{subject_prefix} | {action}{live_tag}{alert_tag}"
    pages_url = os.environ.get("MAIL_PAGES_URL", "").strip()

    use_html = bool(args.html) and not args.plain_only
    html_body = ""
    if use_html:
        html_body = _html_report(
            latest,
            plain,
            append_shadow=args.append_shadow,
            shadow_names=args.shadow_names,
            append_alerts=args.append_alerts,
            append_status=args.append_status,
            append_compare=args.append_compare,
            append_live=args.append_live,
            append_today=args.append_today,
        append_asof=args.append_asof,
        append_yield=args.append_yield,
        append_brief=args.append_brief,
        append_data=args.append_data,
        append_next=args.append_next,
        append_pull=args.append_pull,
        append_go=args.append_go,
        append_ready=args.append_ready,
        append_digest=args.append_digest,
        append_eod=args.append_eod,
        append_progress=args.append_progress,
        append_pulse=args.append_pulse,
            pages_url=pages_url,
        )

    if args.dry_print:
        print(subject)
        if use_html:
            print("--- HTML preview (truncated) ---")
            print(html_body[:2500])
            print("--- plain ---")
        print(plain)
        print("--- dry-print: 未发送 ---")
        # 也写预览文件, 方便本地打开
        prev = OUTPUT_DIR / "email_preview.html"
        if use_html:
            prev.write_text(html_body, encoding="utf-8")
            print(f"WROTE {prev}")
        return

    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    mail_to = os.environ.get("MAIL_TO", "").strip()
    mail_from = os.environ.get("MAIL_FROM", user).strip()
    port = int(os.environ.get("SMTP_PORT", "465"))

    if not all([host, user, password, mail_to]):
        print("ERROR: 需要 SMTP_HOST / SMTP_USER / SMTP_PASSWORD / MAIL_TO")
        sys.exit(1)

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]
    if use_html:
        msg: MIMEMultipart | MIMEText = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = mail_from
        msg["To"] = mail_to
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(plain, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = mail_from
        msg["To"] = mail_to

    context = ssl.create_default_context()
    print(f"发送邮件 → {recipients} via {host}:{port} html={use_html}")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
            s.login(user, password)
            s.sendmail(mail_from, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=context)
            s.login(user, password)
            s.sendmail(mail_from, recipients, msg.as_string())
    print("✅ 邮件已发送 (" + ("HTML+纯文本" if use_html else "纯文本") + ")")


if __name__ == "__main__":
    main()
