# ETF 跨行业轮动 (C01 冻结版)

模块化工程。生产策略 **C01_效15d** 已冻结，至少跑满 3 个月再调参。

## 目录

```
etf-rotation/
├── config/           # 策略与ETF池 (JSON)
│   ├── c01.json      # 生产主策略 (frozen)
│   ├── c13_shadow.json
│   └── pool.json
├── etf_rotation/     # Python 包
│   ├── data.py       # 取数
│   ├── factors.py    # 因子/打分
│   ├── backtest.py   # 回测引擎
│   ├── portfolio.py  # 模拟账户
│   ├── signal.py     # 信号主流程
│   ├── report.py     # 报告输出
│   ├── config.py
│   └── paths.py
├── scripts/
│   ├── run_signal.py     # 生产信号
│   ├── run_backtest.py   # 只读回测验证
│   └── run_daily.sh      # cron 入口
├── archive/          # 旧脚本与历史实验
└── docs/
```

运行时输出 (便于人工查看):

```
~/桌面/ETF轮动信号/
├── latest.txt        # 固定入口
├── latest.json
├── 模拟仓位.json
├── etf信号_*.txt
├── 迭代记录.md
└── logs/
```

## 日常使用

```bash
# 生产信号
python3 /home/abc/etf-rotation/scripts/run_signal.py

# 只看不改仓
python3 /home/abc/etf-rotation/scripts/run_signal.py --dry-run

# 回测验证 (不改生产参数)
python3 /home/abc/etf-rotation/scripts/run_backtest.py --strategy c01 --count 500
# 长样本
python3 /home/abc/etf-rotation/scripts/run_backtest.py --strategy c01 --count 2500 --adjust qfq
```

固定阅读入口: `~/桌面/ETF轮动信号/latest.txt`

## Cron (本机, 可选)

```
30 15 * * 1-5 /home/abc/etf-rotation/scripts/run_daily.sh
```

## 云端定时 + 邮件 (推荐)

用 **GitHub Actions** 每个交易日 15:35(北京) 跑信号并发邮件, 本机可关 cron。

详见: [docs/云端定时与邮件.md](docs/云端定时与邮件.md)

摘要:
1. 推送到 GitHub
2. 配置 Secrets: `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_TO`
3. Actions 里手动 Run workflow 试一次
4. (可选) 开 Pages 看网页版 `latest`

## 升级约定

| 改什么 | 放哪 |
|--------|------|
| 调参 | `config/*.json` (C01 冻结期间勿改 weights/rb) |
| 加/减 ETF | `config/pool.json` |
| 信号逻辑 | `etf_rotation/signal.py` / `portfolio.py` |
| 因子 | `etf_rotation/factors.py` |
| 回测 | `etf_rotation/backtest.py` |
| 取数源 | `etf_rotation/data.py` |

## 策略摘要 (C01)

- 得分: `0.6 * z(eff) + 0.4 * z(mtf)`
- 调仓: 15 自然日
- 趋势: 沪深300 > MA20
- 止损: -8% · 迟滞 20% · 最小持仓 5 日
- 过热: 20 日涨幅 > 30% 跳过
