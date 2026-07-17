#!/usr/bin/env python3
"""把 latest 信号发邮件.

环境变量 (GitHub Secrets 同名):
  SMTP_HOST      如 smtp.qq.com / smtp.gmail.com / smtp.163.com
  SMTP_PORT      默认 465
  SMTP_USER      登录邮箱
  SMTP_PASSWORD  授权码(不是登录密码)
  MAIL_FROM      发件人, 默认=SMTP_USER
  MAIL_TO        收件人, 多个用逗号分隔
  MAIL_SUBJECT   可选主题前缀

用法:
  python3 scripts/send_email.py
  python3 scripts/send_email.py --file output/latest.txt
"""
from __future__ import annotations

import argparse
import os
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="", help="正文文件, 默认 latest.txt")
    args = ap.parse_args()

    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    mail_to = os.environ.get("MAIL_TO", "").strip()
    mail_from = os.environ.get("MAIL_FROM", user).strip()
    port = int(os.environ.get("SMTP_PORT", "465"))

    if not all([host, user, password, mail_to]):
        print("ERROR: 需要 SMTP_HOST / SMTP_USER / SMTP_PASSWORD / MAIL_TO")
        print("当前缺失:", [k for k, v in {
            "SMTP_HOST": host, "SMTP_USER": user,
            "SMTP_PASSWORD": password, "MAIL_TO": mail_to,
        }.items() if not v])
        sys.exit(1)

    body_path = Path(args.file) if args.file else LATEST_TXT
    if not body_path.exists():
        print(f"ERROR: 找不到信号文件 {body_path}")
        sys.exit(1)
    body = body_path.read_text(encoding="utf-8")

    # 主题: 从正文抽今日动作
    subject_prefix = os.environ.get("MAIL_SUBJECT", "ETF轮动信号")
    action = "日报"
    for line in body.splitlines():
        if "今日动作" in line:
            action = line.split("今日动作")[-1].strip(" :：")
            break
    subject = f"{subject_prefix} | {action}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # 附带 json 摘要(若有)
    if LATEST_JSON.exists():
        try:
            import json
            summary = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
            brief = (
                f"\n\n--- JSON摘要 ---\n"
                f"时间: {summary.get('time')}\n"
                f"动作: {summary.get('action')}\n"
                f"大盘OK: {summary.get('market_ok')}\n"
                f"总资产: {summary.get('total_value')}\n"
                f"收益: {summary.get('return_pct')}%\n"
            )
            # 重建纯文本
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = mail_from
            msg["To"] = mail_to
            msg.attach(MIMEText(body + brief, "plain", "utf-8"))
        except Exception:
            pass

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]
    context = ssl.create_default_context()
    print(f"发送邮件 → {recipients} via {host}:{port}")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
            s.login(user, password)
            s.sendmail(mail_from, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=context)
            s.login(user, password)
            s.sendmail(mail_from, recipients, msg.as_string())
    print("✅ 邮件已发送")


if __name__ == "__main__":
    main()
