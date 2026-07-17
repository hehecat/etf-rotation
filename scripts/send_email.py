#!/usr/bin/env python3
"""把 latest 信号发邮件 (纯文本 + 手机友好 HTML).

环境变量 (GitHub Secrets 同名):
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD
  MAIL_FROM / MAIL_TO / MAIL_SUBJECT
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

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import LATEST_JSON, LATEST_TXT  # noqa: E402


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def extract_action(body: str, summary: dict | None) -> str:
    if summary and summary.get("action"):
        return str(summary["action"]).strip()
    for line in body.splitlines():
        if "今日动作" in line:
            return line.split("今日动作")[-1].strip(" :：")
    return "日报"


def action_tone(action: str) -> tuple[str, str]:
    """返回 (bg, fg) 用于动作卡片."""
    a = action or ""
    if "买入" in a or "🟢" in a:
        return "#0d3b1e", "#3fb950"
    if "卖出" in a or "🔴" in a:
        return "#3d1214", "#f85149"
    if "持有" in a or "🟡" in a:
        return "#3d2e00", "#d29922"
    return "#21262d", "#8b949e"  # 空仓/观望


def build_html(body: str, summary: dict | None) -> str:
    action = extract_action(body, summary)
    bg, fg = action_tone(action)

    # 从正文抽沪深300行
    market_line = ""
    for line in body.splitlines():
        if "沪深300" in line:
            market_line = line.strip().lstrip("📊 ").strip()
            break

    s = summary or {}
    checks = s.get("checks") or []
    top3 = s.get("top3") or []
    reasons = s.get("reasons") or []
    shadow = s.get("shadow") or {}
    holding = s.get("holding")

    check_rows = []
    for c in checks:
        ok = c.get("ok")
        mark = "✓" if ok else "✗"
        color = "#3fb950" if ok else "#f85149"
        check_rows.append(
            f'<tr><td style="padding:8px 0;border-bottom:1px solid #30363d;width:28px;'
            f'color:{color};font-weight:700;font-size:16px;">{esc(mark)}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid #30363d;color:#e6edf3;">'
            f'<div style="font-weight:600;">{esc(c.get("name"))}</div>'
            f'<div style="color:#8b949e;font-size:13px;margin-top:2px;">{esc(c.get("detail"))}</div>'
            f"</td></tr>"
        )
    checks_html = "".join(check_rows) or (
        '<tr><td style="color:#8b949e;">无检查项</td></tr>'
    )

    top_rows = []
    for i, t in enumerate(top3, 1):
        top_rows.append(
            f'<tr><td style="padding:6px 8px 6px 0;color:#8b949e;">{i}</td>'
            f'<td style="padding:6px 0;color:#e6edf3;font-weight:600;">{esc(t.get("name"))}</td>'
            f'<td style="padding:6px 0;color:#8b949e;font-size:12px;">{esc(t.get("code"))}</td>'
            f'<td style="padding:6px 0;text-align:right;color:#58a6ff;">'
            f'{esc(t.get("score"))}</td></tr>'
        )
    top_html = "".join(top_rows) or (
        '<tr><td style="color:#8b949e;">暂无</td></tr>'
    )

    reason_html = "".join(
        f'<li style="margin:0 0 6px 0;color:#c9d1d9;">{esc(r)}</li>' for r in reasons
    ) or '<li style="color:#8b949e;">—</li>'

    if holding:
        hold_html = (
            f'<div style="color:#e6edf3;font-size:15px;font-weight:600;">'
            f'{esc(holding.get("name"))} '
            f'<span style="color:#8b949e;font-weight:400;font-size:13px;">'
            f'({esc(holding.get("code"))})</span></div>'
            f'<div style="color:#8b949e;font-size:13px;margin-top:4px;">'
            f'成本 {esc(holding.get("buy_price"))} · '
            f'{esc(holding.get("shares"))} 股 · '
            f'买入日 {esc(holding.get("buy_date"))}</div>'
        )
    else:
        hold_html = '<div style="color:#8b949e;">空仓</div>'

    market_ok = s.get("market_ok")
    mkt_badge = (
        '<span style="background:#0d3b1e;color:#3fb950;padding:2px 8px;border-radius:10px;'
        'font-size:12px;">趋势向上</span>'
        if market_ok
        else '<span style="background:#3d1214;color:#f85149;padding:2px 8px;border-radius:10px;'
        'font-size:12px;">趋势向下/空仓</span>'
    )

    # 完整正文: 保留换行, 等宽, 可横向滚动
    pre_body = esc(body)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF轮动信号</title>
</head>
<body style="margin:0;padding:0;background:#0d1117;color:#e6edf3;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC',
'Hiragino Sans GB','Microsoft YaHei',sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="background:#0d1117;padding:16px 10px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="max-width:480px;width:100%;">

  <!-- 标题 -->
  <tr><td style="padding:4px 4px 12px 4px;">
    <div style="color:#8b949e;font-size:12px;letter-spacing:0.5px;">ETF 跨行业轮动</div>
    <div style="color:#e6edf3;font-size:18px;font-weight:700;margin-top:2px;">
      {esc(s.get("config") or "C01")} · 日报
    </div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px;">{esc(s.get("time") or "")}</div>
  </td></tr>

  <!-- 今日动作大卡片 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:{bg};border:1px solid {fg}55;border-radius:12px;padding:16px 18px;">
      <div style="color:{fg};font-size:11px;font-weight:600;letter-spacing:1px;
        text-transform:uppercase;opacity:0.9;">今日动作</div>
      <div style="color:{fg};font-size:22px;font-weight:700;margin-top:6px;line-height:1.3;">
        {esc(action)}
      </div>
    </div>
  </td></tr>

  <!-- 原因 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 16px;">
      <div style="color:#8b949e;font-size:12px;margin-bottom:8px;">决策原因</div>
      <ul style="margin:0;padding-left:18px;">{reason_html}</ul>
    </div>
  </td></tr>

  <!-- 市场 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 16px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
        <span style="color:#8b949e;font-size:12px;">大盘</span>
        {mkt_badge}
      </div>
      <div style="color:#c9d1d9;font-size:13px;line-height:1.5;">{esc(market_line) or "—"}</div>
      <div style="color:#8b949e;font-size:12px;margin-top:8px;">
        市场宽度 {(float(s.get("breadth") or 0)*100):.0f}% ·
        调仓还需 {esc(s.get("days_to_rebalance"))} 日
      </div>
    </div>
  </td></tr>

  <!-- 检查清单 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 16px;">
      <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">检查清单</div>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
        {checks_html}
      </table>
    </div>
  </td></tr>

  <!-- 持仓 / 账户 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 16px;">
      <div style="color:#8b949e;font-size:12px;margin-bottom:8px;">模拟账户</div>
      {hold_html}
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:12px;">
        <tr>
          <td style="padding:6px 0;color:#8b949e;font-size:13px;">总资产</td>
          <td style="padding:6px 0;text-align:right;color:#e6edf3;font-weight:600;">
            {esc(s.get("total_value"))}
          </td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#8b949e;font-size:13px;">总收益</td>
          <td style="padding:6px 0;text-align:right;color:#e6edf3;font-weight:600;">
            {esc(s.get("return_pct"))}%
          </td>
        </tr>
      </table>
    </div>
  </td></tr>

  <!-- TOP3 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 16px;">
      <div style="color:#8b949e;font-size:12px;margin-bottom:6px;">效率 TOP3</div>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
        {top_html}
      </table>
    </div>
  </td></tr>

  <!-- 影子 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 16px;">
      <div style="color:#8b949e;font-size:12px;margin-bottom:6px;">影子对照 (不交易)</div>
      <div style="color:#c9d1d9;font-size:14px;">{esc(shadow.get("action") or "—")}</div>
    </div>
  </td></tr>

  <!-- 完整原文 -->
  <tr><td style="padding:0 0 12px 0;">
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px 12px;">
      <div style="color:#8b949e;font-size:12px;margin:0 4px 10px 4px;">完整原文</div>
      <pre style="margin:0;padding:10px;background:#0d1117;border-radius:8px;
        color:#c9d1d9;font-size:11px;line-height:1.45;white-space:pre-wrap;
        word-wrap:break-word;overflow-wrap:anywhere;
        font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">{pre_body}</pre>
    </div>
  </td></tr>

  <tr><td style="padding:8px 4px 20px 4px;color:#484f58;font-size:11px;text-align:center;line-height:1.5;">
    不构成投资建议 · C01 冻结策略 · 由 GitHub Actions 自动发送
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>
"""


def build_plain(body: str, summary: dict | None) -> str:
    """手机纯文本也尽量段落化, 少用宽表."""
    action = extract_action(body, summary)
    lines = [
        "【ETF轮动信号】",
        f"动作: {action}",
        "",
    ]
    if summary:
        lines.append(f"时间: {summary.get('time')}")
        lines.append(f"配置: {summary.get('config')}")
        lines.append(f"大盘OK: {summary.get('market_ok')}")
        lines.append(f"总资产: {summary.get('total_value')}  收益: {summary.get('return_pct')}%")
        if summary.get("reasons"):
            lines.append("")
            lines.append("原因:")
            for r in summary["reasons"]:
                lines.append(f"  · {r}")
        if summary.get("checks"):
            lines.append("")
            lines.append("检查:")
            for c in summary["checks"]:
                mark = "✓" if c.get("ok") else "✗"
                lines.append(f"  [{mark}] {c.get('name')}: {c.get('detail')}")
        lines.append("")
        lines.append("—— 完整原文 ——")
        lines.append("")
    # 原文: 去掉过长的分隔线, 方便手机
    cleaned = []
    for line in body.splitlines():
        if re.fullmatch(r"[=─-]{10,}", line.strip()):
            cleaned.append("--------")
        else:
            cleaned.append(line)
    lines.extend(cleaned)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="", help="正文文件, 默认 latest.txt")
    ap.add_argument("--preview", action="store_true", help="只写 HTML 预览, 不发信")
    args = ap.parse_args()

    body_path = Path(args.file) if args.file else LATEST_TXT
    if not body_path.exists():
        print(f"ERROR: 找不到信号文件 {body_path}")
        sys.exit(1)
    body = body_path.read_text(encoding="utf-8")

    summary = None
    if LATEST_JSON.exists():
        try:
            summary = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        except Exception:
            summary = None

    action = extract_action(body, summary)
    subject_prefix = os.environ.get("MAIL_SUBJECT", "ETF轮动信号")
    subject = f"{subject_prefix} | {action}"

    plain = build_plain(body, summary)
    html_body = build_html(body, summary)

    if args.preview:
        out = ROOT / "output" / "email_preview.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_body, encoding="utf-8")
        print(f"预览: {out}")
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

    # multipart/alternative: 先 plain 后 html, 客户端优先显示 html
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]
    context = ssl.create_default_context()
    print(f"发送邮件(HTML) → {recipients} via {host}:{port}")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
            s.login(user, password)
            s.sendmail(mail_from, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=context)
            s.login(user, password)
            s.sendmail(mail_from, recipients, msg.as_string())
    print("✅ 邮件已发送 (HTML + 纯文本)")


if __name__ == "__main__":
    main()
