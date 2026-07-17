#!/usr/bin/env python3
"""把 latest 信号发纯文本邮件.

环境变量 (GitHub Secrets 同名):
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD
  MAIL_FROM / MAIL_TO / MAIL_SUBJECT
"""
from __future__ import annotations

import argparse
import os
import re
import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from etf_rotation.paths import LATEST_TXT  # noqa: E402


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
        sys.exit(1)

    body_path = Path(args.file) if args.file else LATEST_TXT
    if not body_path.exists():
        print(f"ERROR: 找不到信号文件 {body_path}")
        sys.exit(1)
    body = body_path.read_text(encoding="utf-8")

    # 长分隔线缩短, 减少手机折行观感
    lines = []
    for line in body.splitlines():
        if re.fullmatch(r"[=─-]{10,}", line.strip()):
            lines.append("--------")
        else:
            lines.append(line)
    body = "\n".join(lines)

    action = "日报"
    for line in body.splitlines():
        if "今日动作" in line:
            action = line.split("今日动作")[-1].strip(" :：")
            break
    subject_prefix = os.environ.get("MAIL_SUBJECT", "ETF轮动信号")
    subject = f"{subject_prefix} | {action}"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to

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
    print("✅ 邮件已发送 (纯文本)")


if __name__ == "__main__":
    main()
