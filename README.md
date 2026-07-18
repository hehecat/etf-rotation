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
- 调仓: 15 **交易日** (与回测对齐; 旧版误用自然日)
- 趋势: 沪深300 > MA20
- 止损: -8% · 迟滞 20% · 最小持仓 5 交易日
- 过热: 20 日涨幅 > 30% 跳过
- 回测默认成交: T+1 开盘 (`next_open`)

## 池子

| 文件 | 说明 |
|------|------|
| `config/pool.json` | **生产默认 = 去重版** (一赛道一只, ~52) |
| `config/pool_full.json` | 原多票池备份 (~72, 同主题可重复) |
| `config/pool_dedup.json` | 与生产池同内容 (对照别名) |
| `config/pool_quality.json` | 报告优选工具版 (实验, 未作生产) |

## 对照实验 A/B/C/D 是什么

`compare_improvements.py` 里的标签 (不是四套独立上线策略):

| 标签 | 策略参数 | 池子 | 成交假设 | 含义 |
|------|----------|------|----------|------|
| **A** | C01 冻结权重 | 原多票 `pool_full` | **收盘成交** | 旧乐观回测上界 |
| **B** | C01 冻结权重 | 原多票 `pool_full` | **T+1 开盘** | 可执行基线 (同参数) |
| **C** | C01 冻结权重 | **去重池** | T+1 开盘 | 只换池、不改因子 |
| **D** | `c01_improved` | 去重池 | T+1 开盘 | 方向效率+绝对动量 |
| **E** | `c01_improved` | 原多票 | T+1 开盘 | 改进因子+旧池 |

要点:
- **A vs B**: 只改成交假设 (close → next_open)
- **B vs C**: 只改池子 (多票 → 一赛道一只)
- **C vs D**: 只改因子规则 (无方向 eff → 方向 eff + 要求 m20>0)
- 生产当前: **C01 参数 + 去重池 + empty_free_entry** (空仓可立即再入)
- 对照 `c01_park.json`: 行业弱于300时停靠底仓 (实验; 长样本总收益通常低于纯C01)

### 为何有的年份跑输300?

长样本(~8年) **总体仍大幅跑赢** 510300; 分年跑输常见于:
1. **趋势过滤空仓** — 300慢牛/结构牛时现金拖累
2. **单票行业轮动** — 宽基涨、行业主题不涨或轮动踩错
3. **旧逻辑空仓锁15日窗** — 已默认 `empty_free_entry=true` 修复

**不要**为「每年都赢300」去硬叠 prefer_bench: 对照显示会砍掉小牛段超额。
可接受: 熊市少亏、结构年可能跑输、主题年大赢。

```bash
# A/B/C/D 对照
python3 scripts/compare_improvements.py --count 500
# 池子对照 (含 --align)
python3 scripts/compare_pools.py --count 500 --align
# 生产信号 (默认去重池)
python3 scripts/run_signal.py --dry-run
# 回测
python3 scripts/run_backtest.py --strategy c01 --fill next_open
```
